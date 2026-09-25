import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import select

from src.db.engine import get_session_factory
from src.db.models import BalanceSheet, CashFlow, IncomeStatement, NSEStockMetadata

from ._common import InvalidRequestError, _validate_source


def _capex_magnitude(v) -> Optional[float]:
    """Capex is stored as a signed outflow (SEC tags it negative); FCF
    subtracts it, so compare on magnitude and ignore which way it points."""
    return abs(v) if v is not None else None


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


def _round2(v: Any) -> Any:
    """Round a numeric metric to max 2 decimals for the response."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    f = float(v)
    return None if (f != f or abs(f) == float("inf")) else round(f, 2)


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


_BS_META_FIELDS = frozenset({
    "id", "symbol", "pulled_at", "_content_hash",
    "period_end_date", "period_start_date", "xbrl_url", "broadcast_date",
    "consolidated", "filing_type", "measure", "entity_identifier",
    "fiscal_period", "source_endpoint", "context_ref_type",
})


def _carry_forward_balance_sheets(records: list, balance_docs: dict) -> None:
    """Interim quarters publish P&L-only XBRLs; fill missing stock fields from
    the nearest older balance sheet (NSE reports BS instants at year-end).
    """
    # ponytail: fixed 380-day lookback; widen only if NSE skips a year-end filing
    if not balance_docs:
        return
    bs_dates = sorted(balance_docs.keys(), reverse=True)
    fields = set()
    for d in balance_docs.values():
        fields |= set(d.keys())
    fields -= _BS_META_FIELDS
    # Newest quarterly filings can be stub rows (Q1 XBRLs only carry ratio
    # fields), so the fill set must come from every stored balance sheet, not
    # just the first row's keys.
    for r in records:
        d = r.get("period_end_date")
        if not isinstance(d, str):
            continue
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            continue
        for bd in bs_dates:
            try:
                bdt = datetime.strptime(bd, "%Y-%m-%d")
            except ValueError:
                continue
            gap = (dt - bdt).days
            if gap < 0:
                continue
            if gap > 380:
                break
            src = balance_docs[bd]
            for k in fields:
                if r.get(k) is None and src.get(k) is not None:
                    r[k] = src[k]


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
    country: Optional[str] = None,
    source: str = "nse",
    consolidated: bool = True,
    filing_type: str = "ttm",
) -> Dict[str, Any]:
    symbol = symbol.upper()
    _, source = _validate_source(country, source)

    if filing_type not in ("quarterly", "annual", "ttm"):
        raise InvalidRequestError("filing_type must be 'quarterly', 'annual', or 'ttm'")

    # One call serves all financial metrics: flows are computed on a TTM basis,
    # stocks (balance-sheet items) on the latest quarter. filing_type is kept
    # only as an optional override for callers that need a specific basis.
    is_ttm = filing_type == "ttm"

    from src.tools.nse.technicals import fetch_price_info, fetch_technicals

    is_cons = consolidated

    income_docs: dict = {}
    balance_docs: dict = {}
    cashflow_docs: dict = {}

    db_ft = "quarterly" if filing_type == "ttm" else filing_type

    yf_exchange = source
    factory = get_session_factory()
    async with factory() as session:
        if source == "SEC":
            result = await session.execute(
                select(NSEStockMetadata).where(
                    NSEStockMetadata.symbol == symbol,
                    NSEStockMetadata.source == "SEC",
                )
            )
            meta = result.scalar_one_or_none()
            yf_exchange = (meta.exchange or "NASDAQ") if meta else "NASDAQ"

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
                    model_class.source == source,
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
    _carry_forward_balance_sheets(records, balance_docs)
    latest = records[0]

    price_info = await _safe_market_fetch(fetch_price_info, symbol, yf_exchange)
    current_price = _to_float(price_info.get("current_price"))
    shares_outstanding = _to_float(price_info.get("shares_outstanding"))
    if shares_outstanding is None:
        from src.tools.nse.valuation import compute_shares_outstanding

        shares_outstanding = compute_shares_outstanding(latest)

    technicals = await _safe_market_fetch(fetch_technicals, symbol, yf_exchange)

    assets_t = _to_float(latest.get("assets"))
    equity_sc = _to_float(latest.get("equity_share_capital"))
    other_eq = _to_float(latest.get("other_equity"))
    borrowings_c = _to_float(latest.get("borrowings_current"))
    borrowings_nc = _to_float(latest.get("borrowings_noncurrent"))
    ncl = _to_float(latest.get("noncurrent_liabilities"))
    cash_eq_raw = _to_float(latest.get("cash_and_cash_equivalents"))
    bank_balance = _to_float(
        latest.get("bank_balance_other_than_cash_and_cash_equivalents")
    )
    # Cash & equivalents includes bank balances other than cash; filings split
    # them so a cash-only read understates liquidity (e.g. Skygold ₹235 Cr
    # vs ₹7.9 Cr).
    cash_eq = sum(
        v for v in (cash_eq_raw, bank_balance) if v is not None
    ) or None

    is_ttm = filing_type == "ttm"
    flow_fields = [
        "revenue_from_operations",
        "profit_before_tax",
        "profit_loss_for_period",
        "finance_costs",
        "depreciation_depletion_and_amortisation_expense",
        "cash_flows_from_used_in_operating_activities",
        "payments_for_purchase_of_noncurrent_assets",
        "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations",
        "cost_of_revenue",
        "expenses",
        "tax_expense",
        "dividends_paid",
    ]
    sparse_flows = {
        "cash_flows_from_used_in_operating_activities",
        "payments_for_purchase_of_noncurrent_assets",
        "dividends_paid",
    }
    ttm_values: dict = {f: None for f in flow_fields}

    def _compute_ttm_windows():
        for f in flow_fields:
            ttm_values[f] = _ttm_window(
                records,
                f,
                0,
                require_all=(f not in sparse_flows),
            )

    if filing_type != "annual":
        _compute_ttm_windows()

    # Graceful degradation: when fewer than 4 quarters are stored, the strict
    # TTM window returns None. Fall back to the latest single quarter so the
    # response stays populated (degraded, not empty) for newly listed symbols.
    if ttm_values.get("revenue_from_operations") is None:
        for f in flow_fields:
            latest_val = _to_float(latest.get(f))
            if ttm_values[f] is None and latest_val is not None:
                ttm_values[f] = latest_val
    ttm_rev = ttm_values.get("revenue_from_operations")
    ttm_pat = ttm_values.get("profit_loss_for_period")
    ttm_pbt = ttm_values.get("profit_before_tax")
    ttm_fc = ttm_values.get("finance_costs")
    ttm_ocf = ttm_values.get("cash_flows_from_used_in_operating_activities")
    ttm_capex = _capex_magnitude(ttm_values.get("payments_for_purchase_of_noncurrent_assets"))
    ttm_dep = ttm_values.get("depreciation_depletion_and_amortisation_expense")
    ttm_eps = ttm_values.get(
        "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
    )
    ttm_cor = ttm_values.get("cost_of_revenue")
    ttm_exp = ttm_values.get("expenses")
    ttm_tax = ttm_values.get("tax_expense")
    ttm_div = ttm_values.get("dividends_paid")
    ttm_ebit = (
        (ttm_pbt or 0) + (ttm_fc or 0)
        if ttm_pbt is not None or ttm_fc is not None
        else None
    )

    if is_ttm:
        rev, pbt, pat, fc, dep, ocf, capex, eps = [ttm_values[f] for f in flow_fields[:8]]
    else:
        rev = _to_float(latest.get("revenue_from_operations"))
        pbt = _to_float(latest.get("profit_before_tax"))
        pat = _to_float(latest.get("profit_loss_for_period"))
        fc = _to_float(latest.get("finance_costs"))
        dep = _to_float(latest.get("depreciation_depletion_and_amortisation_expense"))
        ocf = _to_float(latest.get("cash_flows_from_used_in_operating_activities"))
        capex = _capex_magnitude(_to_float(latest.get("payments_for_purchase_of_noncurrent_assets")))
        eps = _to_float(
            latest.get(
                "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
            )
        )
    ebit = (pbt or 0) + (fc or 0) if pbt is not None or fc is not None else None
    val_capex = ttm_capex if ttm_capex is not None else capex

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
    prev_q_rec = _find_record(records, latest_date, 3) if latest_date else None

    def _qoq(field: str) -> Optional[float]:
        return _growth_rate(
            _to_float(latest.get(field)),
            _to_float(prev_q_rec.get(field)) if prev_q_rec else None,
        )

    def _yoy(field: str) -> Optional[float]:
        return _growth_rate(
            _to_float(latest.get(field)),
            _to_float(yoy_rec.get(field)) if yoy_rec else None,
        )

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
        capex_prior = _capex_magnitude(
            _ttm_window(records, "payments_for_purchase_of_noncurrent_assets", 4)
        )
        dep_prior = _ttm_window(
            records, "depreciation_depletion_and_amortisation_expense", 4
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
        op_income_growth = _growth_rate(ttm_ebit, ebit_prior)
        # FCF and EBITDA need capex / D&A; without both, growth falls back to
        # the OCF and EBIT series rather than reporting a fabricated number.
        fcf_now, fcf_prior = ttm_ocf, ocf_prior
        if ttm_capex is not None and capex_prior is not None:
            fcf_now = ttm_ocf - ttm_capex if ttm_ocf is not None else None
            fcf_prior = (
                ocf_prior - capex_prior if ocf_prior is not None else None
            )
        fcf_growth = _growth_rate(fcf_now, fcf_prior)
        ebitda_now = ebitda_prior = None
        if ttm_dep is not None and dep_prior is not None:
            ebitda_now = (ttm_ebit or 0) + ttm_dep
            ebitda_prior = (ebit_prior or 0) + dep_prior
        ebitda_growth = _growth_rate(ebitda_now, ebitda_prior)
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
    if not is_ttm:
        yoy_ocf = _to_float(yoy_rec.get("cash_flows_from_used_in_operating_activities")) if yoy_rec else None
        yoy_capex = _capex_magnitude(_to_float(yoy_rec.get("payments_for_purchase_of_noncurrent_assets"))) if yoy_rec else None
        fcf_growth = _growth_rate(
            (ocf - capex) if (ocf is not None and capex is not None) else ocf,
            (yoy_ocf - yoy_capex) if (yoy_ocf is not None and yoy_capex is not None) else yoy_ocf,
        )
        yoy_dep = _to_float(yoy_rec.get("depreciation_depletion_and_amortisation_expense")) if yoy_rec else None
        ebitda_growth = (
            _growth_rate(ebit + dep, (yoy_rec.get("profit_before_tax") or 0) + (yoy_rec.get("finance_costs") or 0) + yoy_dep)
            if yoy_rec is not None and dep is not None and yoy_dep is not None
            else op_income_growth
        )
    peg_growth = eps_growth

    result: Dict[str, Any] = {
        "symbol": symbol,
        "last_quarter_end_date": latest.get("period_end_date"),
        "last_annual_end_date": None,
        "consolidated": is_cons,
        "filing_type": filing_type,
        "price_data": "live" if current_price is not None else "unavailable",
    }
    # Last annual period end: prefer an annual filing_type row if present,
    # else infer the fiscal year-end from stored quarterly periods.
    annual_dates = [
        k for k, d in balance_docs.items()
        if (d.get("filing_type") == "annual")
    ]
    if annual_dates:
        result["last_annual_end_date"] = max(annual_dates)
    else:
        try:
            dt = datetime.strptime(latest_date, "%Y-%m-%d")
            month, year = dt.month, dt.year
            if month == 12:
                result["last_annual_end_date"] = f"{year}-12-31"
            else:
                result["last_annual_end_date"] = f"{year - 1}-12-31"
        except (ValueError, TypeError):
            result["last_annual_end_date"] = None
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
    val_dep = ttm_dep if ttm_dep is not None else dep
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
    # One EBITDA for both the multiple and the margin: US filers report D&A in
    # the cash-flow statement, and when it is absent the multiple falls back to
    # EBIT rather than dividing by a fabricated zero.
    ebitda = (
        val_ebit + val_dep
        if val_ebit is not None and val_dep is not None
        else val_ebit
    )
    result["enterprise_value_to_ebitda_ratio"] = (
        _safe_div(enterprise_value, ebitda) if enterprise_value is not None else None
    )
    result["enterprise_value_to_revenue_ratio"] = (
        _safe_div(enterprise_value, val_rev) if enterprise_value is not None else None
    )
    # FCF = OCF - CapEx when CapEx is present in filings; falls back to OCF
    # (the old proxy) only when the filing omits CapEx.
    if val_ocf is not None:
        fcf = val_ocf - val_capex if val_capex is not None else val_ocf
        fcf_source = (
            "operating_cash_flow_minus_capex"
            if val_capex is not None
            else "operating_cash_flow_capex_absent"
        )
    else:
        fcf = None
        fcf_source = None
    result["free_cash_flow_source"] = fcf_source
    result["peg_ratio"] = (
        _safe_div(pe, peg_growth)
        if pe is not None and peg_growth is not None and peg_growth > 0
        else None
    )

    # Gross margin = (revenue - cost of revenue) / revenue. NSE integrated
    # filings don't always tag COGS; fall back to (revenue - expenses) with
    # other income excluded, and to None when neither is available.
    gross_profit = None
    if ttm_cor is not None and ttm_rev is not None:
        gross_profit = ttm_rev - ttm_cor
    elif ttm_exp is not None and ttm_rev is not None:
        gross_profit = ttm_rev - ttm_exp
    result["gross_margin"] = (
        _pct(_safe_div(gross_profit, ttm_rev))
        if gross_profit is not None and ttm_rev
        else None
    )
    result["ebitda_margin"] = (
        _pct(_safe_div(ebitda, val_rev))
        if ebitda is not None and val_dep is not None and val_rev
        else None
    )
    # Margins are on a TTM basis; a single quarter overstates/understates
    # (Amazon operating margin 39.65% quarterly vs 12.68% TTM).
    result["operating_margin"] = (
        _pct(_safe_div(val_ebit, val_rev)) if val_rev else None
    )
    result["net_margin"] = (
        _pct(_safe_div(ttm_pat, val_rev))
        if ttm_pat is not None and val_rev
        else None
    )
    # Return ratios compare TTM flows against point-in-time stocks; a single
    # quarter's PAT over equity understates ROE ~4x.
    ret_pat = ttm_pat if ttm_pat is not None else pat
    ret_ebit = ttm_ebit if ttm_ebit is not None else ebit
    result["return_on_equity"] = (
        _pct(_safe_div(ret_pat, total_equity)) if total_equity else None
    )
    result["return_on_assets"] = (
        _pct(_safe_div(ret_pat, assets_t)) if assets_t else None
    )
    # ROIC: TTM NOPAT (EBIT x (1 - effective tax rate)) / invested capital
    # (total debt + equity). Screener-style convention; the old assets-minus-
    # non-current-liabilities proxy understated it for cash-rich firms.
    invested_capital = total_debt + total_equity
    nopat = None
    if ret_ebit is not None:
        if ttm_pbt is not None and ttm_tax is not None and ttm_pbt != 0:
            tax_rate = max(0.0, min(ttm_tax / ttm_pbt, 1.0))
            nopat = ret_ebit * (1 - tax_rate)
        elif ttm_pat is not None:
            nopat = ttm_pat + ttm_fc if ttm_fc is not None else None
    result["return_on_invested_capital"] = (
        _pct(_safe_div(nopat, invested_capital))
        if nopat is not None and invested_capital
        else None
    )

    result["asset_turnover"] = _safe_div(val_rev, assets_t) if assets_t else None
    current_liab = _to_float(latest.get("current_liabilities"))
    assets_cur = _to_float(latest.get("assets_current"))
    inventories = _to_float(latest.get("inventories"))
    receivables = _to_float(latest.get("trade_receivables_current"))
    payables = _to_float(latest.get("trade_payables"))

    result["inventory_turnover"] = (
        _safe_div(ttm_cor if ttm_cor is not None else val_rev, inventories)
        if inventories
        else None
    )
    result["working_capital_turnover"] = (
        _safe_div(val_rev, assets_cur - current_liab)
        if assets_cur is not None and current_liab is not None
        and (assets_cur - current_liab) != 0
        else None
    )

    # Liquidity ratios need the current-assets/current-liabilities detail;
    # stay None (not zero) when the filing omits them.
    result["current_ratio"] = (
        _safe_div(assets_cur, current_liab)
        if assets_cur is not None and current_liab
        else None
    )
    if assets_cur is not None and current_liab:
        quick_assets = assets_cur - (inventories or 0)
        result["quick_ratio"] = _safe_div(quick_assets, current_liab)
    else:
        result["quick_ratio"] = None

    result["days_inventory_outstanding"] = (
        round(365.0 / result["inventory_turnover"], 2)
        if result["inventory_turnover"]
        else None
    )
    dso = (
        _safe_div(receivables, val_rev) if receivables is not None and val_rev else None
    )
    result["days_receivable_outstanding"] = round(dso * 365, 2) if dso is not None else None
    dpo = (
        _safe_div(payables, ttm_cor if ttm_cor is not None else val_rev)
        if payables is not None and (ttm_cor is not None or val_rev)
        else None
    )
    result["days_payable_outstanding"] = round(dpo * 365, 2) if dpo is not None else None

    # Always compute from balance-sheet components; the XBRL DebtEquityRatio
    # tag is unreliable (Skygold tagged 0.007 vs a real ~0.7).
    result["debt_to_equity"] = (
        _safe_div(total_debt, total_equity)
        if total_debt is not None and total_equity
        else None
    )
    result["interest_coverage"] = _safe_div(val_ebit, ttm_fc) if ttm_fc else None

    result["revenue_growth"] = revenue_growth
    result["revenue_growth_qoq"] = _qoq("revenue_from_operations")
    result["revenue_growth_yoy"] = _yoy("revenue_from_operations")
    result["earnings_growth"] = earnings_growth
    result["earnings_growth_qoq"] = _qoq("profit_loss_for_period")
    result["earnings_growth_yoy"] = _yoy("profit_loss_for_period")
    result["book_value_growth"] = book_value_growth
    result["book_value_growth_qoq"] = _growth_rate(
        total_equity if total_equity else None,
        (
            (_to_float(prev_q_rec.get("equity_share_capital")) or 0)
            + (_to_float(prev_q_rec.get("other_equity")) or 0)
        )
        if prev_q_rec
        else None,
    )
    result["book_value_growth_yoy"] = book_value_growth
    result["earnings_per_share_growth"] = eps_growth
    result["earnings_per_share_growth_qoq"] = _qoq(
        "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
    )
    result["earnings_per_share_growth_yoy"] = _yoy(
        "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
    )
    result["free_cash_flow_growth"] = fcf_growth
    result["operating_income_growth"] = op_income_growth
    result["ebitda_growth"] = ebitda_growth

    result["earnings_per_share"] = val_eps
    result["book_value_per_share"] = bvps
    result["free_cash_flow_per_share"] = (
        _safe_div(fcf, shares_outstanding)
        if fcf is not None and shares_outstanding
        else None
    )

    # Payout ratio: TTM dividends paid / TTM PAT. Filings store dividends as a
    # negative outflow, so take the absolute value or every payer reads negative.
    result["payout_ratio"] = (
        _pct(_safe_div(abs(ttm_div), ttm_pat))
        if ttm_div is not None and ttm_pat
        else None
    )
    result["market_capitalization"] = market_cap
    result["total_debt"] = total_debt if total_debt else None
    result["total_equity"] = total_equity if total_equity else None
    result["cash_and_equivalents"] = cash_eq

    # Max 2 decimal places on every numeric metric in the response.
    return {k: _round2(v) for k, v in result.items()}
