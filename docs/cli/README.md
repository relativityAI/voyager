# Voyager CLI tutorials

Voyager ships two command-line tools. Each has its own tutorial:

| Tool | What it does | Run it with | Tutorial |
| --- | --- | --- | --- |
| API CLI | Interactive explorer for a deployed Voyager API | `python cli.py ...` | below |
| Remote client | Python client library + Typer CLI for the deployed API | `python -m client ...` | [02-client-cli.md](02-client-cli.md) |

## API CLI

`cli.py` at the repo root reads your API key from `.env`, fetches the OpenAPI
spec, and lets you pick endpoints interactively, fill in args, and render
results with rich. Read-only — the API has no write endpoints.

```bash
python cli.py                              # interactive mode
python cli.py --url http://localhost:8001
```

## Which one should I use?

- **You want to query a deployed, read-only API** → either tool. The API CLI is
  the quick interactive explorer; the remote client is the programmatic one.
- **You want to ingest data** → ingestion runs via `src.ingest` (package) and
  the admin panel; the HTTP API is read-only.

## Quick cheat sheet

```bash
# API CLI — interactive exploration (needs VOYAGER_API_KEY in .env)
python cli.py

# Remote client — query the deployed API (needs base URL + API key)
export VOYAGER_BASE_URL=https://voyager-1hpq.onrender.com
export VOYAGER_API_KEY="vgr_..."
python -m client ping
python -m client metrics VBL --filing-type ttm
```
