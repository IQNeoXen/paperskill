#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

try:
    import requests
except ImportError:  # pragma: no cover - runtime guard
    print("Missing dependency: requests. Install with `pip install requests`.", file=sys.stderr)
    sys.exit(1)


class ApiError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def require_env() -> tuple[str, str]:
    base_url = os.getenv("PAPERLESS_URL", "").strip()
    token = os.getenv("PAPERLESS_TOKEN", "").strip()
    if not base_url or not token:
        eprint("Missing PAPERLESS_URL or PAPERLESS_TOKEN environment variables.")
        eprint("Set them before running, e.g. export PAPERLESS_URL=... and PAPERLESS_TOKEN=...")
        sys.exit(2)
    return base_url.rstrip("/"), token


def build_url(base_url: str, path: str) -> str:
    return urljoin(base_url + "/", path.lstrip("/"))


def request_json(session: requests.Session, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        resp = session.get(url, params=params, timeout=30)
    except requests.exceptions.RequestException as exc:
        raise ApiError(-1, f"Network error: {exc}") from exc

    if resp.status_code in (401, 403):
        raise ApiError(resp.status_code, "Authentication failed. Check PAPERLESS_TOKEN.")

    if resp.status_code >= 400:
        try:
            payload = resp.json()
            message = payload.get("detail") or payload.get("error") or json.dumps(payload)
        except ValueError:
            message = resp.text.strip() or f"HTTP {resp.status_code}"
        raise ApiError(resp.status_code, message)

    try:
        return resp.json()
    except ValueError as exc:
        raise ApiError(resp.status_code, "Invalid JSON response from server.") from exc


def iter_pages(session: requests.Session, url: str, params: Optional[Dict[str, Any]] = None) -> Iterable[Dict[str, Any]]:
    data = request_json(session, url, params=params)
    while True:
        yield data
        next_url = data.get("next")
        if not next_url:
            break
        if isinstance(next_url, str) and not next_url.startswith("http"):
            next_url = urljoin(url, next_url)
        data = request_json(session, next_url, params=None)


def iter_documents_with_fallback(
    session: requests.Session,
    docs_url: str,
    params: Dict[str, Any],
) -> Iterable[Dict[str, Any]]:
    try:
        for page in iter_pages(session, docs_url, params=params):
            for doc in page.get("results", []):
                yield doc
        return
    except ApiError as exc:
        if exc.status_code == 400 and params:
            reduced = {k: v for k, v in params.items() if k == "query"}
            if reduced != params:
                eprint("Server rejected one or more filters. Falling back to client-side filtering.")
                for page in iter_pages(session, docs_url, params=reduced):
                    for doc in page.get("results", []):
                        yield doc
                return
        raise


def parse_date_arg(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected YYYY-MM-DD") from exc


def parse_doc_date(value: Optional[str]) -> Optional[dt.date]:
    if not value:
        return None
    date_part = value.split("T")[0]
    try:
        return dt.date.fromisoformat(date_part)
    except ValueError:
        return None


def fetch_id_name_map(session: requests.Session, base_url: str, endpoint: str) -> Dict[int, str]:
    url = build_url(base_url, f"/api/{endpoint}/")
    mapping: Dict[int, str] = {}
    for page in iter_pages(session, url, params=None):
        for item in page.get("results", []):
            if "id" in item and "name" in item:
                mapping[int(item["id"])] = str(item["name"])
    return mapping


def extract_tag_names(doc: Dict[str, Any], tag_map: Optional[Dict[int, str]]) -> List[str]:
    tags = doc.get("tags") or []
    if tags and isinstance(tags[0], dict):
        return [str(tag.get("name", "")) for tag in tags if tag.get("name")]
    if tag_map is None:
        return [str(tag) for tag in tags]
    names: List[str] = []
    for tag_id in tags:
        try:
            tag_id_int = int(tag_id)
        except (TypeError, ValueError):
            continue
        if tag_id_int in tag_map:
            names.append(tag_map[tag_id_int])
    return names


def extract_name(doc: Dict[str, Any], id_field: str, name_field: str, mapping: Optional[Dict[int, str]]) -> str:
    if name_field in doc and doc.get(name_field):
        return str(doc.get(name_field))
    if mapping is None:
        return str(doc.get(id_field, ""))
    try:
        value = int(doc.get(id_field))
    except (TypeError, ValueError):
        return ""
    return mapping.get(value, "")


def normalize(value: str) -> str:
    return value.strip().lower()


def format_table(headers: List[str], rows: List[List[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    lines = []
    lines.append("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Search Paperless-ngx documents")
    parser.add_argument("--query", help="Full-text search string")
    parser.add_argument("--tag", action="append", default=[], help="Filter by tag name (repeatable)")
    parser.add_argument("--type", dest="doc_type", help="Filter by document_type name")
    parser.add_argument("--correspondent", help="Filter by correspondent name")
    parser.add_argument("--after", type=parse_date_arg, help="Created after YYYY-MM-DD")
    parser.add_argument("--before", type=parse_date_arg, help="Created before YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default 10)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.limit <= 0:
        eprint("--limit must be greater than 0")
        return 2

    base_url, token = require_env()
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Token {token}",
        "Accept": "application/json",
    })

    params: Dict[str, Any] = {}
    if args.query:
        params["query"] = args.query
    if args.tag:
        params["tags__name__in"] = ",".join(args.tag)
    if args.doc_type:
        params["document_type__name"] = args.doc_type
    if args.correspondent:
        params["correspondent__name"] = args.correspondent
    if args.after:
        params["created__date__gte"] = args.after.isoformat()
    if args.before:
        params["created__date__lte"] = args.before.isoformat()

    docs_url = build_url(base_url, "/api/documents/")

    tag_map: Optional[Dict[int, str]] = None
    corr_map: Optional[Dict[int, str]] = None
    type_map: Optional[Dict[int, str]] = None

    def ensure_tag_map() -> Dict[int, str]:
        nonlocal tag_map
        if tag_map is None:
            tag_map = fetch_id_name_map(session, base_url, "tags")
        return tag_map

    def ensure_corr_map() -> Dict[int, str]:
        nonlocal corr_map
        if corr_map is None:
            corr_map = fetch_id_name_map(session, base_url, "correspondents")
        return corr_map

    def ensure_type_map() -> Dict[int, str]:
        nonlocal type_map
        if type_map is None:
            type_map = fetch_id_name_map(session, base_url, "document_types")
        return type_map

    def passes_filters(doc: Dict[str, Any]) -> bool:
        if args.tag:
            tags_value = doc.get("tags") or []
            needs_map = not (tags_value and isinstance(tags_value[0], dict))
            tag_names = [normalize(name) for name in extract_tag_names(doc, ensure_tag_map() if needs_map else None)]
            for tag in args.tag:
                if normalize(tag) not in tag_names:
                    return False
        if args.doc_type:
            doc_type_map = None if doc.get("document_type_name") else ensure_type_map()
            doc_type = normalize(extract_name(doc, "document_type", "document_type_name", doc_type_map))
            if doc_type != normalize(args.doc_type):
                return False
        if args.correspondent:
            corr_map = None if doc.get("correspondent_name") else ensure_corr_map()
            corr = normalize(extract_name(doc, "correspondent", "correspondent_name", corr_map))
            if corr != normalize(args.correspondent):
                return False
        if args.after or args.before:
            created_date = parse_doc_date(doc.get("created"))
            if created_date is None:
                return False
            if args.after and created_date < args.after:
                return False
            if args.before and created_date > args.before:
                return False
        return True

    results: List[Dict[str, Any]] = []

    try:
        for doc in iter_documents_with_fallback(session, docs_url, params=params):
            if not passes_filters(doc):
                continue
            results.append(doc)
            if len(results) >= args.limit:
                break
    except ApiError as exc:
        eprint(f"Error: {exc.message}")
        return 1

    if not results:
        print("No documents found.")
        return 0

    output_rows: List[List[str]] = []
    output_json: List[Dict[str, Any]] = []

    for doc in results:
        tags_value = doc.get("tags") or []
        needs_map = not (tags_value and isinstance(tags_value[0], dict))
        tag_names = extract_tag_names(doc, ensure_tag_map() if needs_map else None)
        corr_map = None if doc.get("correspondent_name") else ensure_corr_map()
        type_map = None if doc.get("document_type_name") else ensure_type_map()
        correspondent = extract_name(doc, "correspondent", "correspondent_name", corr_map)
        doc_type = extract_name(doc, "document_type", "document_type_name", type_map)
        created_raw = str(doc.get("created", ""))
        created_display = created_raw.split("T")[0] if created_raw else ""
        title = str(doc.get("title") or doc.get("original_file_name") or "")

        output_rows.append([
            str(doc.get("id", "")),
            title,
            created_display,
            correspondent,
            ", ".join(tag_names),
            doc_type,
        ])

        output_json.append({
            "id": doc.get("id"),
            "title": title,
            "created": created_raw,
            "correspondent": correspondent,
            "tags": tag_names,
            "document_type": doc_type,
        })

    if args.json:
        print(json.dumps(output_json, indent=2))
        return 0

    headers = ["id", "title", "created", "correspondent", "tags", "document_type"]
    print(format_table(headers, output_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
