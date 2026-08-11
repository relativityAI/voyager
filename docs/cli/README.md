# Voyager CLI tutorials

Voyager ships three command-line tools. Each has its own tutorial:

| Tool | What it does | Run it with | Tutorial |
| --- | --- | --- | --- |
| Local CLI | Reads/writes MongoDB directly (dev + data pulling) | `python scripts/cli.py ...` | [01-local-cli.md](01-local-cli.md) |
| Remote client | Talks to a deployed Voyager API over HTTPS | `python -m client ...` | [02-client-cli.md](02-client-cli.md) |
| MCP server | Exposes Voyager tools to MCP clients (Claude, etc.) | `python mcp_server.py ...` | [03-mcp-server.md](03-mcp-server.md) |

## Which one should I use?

- **You want to pull data into a database** (local MongoDB or Atlas) → **Local CLI**. It runs the scrapers/parsers against the DB you point it at.
- **You want to query a deployed, read-only API** → **Remote client**. It only makes authenticated HTTP calls.
- **You want to build an AI assistant / IDE integration** over the same data → **MCP server**.

## Quick cheat sheet

```bash
# Local CLI — pull + inspect data (needs a MongoDB)
python scripts/cli.py pull VBL --filing-type quarterly
python scripts/cli.py pull-status VBL
python scripts/cli.py metrics VBL --filing-type ttm
python scripts/cli.py financials merged VBL

# Remote client — query the deployed API (needs base URL + API key)
export VOYAGER_BASE_URL=https://voyager-1hpq.onrender.com
export VOYAGER_API_KEY="vgr_..."
python -m client ping
python -m client metrics VBL --filing-type ttm
python -m client pull VBL --no-watch        # needs data:write scope

# MCP server — expose tools to an MCP client
python mcp_server.py --transport http --port 8002
```
