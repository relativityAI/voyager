"""Two-stage discounted cash flow (DCF) valuation, computed from stored data.

Reuses ``financial_metrics`` (TTM OCF, CapEx, shares, price, growth) so
the model needs no new DB reads. Free cash flow is operating cash flow minus
CapEx when the filing provides it; otherwise it falls back to operating cash
flow alone and the output flags the proxy with a warning.
"""

from typing import Any, Dict, Optional

from ._common import InvalidRequestError, NotFoundError
from .metrics import _safe_div, _to_float, financial_metrics

RISK_FREE_RATE = 0.064  # India 10Y govt yield proxy; override via risk_free_rate
MARKET_PREMIUM = 0.06
TAX_RATE = 0.25
# Auto-derived growth (revenue growth) is capped: sustainable FCF growth
# cannot exceed nominal GDP + a tailwind. User-supplied growth is never capped.
MAX_AUTO_GROWTH = 0.12


def _default_discount_rate(beta: float) -> float:
    """Cost of equity via CAPM. Debt weighting skipped: cost-of-debt inputs
    (finance costs) are absent from the TTM metrics output."""
    return RISK_FREE_RATE + beta * MARKET_PREMIUM


async def dcf_valuation(
    symbol: str,
    source: str = "nse",
    *,
    growth_rate: Optional[float] = None,
    terminal_growth_rate: Optional[float] = None,
    discount_rate: Optional[float] = None,
    years: int = 5,
    beta: float = 1.0,
) -> Dict[str, Any]:
    if years < 1 or years > 20:
        raise InvalidRequestError("years must be between 1 and 20")

    metrics = await financial_metrics(symbol, None, source, True, "ttm")
    if not metrics:
        raise NotFoundError(f"No financial data found for {symbol}")

    fcf_per_share = _to_float(metrics.get("free_cash_flow_per_share"))
    current_price = _to_float(metrics.get("current_price"))
    g = growth_rate if growth_rate is not None else (
        (_to_float(metrics.get("revenue_growth")) or 0.0) / 100.0 or 0.10
    )
    tg = terminal_growth_rate if terminal_growth_rate is not None else 0.04
    r = (
        discount_rate
        if discount_rate is not None
        else _default_discount_rate(beta)
    )

    if fcf_per_share is None:
        raise NotFoundError(f"No free cash flow data available for {symbol}")
    if r <= tg:
        raise InvalidRequestError(
            "discount rate must be greater than terminal growth rate"
        )

    warnings = []
    auto_growth = growth_rate is None
    if auto_growth and g > MAX_AUTO_GROWTH:
        warnings.append(
            f"Growth rate capped from {g*100:.1f}% to {MAX_AUTO_GROWTH*100:.0f}% "
            "(auto-computed revenue growth; pass growth_rate to override)"
        )
        g = MAX_AUTO_GROWTH

    fcf_src = metrics.get("free_cash_flow_source")
    fcf_from_capex = fcf_src == "operating_cash_flow_minus_capex"
    if not fcf_from_capex:
        warnings.append(
            "FCF approximated as operating cash flow (CapEx absent from filings); "
            "intrinsic value is optimistic"
        )

    pv_explicit = 0.0
    for i in range(1, years + 1):
        cf = fcf_per_share * ((1 + g) ** i)
        pv_explicit += cf / ((1 + r) ** i)
    terminal_value = fcf_per_share * ((1 + g) ** years) * (1 + tg) / (r - tg)
    pv_terminal = terminal_value / ((1 + r) ** years)

    intrinsic_value = pv_explicit + pv_terminal
    margin_of_safety = (
        _safe_div(intrinsic_value - current_price, intrinsic_value) * 100
        if current_price is not None
        else None
    )

    model = (
        "two-stage FCFF (FCF = operating cash flow - CapEx)"
        if fcf_from_capex
        else "two-stage FCFF (FCF ~= operating cash flow, CapEx absent)"
    )

    return {
        "symbol": symbol,
        "source": source,
        "valuation": "dcf",
        "model": model,
        "current_price": current_price,
        "intrinsic_value_per_share": round(intrinsic_value, 2),
        "margin_of_safety_pct": margin_of_safety,
        "warnings": warnings,
        "assumptions": {
            "growth_rate": g,
            "terminal_growth_rate": tg,
            "discount_rate": r,
            "years": years,
            "beta": beta,
            "risk_free_rate": RISK_FREE_RATE,
            "market_premium": MARKET_PREMIUM,
            "fcf_per_share": fcf_per_share,
            "fcf_source": (
                "operating_cash_flow_minus_capex"
                if fcf_from_capex
                else "operating_cash_flow_capex_absent"
            ),
        },
    }


def _demo() -> None:
    """Smoke-test the DCF math with synthetic numbers, no DB required."""
    fcf = 10.0
    g, tg, r, years = 0.10, 0.04, 0.12, 2
    expected = sum(
        fcf * (1 + g) ** i / (1 + r) ** i for i in range(1, years + 1)
    ) + fcf * (1 + g) ** years * (1 + tg) / (r - tg) / (1 + r) ** years
    assert abs(expected - 144.87) < 0.01, expected
    print("DCF demo OK:", round(expected, 2))


if __name__ == "__main__":
    _demo()
