# MCP server tutorial (`mcp_server.py`)

The **MCP server** exposes Voyager's data-fetching and DB-querying capabilities
as [Model Context Protocol](https://modelcontextprotocol.io) tools, so AI
assistants (Claude Desktop, IDE agents, etc.) can pull and inspect financial data
through tool calls instead of the CLI.

It uses [FastMCP](https://github.com/jlowin/fastmcp) and reuses the same service
layer as the local CLI, so the tools behave like the CLI commands.

---

## 1. Prerequisites

- Python 3.12+ with `requirements.txt` installed. `fastmcp` is required:
  ```bash
  pip install -r requirements.txt        # includes fastmcp
  # or just: pip install fastmcp
  ```
- MongoDB reachable via env vars for anything that touches the DB:
  ```bash
  export MONGODB_URL=mongodb://root:example@localhost:27017/
  export MONGODB_DB_NAME=voyager
  ```
  (Or use a profile-style env file, e.g. `profiles/atlas.env`.)

---

## 2. Invocation & options

```
Usage: python mcp_server.py [--transport {stdio,http}] [--host HOST] [--port PORT]
```

| Flag | Env var | Default | Description |
| --- | --- | --- | --- |
| `--transport` | `MCP_TRANSPORT` | `stdio` | `stdio` for local tools, `http` for Streamable HTTP. |
| `--host` | `MCP_HOST` | `127.0.0.1` | Host to bind for the `http` transport. |
| `--port` | `MCP_PORT` | `8002` | Port to bind for the `http` transport. |

Help: `python mcp_server.py --help`

```bash
# stdio transport (default) — used by MCP clients that spawn the process
python mcp_server.py

# HTTP (Streamable HTTP) server
python mcp_server.py --transport http --port 8002
python mcp_server.py --transport http --host 0.0.0.0 --port 9000

# via env vars
MCP_TRANSPORT=http MCP_PORT=8080 python mcp_server.py
```

With `http`, the MCP endpoint is at `http://<host>:<port>/mcp`.

---

## 3. Transports

### stdio (default)

The MCP client launches `python mcp_server.py` as a subprocess and speaks
JSON-RPC over stdin/stdout. Configure your client to start it:

```json
// e.g. Claude Desktop claude_desktop_config.json
{
  "mcpServers": {
    "voyager": {
      "command": "python",
      "args": ["/path/to/voyager/mcp_server.py"],
      "env": {
        "MONGODB_URL": "mongodb://root:example@localhost:27017/",
        "MONGODB_DB_NAME": "voyager"
      }
    }
  }
}
```

### http (Streamable HTTP)

Run the server, then point an HTTP-capable MCP client at
`http://127.0.0.1:8002/mcp`:

```bash
python mcp_server.py --transport http --port 8002
```

This is the transport to use for a shared/remote deployment (e.g. alongside the
API on Render).

---

## 4. What tools are exposed

The server registers **24 tools**, all built on the same service functions used
by the local CLI. Tool names equal the function names (list them with your MCP
client, e.g. by running the server and calling `tools/list`).

**Health / lists**
- `ping`, `version`
- `list_categories` — sources, countries, industries, sectors, indices
- `get_source_schema` — JSON schema for a data source

**Financials / metrics (from the DB)**
- `get_financials` — merged income + balance + cash-flow for the latest period
- `get_income_statements`
- `get_balance_sheets`
- `get_cash_flows`
- `get_financial_metrics` — computed ratios + live price/technicals

**Corporate data**
- `announcements`, `shareholdings`
- `pull_status` — record counts + availability breakdown

**Raw NSE scrapers / DB utilities (legacy)**
- `nse_financials_raw`, `nse_announcements`, `nse_announcements_search`,
  `nse_announcements_extract`, `nse_annual_reports_list`, `nse_annual_reports`,
  `nse_shareholdings_raw`, `nse_full_download`

**Web screeners**
- `screener_fetch`, `screener_screen`, `trendlyne_fetch`,
  `stockscans_fetch`, `marketsmithindia_fetch`

> ℹ️ The OCR/annual-report-text tools (`nse_process_annual_report`,
> `nse_annual_report_sections`) were **removed** when the OCR pipeline was
> dropped — see [`removed_tool_sources.md`](../removed_tool_sources.md).

Each tool's parameters mirror the CLI options (`symbol`, `country`, `source`,
`filing_type`, `consolidated`, `limit`, `save`, etc.).

---

## 5. Examples

- Ask an assistant connected via stdio: *"What are VBL's financial metrics for
  ttm?"* → it calls `get_financial_metrics(symbol="VBL", filing_type="ttm")`.
- *"Pull VBL quarterly financials"* → `nse_financials_raw(symbol="VBL")` /
  `nse_full_download(symbol="VBL")`.
- *"Show VBL's income statements, 8 periods"* → `get_income_statements(symbol="VBL", limit=8)`.
- *"What data is available for VBL?"* → `pull_status(symbol="VBL")`.

---

## 6. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ModuleNotFoundError: No module named 'fastmcp'` | `pip install fastmcp` (or `pip install -r requirements.txt`). |
| Tools fail with `ServerSelectionTimeoutError` | Check `MONGODB_URL` / `MONGODB_DB_NAME` and that MongoDB is up. |
| Client can't reach the HTTP server | Confirm `--host`/`--port`; use `0.0.0.0` if the client is on another host; endpoint is `/mcp`. |
| A financial tool returns partial data | Same as the CLI: some NSE endpoints 404 from certain networks; re-pull locally into Atlas for full coverage. |
