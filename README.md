# paperless-ngx-skill

OpenClaw skill plus standalone CLI scripts for searching, fetching, and updating documents from a Paperless-ngx instance via its REST API.

## Requirements
- Python 3.10+
- requests (`pip install requests`)
- Paperless-ngx API token with document access

## Setup
1. Install dependencies: `pip install requests`.
2. Create a `config.env` file (or export environment variables) with the following values:

```env
PAPERLESS_URL=http://localhost:8000
PAPERLESS_TOKEN=your_api_token_here
```

3. Ensure `PAPERLESS_URL` and `PAPERLESS_TOKEN` are available in your environment before running the scripts.

## Quickstart
```bash
python scripts/search.py --query "tax form" --limit 5
python scripts/search.py --query "songani" --content --limit 5
python scripts/fetch.py --id 123 --text
python scripts/update_meta.py --id 123 --add-tag important
```

## Commands

### Search documents
```bash
python scripts/search.py --query "tax form" --tag receipts --after 2024-01-01 --limit 10
python scripts/search.py --correspondent "Acme Corp" --type "Invoice" --json
```

Supported filters:
- `--query` full-text search
- `--tag` tag name (repeatable)
- `--type` document type name
- `--correspondent` correspondent name
- `--after` created after (YYYY-MM-DD)
- `--before` created before (YYYY-MM-DD)
- `--content` include OCR text content in matching (slower; fetches document details)

Output:
- Default: human-readable table with columns `id`, `title`, `created`, `correspondent`, `tags`, `document_type`
- `--json`: machine-readable JSON array

### Fetch documents
```bash
python scripts/fetch.py --id 123
python scripts/fetch.py --id 123 --out ./downloads/
python scripts/fetch.py --id 123 --text
```

Behavior:
- Default: downloads the file from `/api/documents/{id}/download/`
- `--text`: prints the OCR/text content from `/api/documents/{id}/`

### Update metadata
```bash
python scripts/update_meta.py --id 123 --add-tag important --remove-tag inbox
python scripts/update_meta.py --id 123 --title "Q1 Invoice" --correspondent "Acme Corp"
```

Behavior:
- Resolves tag and correspondent names to IDs via `/api/tags/` and `/api/correspondents/`
- Sends a `PATCH` to `/api/documents/{id}/`

## Notes and troubleshooting
- Authentication uses `Authorization: Token {PAPERLESS_TOKEN}`.
- Pagination is handled automatically.
- If `--text` output is empty, OCR may still be processing. Reprocess in Paperless-ngx, then retry.
- A 401/403 error usually means the token is invalid or lacks access.
- Some Paperless-ngx setups may reject certain query parameters; the search script falls back to client-side filtering when needed.
- Content search (`--content`) scans document text via the detail endpoint and can be slow on large archives.
- Do not commit `config.env` (it contains secrets). Use `config.example.env` as the template to share.

## FAQ
**Q: Why is `--text` empty or incomplete?**  
A: OCR may still be running. Reprocess the document in Paperless-ngx and try again.

**Q: How do I reprocess a document?**  
A: Use the Paperless-ngx UI or call the reprocess endpoint (e.g. via `/api/documents/bulk_edit/` with method `reprocess`).

**Q: I get 401/403 errors. What should I check?**  
A: Verify that `PAPERLESS_TOKEN` is a valid API token with document permissions.

**Q: Search results look incomplete when using filters.**  
A: Some servers reject certain filters. The script will fall back to client-side filtering, but if your API blocks the query param entirely, try fewer filters.

## OpenClaw
This skill is designed for OpenClaw, but the scripts work standalone from the command line.
