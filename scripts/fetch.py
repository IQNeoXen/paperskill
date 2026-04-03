#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional
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
    if not base_url.startswith("https://"):
        eprint("Error: PAPERLESS_URL must use HTTPS (got: " + base_url + ").")
        eprint("Set PAPERLESS_URL to an https:// address to protect your token and data.")
        sys.exit(1)
    return base_url.rstrip("/"), token


def build_url(base_url: str, path: str) -> str:
    return urljoin(base_url + "/", path.lstrip("/"))


def request_json(session: requests.Session, url: str) -> Dict[str, Any]:
    try:
        resp = session.get(url, timeout=30)
    except requests.exceptions.RequestException as exc:
        raise ApiError(-1, f"Network error: {exc}") from exc

    if resp.status_code in (401, 403):
        raise ApiError(resp.status_code, "Authentication failed. Check PAPERLESS_TOKEN.")

    if resp.status_code >= 400:
        try:
            payload = resp.json()
            message = payload.get("detail") or payload.get("error") or json.dumps(payload)
        except ValueError:
            safe_text = re.sub(r'[^\x20-\x7E]', '', resp.text)[:200]
            message = safe_text.strip() or f"HTTP {resp.status_code}"
        raise ApiError(resp.status_code, message)

    try:
        return resp.json()
    except ValueError as exc:
        raise ApiError(resp.status_code, "Invalid JSON response from server.") from exc


def parse_filename_from_header(content_disposition: str) -> Optional[str]:
    if not content_disposition:
        return None
    match = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip('"')
    match = re.search(r"filename=\"?([^\";]+)\"?", content_disposition, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def determine_output_path(out_arg: Optional[str], filename: str) -> Path:
    if out_arg:
        out_path = Path(out_arg)
        if out_path.exists() and out_path.is_dir():
            return out_path / filename
        if str(out_path).endswith(("/", "\\")):
            return out_path / filename
        return out_path
    return Path.cwd() / filename


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a Paperless-ngx document")
    parser.add_argument("--id", type=int, required=True, help="Document ID to fetch")
    parser.add_argument("--out", help="Output file path (default: current dir, filename from API)")
    parser.add_argument("--text", action="store_true", help="Extract text content to stdout instead of saving file")
    args = parser.parse_args()

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

    if args.text:
        content = doc.get("content")
        if content is None:
            eprint("Document content is not available.")
            return 1
        print("--- BEGIN DOCUMENT CONTENT (UNTRUSTED) ---")
        print(content)
        print("--- END DOCUMENT CONTENT ---")
        return 0

    download_url = build_url(base_url, f"/api/documents/{args.id}/download/")
    try:
        resp = session.get(download_url, stream=True, timeout=60)
    except requests.exceptions.RequestException as exc:
        eprint(f"Network error: {exc}")
        return 1

    if resp.status_code in (401, 403):
        eprint("Authentication failed. Check PAPERLESS_TOKEN.")
        return 1

    if resp.status_code >= 400:
        eprint(f"Download failed: HTTP {resp.status_code}")
        return 1

    filename = parse_filename_from_header(resp.headers.get("Content-Disposition", ""))
    if not filename:
        filename = doc.get("original_file_name") or doc.get("filename") or f"document_{args.id}"
    # Strip any directory components from the server-returned filename to prevent path traversal.
    filename = Path(filename).name or f"document_{args.id}"

    out_path = determine_output_path(args.out, filename)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with out_path.open("wb") as handle:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    handle.write(chunk)
    except OSError as exc:
        eprint(f"Failed to write file: {exc}")
        return 1

    print(f"Saved to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
