"""MCP tool playground: call Voyager MCP tools directly.

Tests the same service-layer functions the MCP server exposes, without
the JSON-RPC/stdio overhead. For protocol-level testing, use the MCP CLI.

ponytail: calls service functions directly, bypassing MCP protocol.
Upgrade path: spawn mcp_server.py as a subprocess and communicate via
stdio JSON-RPC if protocol-level testing is needed.
"""
import asyncio
import inspect

import streamlit as st

from admin_panel.state import sidebar
from mcp_server import mcp as mcp_server
from src.services import (
    financial_metrics,
    get_announcements,
    get_financials,
    get_news_stories,
    get_reddit,
    get_shareholdings,
    get_ticker_mentions,
    get_youtube_search,
    get_youtube_transcript,
    list_category,
    macro_breadth,
    macro_derivatives,
    macro_flows,
    macro_flows_fpi,
    macro_index_constituents,
    macro_index_history,
    macro_index_quote,
    macro_index_returns,
    macro_index_valuation,
    macro_indices,
    macro_market_valuation,
    macro_overview,
    macro_rates,
    macro_turnover,
    search_symbols,
)
from src.services.technical_report import build_technical_report

MCP_TOOLS = [
    search_symbols,
    list_category,
    get_financials,
    financial_metrics,
    get_announcements,
    get_shareholdings,
    macro_overview,
    macro_indices,
    macro_index_quote,
    macro_index_history,
    macro_index_valuation,
    macro_index_returns,
    macro_index_constituents,
    macro_market_valuation,
    macro_breadth,
    macro_flows,
    macro_flows_fpi,
    macro_turnover,
    macro_derivatives,
    macro_rates,
    get_news_stories,
    get_ticker_mentions,
    get_reddit,
    get_youtube_search,
    get_youtube_transcript,
    build_technical_report,
]

# Descriptions come from FastMCP (docstring → description + per-arg schema docs).
_tools = asyncio.run(mcp_server.list_tools())
_DESC = {t.name: (t.description or "") for t in _tools}
_SCHEMA = {t.name: (t.parameters or {}).get("properties", {}) for t in _tools}


def mcp() -> None:
    sidebar()
    st.subheader("MCP Tools")
    st.caption("Call Voyager MCP tools directly (bypasses JSON-RPC/stdio)")

    labels = [
        f"{func.__name__} — {_DESC.get(func.__name__, '').split(chr(10))[0]}"
        for func in MCP_TOOLS
    ]
    chosen = st.selectbox(
        "Tool", labels, key="inp_mcp_tool", label_visibility="collapsed"
    )
    index = labels.index(chosen)
    func = MCP_TOOLS[index]

    st.markdown(f"**`{func.__name__}`**")
    desc = _DESC.get(func.__name__, "")
    if desc:
        st.markdown(desc)

    sig = inspect.signature(func)
    props = _SCHEMA.get(func.__name__, {})
    kwargs = {}
    with st.form("mcp_form", border=False):
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            default = (
                param.default if param.default != inspect.Parameter.empty else None
            )
            required = param.default == inspect.Parameter.empty
            doc = props.get(pname, {}).get("description", None)

            if param.annotation == bool:
                val = st.checkbox(
                    pname,
                    value=bool(default) if default else False,
                    key=f"mcp_{func.__name__}_{pname}",
                    help=doc,
                )
            elif param.annotation == int:
                val = st.number_input(
                    pname,
                    value=int(default) if default else 0,
                    key=f"mcp_{func.__name__}_{pname}",
                    help=doc,
                )
            elif param.annotation == float:
                val = st.number_input(
                    pname,
                    value=float(default) if default else 0.0,
                    key=f"mcp_{func.__name__}_{pname}",
                    help=doc,
                )
            else:
                val = st.text_input(
                    pname,
                    value=str(default) if default else "",
                    key=f"mcp_{func.__name__}_{pname}",
                    placeholder="required" if required else "",
                    help=doc,
                )

            if val is not None and val != "":
                kwargs[pname] = val

        execute = st.form_submit_button(
            "▶️ Execute", type="primary", width="stretch"
        )

    if execute:
        with st.spinner("Calling..."):
            try:
                result = func(**kwargs)
                if asyncio.iscoroutine(result):
                    result = asyncio.run(result)
                st.json(result)
            except Exception as exc:
                st.error(f"{type(exc).__name__}: {exc}")
