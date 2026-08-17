import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import select

from src.db.engine import get_session_factory
from src.db.models import IncomeStatement, BalanceSheet, CashFlow

from ._common import InvalidRequestError, UnsupportedSourceError


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(str(v).replace(",", ""))
        return None if (f != f or abs(f) == float("inf")) else f
    except (ValueError, TypeError):
        return None


def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    r = a / b
    return None if (r != r or abs(r) == float("inf")) else r


def _pct(v: Optional[float]) -> Optional[float]:
    return round(v * 100, 4) if v is not None else None


def _ttm_window(
    records: list, field: str, start: int = 0, require_all: bool = True
) -> Optional[float]:
    vals = [_to_float(r.get(field)) for r in records[start : start + 4]]
    available = [v for v in vals if v is not None]
    if not available:
        return None
    if require_all and len(available) != 4:
        return None
    return sum(available)


def _find_record(records: list, ref_date: str, offset_months: int) -> Optional[dict]:
    try:
        ref = datetime.strptime(ref_date, "%Y-%m-%d")
        total = ref.month - offset_months
        ty = ref.year
        tm = total
        if total <= 0:
            tm = total + 12
            ty -= 1
        elif total > 12:
            tm = total - 12
            ty += 1
        for r in records:
            rd = r.get("period_end_date")
            if not rd:
                continue
            try:
                if isinstance(rd, str):
                    od = datetime.strptime(rd, "%Y-%m-%d")
                elif isinstance(rd, datetime):
                    od = rd
                else:
                    continue
                if od.year == ty and od.month == tm:
                    return r
            except ValueError:
                pass
    except ValueError:
        pass
    return None


async def _safe_market_fetch(func, symbol: str, source: str) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(func, symbol, source)
    except Exception as exc:
        logger.warning(f"Market data fetch failed for {symbol}: {exc}")
        return {}


async def financial_metrics(
    symbol: str,
    country: str = "in",
    source: str = "nse",
    consolidated: bool = True,
    filing_type: str = "quarterly",
) -> Dict[str, Any]:
    symbol = symbol.upper()
    source = source.upper()

    if country.lower() != "in" or source != "NSE":
        raise UnsupportedSourceError(
            f"Source '{source}' for country '{country}' is not yet supported"
        )
    if filing_type not in ("quarterly", "annual", "ttm"):
        raise InvalidRequestError("filing_type must be 'quarterly', 'annual', or 'ttm'")

    from src.tools.nse.technicals import fetch_price_info, fetch_technicals

    is_cons = consolidated

    income_docs: dict = {}
    balance_docs: dict = {}
    cashflow_docs: dict = {}

    db_ft = "quarterly" if filing_type == "ttm" else filing_type

    factory = get_session_factory()
    async with factory() as session:
        for model_class, dest in (
            (IncomeStatement, income_docs),
            (BalanceSheet, balance_docs),
            (CashFlow, cashflow_docs),
        ):
            result = await session.execute(
                select(model_class).where(
                    model_class.symbol == symbol,
                    model_class.consolidated == is_cons,
                    model_class.filing_type == db_ft,
                ).order_by(model_class.period_end_date.desc())
            )
            for doc in result.scalars().all():
                d = doc.to_dict()
                key = d.get("period_end_date")
                if key and key not in dest:
                    key_str = key.isoformat() if hasattr(key, "isoformat") else str(key)
                    dest[key_str] = d

    all_dates = sorted(
        set(income_docs.keys()) | set(balance_docs.keys()) | set(cashflow_docs.keys()),
        reverse=True,
    )

    merged_records: list[dict] = []
    for d in all_dates:
        merged = {"period_end_date": d, "consolidated": is_cons}
        for src in (income_docs, balance_docs, cashflow_docs):
            doc = src.get(d)
            if doc:
                for k, v in doc.items():
                    if k not in (
                        "period_end_date",
                        "consolidated",
                        "symbol",
                        "pulled_at",
                        "_content_hash",
                        "id",
                    ):
                        merged[k] = v
        merged_records.append(merged)

    if not merged_records:
        return {}

    records = merged_records
    latest = records[0]

    price_info = await _safe_market_fetch(fetch_price_info, symbol, source)
    current_price = _to_float(price_info.get("current_price"))
    shares_outstanding = _to_float(price_info.get("shares_outstanding"))

    technicals = await _safe_market_fetch(fetch_technicals, symbol, source)

    assets_t = _to_float(latest.get("assets"))
    equity_sc = _to_float(latest.get("equity_share_capital"))
    other_eq = _to_float(latest.get("other_equity"))
    borrowings_c = _to_float(latest.get("borrowings_current"))
    borrowings_nc = _to_float(latest.get("borrowings_noncurrent"))
    ncl = _to_float(latest.get("noncurrent_liabilities"))
    cash_eq = _to_float(latest.get("cash_and_cash_equivalents"))
    debt_eq_ratio = _to_float(latest.get("debt_equity_ratio"))

    is_ttm = filing_type == "ttm"
    flow_fields = [
        "revenue_from_operations",
        "profit_before_tax",
        "profit_loss_for_period",
        "finance_costs",
        "cash_flows_from_used_in_operating_activities",
        "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations",
    ]
    ttm_values: dict = {}
    if is_ttm or filing_type == "quarterly":
        for f in flow_fields:
            ttm_values[f] = _ttm_window(
                records,
                f,
                0,
                require_all=(f != "cash_flows_from_used_in_operating_activities"),
            )

    ttm_rev = ttm_values.get("revenue_from_operations")
    ttm_pat = ttm_values.get("profit_loss_for_period")
    ttm_pbt = ttm_values.get("profit_before_tax")
    ttm_fc = ttm_values.get("finance_costs")
    ttm_ocf = ttm_values.get("cash_flows_from_used_in_operating_activities")
    ttm_eps = ttm_values.get(
        "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
    )
    ttm_ebit = (
        (ttm_pbt or 0) + (ttm_fc or 0)
        if ttm_pbt is not None or ttm_fc is not None
        else None
    )

    if is_ttm:
        rev, pbt, pat, fc, ocf, eps = [ttm_values[f] for f in flow_fields]
    else:
        rev = _to_float(latest.get("revenue_from_operations"))
        pbt = _to_float(latest.get("profit_before_tax"))
        pat = _to_float(latest.get("profit_loss_for_period"))
        fc = _to_float(latest.get("finance_costs"))
        ocf = _to_float(latest.get("cash_flows_from_used_in_operating_activities"))
        eps = _to_float(
            latest.get(
                "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
            )
        )
    ebit = (pbt or 0) + (fc or 0) if pbt is not None or fc is not None else None

    total_debt = (borrowings_c or 0) + (borrowings_nc or 0)
    total_equity = (equity_sc or 0) + (other_eq or 0)
    market_cap = (
        current_price * shares_outstanding
        if current_price is not None and shares_outstanding is not None
        else None
    )
    enterprise_value = (
        (market_cap or 0) + total_debt - (cash_eq or 0)
        if market_cap is not None
        else None
    )

    def _growth_rate(current_val, previous_val):
        if current_val is not None and previous_val is not None and previous_val != 0:
            return _pct(_safe_div(current_val - previous_val, previous_val))
        return None

    latest_date = records[0].get("period_end_date")
    if hasattr(latest_date, "isoformat"):
        latest_date = latest_date.isoformat()
    yoy_rec = _find_record(records, latest_date, 12) if latest_date else None

    if is_ttm:
        rev_prior = _ttm_window(records, "revenue_from_operations", 4)
        pat_prior = _ttm_window(records, "profit_loss_for_period", 4)
        eps_prior = _ttm_window(
            records,
            "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations",
            4,
        )
        ocf_prior = _ttm_window(
            records, "cash_flows_from_used_in_operating_activities", 4
        )
        pbt_prior = _ttm_window(records, "profit_before_tax", 4)
        fc_prior = _ttm_window(records, "finance_costs", 4)
        ebit_prior = (
            (pbt_prior or 0) + (fc_prior or 0)
            if pbt_prior is not None or fc_prior is not None
            else None
        )

        revenue_growth = _growth_rate(ttm_rev, rev_prior)
        earnings_growth = _growth_rate(ttm_pat, pat_prior)
        eps_growth = _growth_rate(ttm_eps, eps_prior)
        ocf_growth = _growth_rate(ttm_ocf, ocf_prior)
        op_income_growth = _growth_rate(ttm_ebit, ebit_prior)
    else:
        revenue_growth = _growth_rate(
            _to_float(latest.get("revenue_from_operations")),
            _to_float(yoy_rec.get("revenue_from_operations")) if yoy_rec else None,
        )
        earnings_growth = _growth_rate(
            _to_float(latest.get("profit_loss_for_period")),
            _to_float(yoy_rec.get("profit_loss_for_period")) if yoy_rec else None,
        )
        eps_growth = _growth_rate(
            _to_float(
                latest.get(
                    "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
                )
            ),
            _to_float(
                yoy_rec.get(
                    "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
                )
            )
            if yoy_rec
            else None,
        )
        ocf_growth = _growth_rate(
            _to_float(latest.get("cash_flows_from_used_in_operating_activities")),
            _to_float(yoy_rec.get("cash_flows_from_used_in_operating_activities"))
            if yoy_rec
            else None,
        )
        op_income_growth = _growth_rate(
            ebit,
            (_to_float(yoy_rec.get("profit_before_tax")) or 0)
            + (_to_float(yoy_rec.get("finance_costs")) or 0)
            if yoy_rec
            else None,
        )

    book_value_growth = _growth_rate(
        total_equity if total_equity else None,
        (_to_float(yoy_rec.get("equity_share_capital")) or 0)
        + (_to_float(yoy_rec.get("other_equity")) or 0)
        if yoy_rec
        else None,
    )
    ebitda_growth = op_income_growth

    if is_ttm:
        eps_vals = [
            _ttm_window(
                records,
                "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations",
                i,
            )
            for i in range(max(0, len(records) - 3))
        ]
    else:
        eps_vals = [
            _to_float(
                r.get(
                    "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
                )
            )
            for r in records
        ]
    cagr = None
    if (
        len(eps_vals) >= 13
        and eps_vals[0] is not None
        and eps_vals[12] is not None
        and eps_vals[0] > 0
        and eps_vals[12] > 0
    ):
        cagr = ((eps_vals[0] / eps_vals[12]) ** (1.0 / 3) - 1) * 100

    peg_growth = cagr if cagr is not None and cagr > 0 else eps_growth

    result: Dict[str, Any] = {
        "symbol": symbol,
        "period_end_date": latest.get("period_end_date"),
        "consolidated": is_cons,
        "filing_type": filing_type,
        "price_data": "live" if price_info else "unavailable",
    }
    for k in (
        "current_price",
        "rsi_14",
        "sma_20",
        "sma_50",
        "sma_200",
        "ema_20",
        "bb_upper",
        "bb_middle",
        "bb_lower",
        "atr_14",
        "volume",
        "avg_volume_10d",
        "avg_volume_3m",
        "high_52w",
        "low_52w",
        "change_pct",
        "volume_ratio",
        "delivery_percentage",
        "relative_strength",
    ):
        if k in technicals:
            result[k] = technicals[k]
    if current_price is not None:
        result["current_price"] = current_price

    result["enterprise_value"] = enterprise_value
    val_eps = ttm_eps if ttm_eps is not None else eps
    val_rev = ttm_rev if ttm_rev is not None else rev
    val_ebit = ttm_ebit if ttm_ebit is not None else ebit
    val_ocf = ttm_ocf if ttm_ocf is not None else ocf
    result["price_to_earnings_ratio"] = (
        _safe_div(current_price, val_eps)
        if current_price is not None and val_eps is not None and val_eps != 0
        else None
    )
    pe = result["price_to_earnings_ratio"]
    bvps = (
        _safe_div(total_equity, shares_outstanding)
        if total_equity and shares_outstanding
        else None
    )
    result["price_to_book_ratio"] = (
        _safe_div(current_price, bvps) if current_price else None
    )
    sps = _safe_div(val_rev, shares_outstanding) if shares_outstanding else None
    result["price_to_sales_ratio"] = (
        _safe_div(current_price, sps) if current_price else None
    )
    result["enterprise_value_to_ebitda_ratio"] = (
        _safe_div(enterprise_value, val_ebit) if enterprise_value is not None else None
    )
    result["enterprise_value_to_revenue_ratio"] = (
        _safe_div(enterprise_value, val_rev) if enterprise_value is not None else None
    )
    fcf = val_ocf or 0
    result["free_cash_flow_yield"] = (
        _pct(_safe_div(fcf, market_cap)) if market_cap else None
    )
    result["peg_ratio"] = (
        _safe_div(pe, peg_growth)
        if pe is not None and peg_growth is not None and peg_growth > 0
        else None
    )

    result["gross_margin"] = None
    result["operating_margin"] = _pct(_safe_div(ebit, rev)) if rev else None
    result["net_margin"] = _pct(_safe_div(pat, rev)) if rev else None
    result["return_on_equity"] = (
        _pct(_safe_div(pat, total_equity)) if total_equity else None
    )
    result["return_on_assets"] = _pct(_safe_div(pat, assets_t)) if assets_t else None
    result["return_on_invested_capital"] = (
        _pct(_safe_div(ebit, (assets_t or 0) - (ncl or 0)))
        if ebit is not None and assets_t is not None
        else None
    )

    result["asset_turnover"] = _safe_div(rev, assets_t) if assets_t else None
    result["inventory_turnover"] = None
    result["receivables_turnover"] = None
    result["days_sales_outstanding"] = None
    result["operating_cycle"] = None
    result["working_capital_turnover"] = None

    result["current_ratio"] = None
    result["quick_ratio"] = None
    result["cash_ratio"] = _safe_div(cash_eq, borrowings_c) if borrowings_c else None
    result["operating_cash_flow_ratio"] = (
        _safe_div(ocf, borrowings_c) if borrowings_c else None
    )

    result["debt_to_equity"] = (
        debt_eq_ratio
        if debt_eq_ratio is not None
        else _safe_div(total_debt, total_equity)
    )
    result["debt_to_assets"] = _safe_div(total_debt, assets_t) if assets_t else None
    result["interest_coverage"] = _safe_div(ebit, fc) if fc else None

    result["revenue_growth"] = revenue_growth
    result["earnings_growth"] = earnings_growth
    result["book_value_growth"] = book_value_growth
    result["earnings_per_share_growth"] = eps_growth
    result["free_cash_flow_growth"] = ocf_growth
    result["operating_income_growth"] = op_income_growth
    result["ebitda_growth"] = ebitda_growth

    result["earnings_per_share"] = eps
    result["book_value_per_share"] = bvps
    result["free_cash_flow_per_share"] = (
        _safe_div(fcf, shares_outstanding) if shares_outstanding else None
    )

    result["payout_ratio"] = None
    result["market_capitalization"] = market_cap
    result["total_debt"] = total_debt if total_debt else None
    result["total_equity"] = total_equity if total_equity else None
    result["cash_and_equivalents"] = cash_eq

    return result
