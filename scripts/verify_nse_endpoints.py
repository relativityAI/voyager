#!/usr/bin/env python3
"""Verify NSE endpoint health — run from the project root.

Primes a cookie jar and hits each NSE endpoint once.  Reports:
  - HTTP status code
  - whether the response is valid JSON
  - record count (if the endpoint returns a list or dict with "data")

Usage:
    python scripts/verify_nse_endpoints.py [--symbol RELIANCE]

Keep SYMBOL as a real NSE equity symbol.
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Ensure the project root is on sys.path
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from src.scrapers.sources.nse import NSE_ENDPOINTS, NSE_MARKET_ENDPOINTS, build_nse_config
from src.scrapers.session import StealthSession


def _count_records(data):
    """Best-effort count of records in the response."""
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            return len(inner)
    return 0


def verify_endpoints(symbol: str = "RELIANCE") -> list:
    """Verify all known NSE endpoints and return a list of result dicts."""
    cfg = build_nse_config()
    session = StealthSession(cfg)
    session.prime(force=True)

    results = []
    all_endpoints = {**NSE_ENDPOINTS, **NSE_MARKET_ENDPOINTS}

    for key, url_template in sorted(all_endpoints.items()):
        entry = {"endpoint": key, "url": url_template[:80] + "..."}
        try:
            url = url_template.format(
                symbol=symbol,
                from_date="01-01-2025",
                to_date="31-12-2025",
            )
            entry["url"] = url[:80] + ("..." if len(url) > 80 else "")
            resp = session.request("GET", url, timeout=10)
            entry["status"] = resp.status_code

            ctype = resp.headers.get("content-type", "")
            if "json" in ctype:
                try:
                    data = resp.json()
                    entry["is_json"] = True
                    entry["records"] = _count_records(data)
                    entry["alive"] = entry["records"] > 0 or isinstance(data, dict)
                except Exception:
                    entry["is_json"] = False
                    entry["alive"] = False
            elif "html" in ctype:
                entry["is_json"] = False
                entry["alive"] = False
                entry["note"] = "HTML response (blocked/not-ready)"
            else:
                entry["is_json"] = False
                entry["alive"] = resp.status_code == 200

        except Exception as exc:
            entry["status"] = None
            entry["alive"] = False
            entry["note"] = str(exc)[:100]

        time.sleep(0.1)  # Be polite
        results.append(entry)

    return results


def main():
    parser = argparse.ArgumentParser(description="Verify NSE endpoint health")
    parser.add_argument("--symbol", default="RELIANCE", help="NSE equity symbol")
    args = parser.parse_args()

    results = verify_endpoints(args.symbol)

    # Print summary table
    print(f"\n{'Endpoint':<30} {'Status':>6} {'JSON':>5} {'Records':>8} {'Alive':>6}  Note")
    print("-" * 90)
    for r in results:
        status = r.get("status", "ERR")
        is_json = "Y" if r.get("is_json") else "N"
        records = r.get("records", "-")
        alive = "Y" if r.get("alive") else "N"
        note = r.get("note", "")
        print(f"{r['endpoint']:<30} {str(status):>6} {is_json:>5} {str(records):>8} {alive:>6}  {note}")

    alive = sum(1 for r in results if r.get("alive"))
    dead = sum(1 for r in results if not r.get("alive"))
    print(f"\nSummary: {alive} alive, {dead} dead out of {len(results)} endpoints")

    # Write full results to JSON
    out_path = Path(_root) / "data" / "nse_endpoint_health.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"Full results written to {out_path}")


if __name__ == "__main__":
    main()
