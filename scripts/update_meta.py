#!/usr/bin/env python3
import argparse
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


def request_json(session: requests.Session, url: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        resp = session.request(method, url, json=payload, timeout=30)
    except requests.exceptions.RequestException as exc:
        raise ApiError(-1, f"Network error: {exc}") from exc

    if resp.status_code in (401, 403):
        raise ApiError(resp.status_code, "Authentication failed. Check PAPERLESS_TOKEN.")

    if resp.status_code >= 400:
        try:
            data = resp.json()
            message = data.get("detail") or data.get("error") or json.dumps(data)
        except ValueError:
            message = resp.text.strip() or f"HTTP {resp.status_code}"
        raise ApiError(resp.status_code, message)

    try:
        return resp.json()
    except ValueError as exc:
        raise ApiError(resp.status_code, "Invalid JSON response from server.") from exc


def iter_pages(session: requests.Session, url: str) -> Iterable[Dict[str, Any]]:
    data = request_json(session, url)
    while True:
        yield data
        next_url = data.get("next")
        if not next_url:
            break
        if isinstance(next_url, str) and not next_url.startswith("http"):
            next_url = urljoin(url, next_url)
        data = request_json(session, next_url)


def fetch_name_map(session: requests.Session, base_url: str, endpoint: str) -> Dict[str, int]:
    url = build_url(base_url, f"/api/{endpoint}/")
    mapping: Dict[str, int] = {}
    for page in iter_pages(session, url):
        for item in page.get("results", []):
            name = item.get("name")
            if name is None:
                continue
            mapping[str(name).strip().lower()] = int(item["id"])
    return mapping


def resolve_name(mapping: Dict[str, int], label: str, value: str) -> int:
    key = value.strip().lower()
    if key not in mapping:
        raise ApiError(404, f"{label} not found: {value}")
    return mapping[key]


def extract_tag_ids(doc: Dict[str, Any]) -> List[int]:
    tags = doc.get("tags") or []
    ids: List[int] = []
    for tag in tags:
        if isinstance(tag, dict):
            if "id" in tag:
                ids.append(int(tag["id"]))
        else:
            try:
                ids.append(int(tag))
            except (TypeError, ValueError):
                continue
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Update Paperless-ngx document metadata")
    parser.add_argument("--id", type=int, required=True, help="Document ID")
    parser.add_argument("--add-tag", action="append", default=[], help="Tag name to add (repeatable)")
    parser.add_argument("--remove-tag", action="append", default=[], help="Tag name to remove (repeatable)")
    parser.add_argument("--title", help="New title")
    parser.add_argument("--correspondent", help="New correspondent name")
    args = parser.parse_args()

    if not (args.add_tag or args.remove_tag or args.title or args.correspondent):
        eprint("No updates specified. Provide at least one of --add-tag, --remove-tag, --title, --correspondent.")
        return 2

    base_url, token = require_env()
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Token {token}",
        "Accept": "application/json",
    })

    doc_url = build_url(base_url, f"/api/documents/{args.id}/")

    try:
        doc = request_json(session, doc_url)
    except ApiError as exc:
        eprint(f"Error: {exc.message}")
        return 1

    tag_map: Dict[str, int] = {}
    if args.add_tag or args.remove_tag:
        try:
            tag_map = fetch_name_map(session, base_url, "tags")
        except ApiError as exc:
            eprint(f"Error: {exc.message}")
            return 1

    correspondent_id: Optional[int] = None
    if args.correspondent:
        try:
            corr_map = fetch_name_map(session, base_url, "correspondents")
            correspondent_id = resolve_name(corr_map, "Correspondent", args.correspondent)
        except ApiError as exc:
            eprint(f"Error: {exc.message}")
            return 1

    existing_tags = set(extract_tag_ids(doc))
    if args.add_tag:
        for name in args.add_tag:
            try:
                existing_tags.add(resolve_name(tag_map, "Tag", name))
            except ApiError as exc:
                eprint(f"Error: {exc.message}")
                return 1
    if args.remove_tag:
        for name in args.remove_tag:
            try:
                existing_tags.discard(resolve_name(tag_map, "Tag", name))
            except ApiError as exc:
                eprint(f"Error: {exc.message}")
                return 1

    payload: Dict[str, Any] = {}
    if args.title:
        payload["title"] = args.title
    if args.correspondent:
        payload["correspondent"] = correspondent_id
    if args.add_tag or args.remove_tag:
        payload["tags"] = sorted(existing_tags)

    if not payload:
        eprint("No changes to apply.")
        return 2

    try:
        updated = request_json(session, doc_url, method="PATCH", payload=payload)
    except ApiError as exc:
        eprint(f"Error: {exc.message}")
        return 1

    print(f"Updated document {updated.get('id', args.id)}")
    if "title" in payload:
        print(f"Title: {updated.get('title')}")
    if "correspondent" in payload:
        print(f"Correspondent ID: {updated.get('correspondent')}")
    if "tags" in payload:
        print(f"Tags: {updated.get('tags')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
