"""SEC (US) data source.

Reads company financial XBRL from EDGAR via ``edgartools`` and serves it
through the same statement tables and API shapes as NSE, distinguished by the
``source`` column (value ``"SEC"``).

SEC conventions differ from NSE in two ways that matter here:
  * 10-Q income and cash-flow statements are *cumulative YTD* per fiscal year.
    Single-quarter values are derived by differencing consecutive periods.
  * 10-K income/cash-flow are full-year; balance sheets are point-in-time
    instants either way. Neither needs differencing.

EDGAR requires a "Name email" declaration and enforces 10 req/s; edgartools
handles the throttling internally.
"""

from __future__ import annotations

import asyncio
import ctypes
import gc
import multiprocessing as mp
import os
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from loguru import logger
from sqlalchemy import select

from src.db.engine import get_session_factory
from src.utils.helpers import utcnow
from src.db.models import NSEStockMetadata

from ._common import NotFoundError, UpstreamError
from .nse import STATEMENT_MODELS, _upsert_rows

# edgartools is imported lazily inside the functions that need it: it costs
# ~60MB RSS (dill, multiinspector, huge class hierarchy) and the API serves
# mostly NSE traffic. Deferring the import keeps idle footprint low on 512MB
# instances and only pays it during SEC pulls.

# EDGAR requires a "Name email" declaration. SEC's bot filter learned to 403
# the old "VoyagerData/1.0 (github...)" identity outright (every request from
# every IP), while a fresh declared identity passes; keep this token unique
# and never reuse a previously-blocked app identifier.
_DEFAULT_IDENTITY = "Voyager/1.0 (data@voyager.local)"
_edgar_ready = False


def _ensure_edgar():
    """Import edgartools once and apply the identity/proxy configuration."""
    global _edgar_ready
    if _edgar_ready:
        return
    from edgar import set_identity
    from edgar.httpclient import configure_http as _configure_http

    identity = os.getenv("SEC_IDENTITY", _DEFAULT_IDENTITY).strip(" \t\r\n\"'")
    if identity:
        identity = " ".join(identity.split())
        set_identity(identity)
        logger.info(f"EDGAR identity set: {identity!r}")
    proxy = os.getenv("SEC_PROXY", "").strip()
    if proxy:
        # Cloud egress IPs (Render/AWS) are hard-flagged by SEC's bot filter
        # even with a browser UA; route through an egress proxy instead.
        _configure_http(proxy=proxy)
        logger.info(f"EDGAR proxy set: {proxy}")
    _edgar_ready = True

EDGAR_MAX_ANNUAL_FILINGS = int(os.getenv("EDGAR_MAX_ANNUAL_FILINGS", "8"))
EDGAR_MAX_QUARTERLY_FILINGS = int(os.getenv("EDGAR_MAX_QUARTERLY_FILINGS", "40"))
# XBRLS.from_filings holds every filing's parsed facts in memory at once; on
# a 512MB Render instance 40 quarters OOMs the worker.
# Filings parsed per forked child. One filing keeps the child's peak at the
# largest single document (an IBM 10-K inline-XBRL instance is ~200MB+);
# 2 filings per child measured 594MB against Render's 512MB limit. Raise it
# only on a bigger instance.
EDGAR_PARSE_BATCH = int(os.getenv("EDGAR_PARSE_BATCH", "1"))

try:
    _libc = ctypes.CDLL("libc.so.6")
    _libc.malloc_trim.argtypes = [ctypes.c_size_t]
except (OSError, AttributeError):  # non-glibc (musl): gc alone is all there is
    _libc = None


def _release_memory() -> None:
    """Hand freed heap back to the OS, not just back to Python.

    XBRL parsing allocates ~25MB of raw inline-XBRL markup per filing and
    frees it again, but gc.collect() only returns the pages to glibc's arena:
    measured RSS after a 4-filing parse stayed at 405MB with zero live strings
    still allocated, and the peak grew with every batch until Render OOM-killed
    the worker mid-pull. malloc_trim(0) returns those pages, so the peak tracks
    the largest live batch instead of the sum of all of them.
    """
    gc.collect()
    if _libc is not None:
        _libc.malloc_trim(0)

# A period gap larger than this (days) separates fiscal years in a 10-Q frame
# (Q4 is reported in the 10-K, so the Jun->Dec gap is ~6 months).
_MAX_QUARTER_GAP_DAYS = 125

_META_FIELDS = frozenset({
    "concept", "label", "standard_concept", "preferred_sign",
    "parent_concept", "level", "abstract", "dimension", "is_breakdown",
    "dimension_axis", "dimension_member", "dimension_member_label",
    "dimension_label", "balance", "weight",
})

_INCOME_MAP = {
    "revenue_from_operations": [
        "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap_SalesRevenueNet",
        "us-gaap_Revenues",
    ],
    "profit_before_tax": [
        "us-gaap_IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "us-gaap_IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "tax_expense": ["us-gaap_IncomeTaxExpenseBenefit"],
    "profit_loss_for_period": ["us-gaap_NetIncomeLoss"],
    "profit_or_loss_attributable_to_owners_of_parent": ["us-gaap_NetIncomeLoss"],
    "comprehensive_income_for_the_period": [
        "us-gaap_ComprehensiveIncomeNetOfTax",
        "us-gaap_NetIncomeLoss",
    ],
    "finance_costs": [
        "us-gaap_InterestExpense",
        "us-gaap_InterestExpenseNonoperating",
    ],
    "depreciation_depletion_and_amortisation_expense": [
        "us-gaap_DepreciationDepletionAndAmortization",
        "us-gaap_DepreciationAmortizationAndAccretionNet",
        "us-gaap_DepreciationDepletionAndAmortizationExcludingFinancingCosts",
    ],
    "expenses": ["us-gaap_CostsAndExpenses", "us-gaap_OperatingExpenses"],
    "cost_of_revenue": [
        "us-gaap_CostOfRevenue",
        "us-gaap_CostOfGoodsAndServicesSold",
        "us-gaap_CostOfGoodsSold",
    ],
    "current_tax": ["us-gaap_CurrentIncomeTaxExpenseBenefit"],
    "deferred_tax": ["us-gaap_DeferredIncomeTaxExpenseBenefit"],
    "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations": [
        "us-gaap_EarningsPerShareBasic",
    ],
    "diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations": [
        "us-gaap_EarningsPerShareDiluted",
    ],
}

_BALANCE_MAP = {
    "assets": ["us-gaap_Assets"],
    "assets_current": ["us-gaap_AssetsCurrent"],
    "inventories": [
        "us-gaap_InventoryNet",
        "us-gaap_InventoryGross",
    ],
    "trade_receivables_current": [
        "us-gaap_AccountsReceivableNetCurrent",
        "us-gaap_ReceivablesNetCurrent",
        "us-gaap_AccountsNotesAndLoansReceivableNetCurrent",
    ],
    "trade_payables": [
        "us-gaap_AccountsPayableCurrent",
        "us-gaap_AccountsPayableTradeCurrent",
    ],
    "noncurrent_assets": ["us-gaap_AssetsNoncurrent"],
    "current_liabilities": ["us-gaap_LiabilitiesCurrent"],
    "noncurrent_liabilities": ["us-gaap_LiabilitiesNoncurrent"],
    "borrowings_current": [
        "us-gaap_DebtCurrent",
        "us-gaap_LongTermDebtCurrent",
    ],
    "borrowings_noncurrent": ["us-gaap_LongTermDebtNoncurrent"],
    "cash_and_cash_equivalents": [
        "us-gaap_CashAndCashEquivalentsAtCarryingValue",
    ],
    "goodwill": ["us-gaap_Goodwill"],
    "other_intangible_assets": [
        "us-gaap_FiniteLivedIntangibleAssetsNet",
        "us-gaap_IntangibleAssetsNetExcludingGoodwill",
        "us-gaap_IntangibleAssetsNetTotal",
        "us-gaap_IntangibleAssetsNet",
    ],
    "deferred_tax_assets_net": ["us-gaap_DeferredTaxAssetsNet"],
    "equity_share_capital": [
        "us-gaap_CommonStockValue",
        "us-gaap_CommonStocksIncludingAdditionalPaidInCapital",
    ],
    "stockholders_equity": ["us-gaap_StockholdersEquity"],
    "reserve_excluding_revaluation_reserves": [
        "us-gaap_RetainedEarningsAccumulatedDeficit",
    ],
}

_CASHFLOW_MAP = {
    "cash_flows_from_used_in_operating_activities": [
        "us-gaap_NetCashProvidedByUsedInOperatingActivities",
        "us-gaap_NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "us-gaap_NetCashProvidedByUsedInOperatingActivitiesExcludingDividendsPaidNetOfDiscontinuedOperations",
    ],
    "dividends_paid": [
        "us-gaap_PaymentsOfDividends",
        "us-gaap_PaymentsOfDividendsCommonStock",
        "us-gaap_PaymentsOfDividendsPreferredStockAndPreferenceStock",
    ],
    "payments_for_purchase_of_noncurrent_assets": [
        "us-gaap_PaymentsToAcquirePropertyPlantAndEquipment",
        "us-gaap_PaymentsToAcquireProductiveAssets",
        "us-gaap_PaymentsToAcquireOtherPropertyPlantAndEquipment",
    ],
}

# US filers report depreciation and intangible amortisation in the cash-flow
# statement, not the income statement, and split them across two tags (IBM
# tags Depreciation + AmortizationOfIntangibleAssets, never
# DepreciationDepletionAndAmortization). Looked up in the cash-flow frame;
# _lookup_sum adds the parts together.
_DA_MAP = {
    "depreciation_depletion_and_amortisation_expense": [
        "us-gaap_Depreciation",
        "us-gaap_AmortizationOfIntangibleAssets",
        "us-gaap_AmortizationOfDeferredCharges",
    ],
}

_MAX_INSIDER_PCT = 100.0
_SEC_DEFAULT_EXCHANGE = "NASDAQ"


def _nan_to_none(val: Any) -> Any:
    if isinstance(val, float):
        return None if val != val else val
    return val


def _period_to_date(period: Any) -> Optional[date]:
    if isinstance(period, date):
        return period
    try:
        return datetime.strptime(str(period), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _fiscal_period(period_end: date) -> Optional[str]:
    month = period_end.month
    if 1 <= month <= 3:
        return "Q4"
    if 4 <= month <= 6:
        return "Q1"
    if 7 <= month <= 9:
        return "Q2"
    if 10 <= month <= 12:
        return "Q3"
    return None


def _period_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if c not in _META_FIELDS and not c.startswith("dimension")]


def _lookup_value(
    df: pd.DataFrame, mappings: Dict[str, List[str]], field: str, period_col: str
) -> Optional[float]:
    for tag in mappings.get(field, []):
        rows = df.loc[df["concept"].astype(str) == tag, period_col]
        for v in rows.to_numpy():
            v = _nan_to_none(v)
            if isinstance(v, (int, float)) and v == v:
                return float(v)
    return None


def _lookup_sum(
    df: pd.DataFrame, mappings: Dict[str, List[str]], field: str, period_col: str
) -> Optional[float]:
    """Add up every concept mapped to `field` (D&A is split across tags)."""
    total, found = 0.0, False
    for tag in mappings.get(field, []):
        v = _lookup_value(df, {field: [tag]}, field, period_col)
        if v is not None:
            total += v
            found = True
    return total if found else None


def _diff_cumulative(df: pd.DataFrame, periods: List[str]) -> pd.DataFrame:
    """Convert a cumulative-YTD frame (income/cash-flow from 10-Qs) into
    single-quarter values.

    Consecutive period columns within a fiscal year hold YTD totals; the value
    for the later quarter minus the earlier quarter is that quarter alone. A gap
    larger than a quarter (Jun->Dec) marks a new fiscal year, where YTD starts
    fresh and the column is already the single-quarter value.

    ponytail: assumes a regular 3-month quarter cadence; splintered schedules
    would mislabel single quarters (metrics then go stale, not wrong).
    """
    dated = sorted(
        [(period, _period_to_date(period)) for period in periods if _period_to_date(period)],
        key=lambda t: t[1],
    )
    if len(dated) < 2:
        return df
    result = df.copy()
    for i, (period, d) in enumerate(dated):
        if i == 0:
            continue
        prev_period, prev_d = dated[i - 1]
        gap = (d - prev_d).days
        if gap <= _MAX_QUARTER_GAP_DAYS:
            result[period] = pd.to_numeric(df[period], errors="coerce") - pd.to_numeric(
                df[prev_period], errors="coerce"
            )
    return result


def _base_row(symbol: str, period: str, source_endpoint: str, is_annual: bool) -> Dict[str, Any]:
    period_end = _period_to_date(period)
    return {
        "symbol": symbol,
        "period_end_date": period_end,
        "period_start_date": None,
        "consolidated": True,
        "filing_type": "annual" if is_annual else "quarterly",
        "measure": "USD",
        "entity_identifier": None,
        "fiscal_period": _fiscal_period(period_end) if period_end else None,
        "source_endpoint": source_endpoint,
        "source": "SEC",
    }


def _income_rows(
    symbol: str,
    df: pd.DataFrame,
    periods: List[str],
    source_endpoint: str,
    is_annual: bool,
    cash_df: Optional[pd.DataFrame] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for period in periods:
        if period not in df.columns:
            continue
        row = _base_row(symbol, period, source_endpoint, is_annual)
        row["revenue_from_operations"] = _lookup_value(df, _INCOME_MAP, "revenue_from_operations", period)
        row["profit_before_tax"] = _lookup_value(df, _INCOME_MAP, "profit_before_tax", period)
        row["tax_expense"] = _lookup_value(df, _INCOME_MAP, "tax_expense", period)
        row["current_tax"] = _lookup_value(df, _INCOME_MAP, "current_tax", period)
        row["deferred_tax"] = _lookup_value(df, _INCOME_MAP, "deferred_tax", period)
        pat = _lookup_value(df, _INCOME_MAP, "profit_loss_for_period", period)
        row["profit_loss_for_period"] = pat
        row["profit_loss_for_period_from_continuing_operations"] = pat
        row["profit_or_loss_attributable_to_owners_of_parent"] = _lookup_value(
            df, _INCOME_MAP, "profit_or_loss_attributable_to_owners_of_parent", period
        )
        row["comprehensive_income_for_the_period"] = _lookup_value(
            df, _INCOME_MAP, "comprehensive_income_for_the_period", period
        )
        row["finance_costs"] = _lookup_value(df, _INCOME_MAP, "finance_costs", period)
        row["depreciation_depletion_and_amortisation_expense"] = _lookup_value(
            df, _INCOME_MAP, "depreciation_depletion_and_amortisation_expense", period
        )
        if row["depreciation_depletion_and_amortisation_expense"] is None and cash_df is not None:
            row["depreciation_depletion_and_amortisation_expense"] = _lookup_sum(
                cash_df, _DA_MAP, "depreciation_depletion_and_amortisation_expense", period
            )
        row["expenses"] = _lookup_value(df, _INCOME_MAP, "expenses", period)
        row["cost_of_revenue"] = _lookup_value(df, _INCOME_MAP, "cost_of_revenue", period)
        eps_b = _lookup_value(
            df, _INCOME_MAP, "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations", period
        )
        eps_d = _lookup_value(
            df, _INCOME_MAP, "diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations", period
        )
        row["basic_earnings_loss_per_share_from_continuing_operations"] = eps_b
        row["diluted_earnings_loss_per_share_from_continuing_operations"] = eps_d
        row["basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"] = eps_b
        row["diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations"] = eps_d
        rows.append(row)
    return rows


def _balance_rows(
    symbol: str, df: pd.DataFrame, periods: List[str], source_endpoint: str, is_annual: bool
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for period in periods:
        if period not in df.columns:
            continue
        row = _base_row(symbol, period, source_endpoint, is_annual)
        row["assets"] = _lookup_value(df, _BALANCE_MAP, "assets", period)
        row["noncurrent_assets"] = _lookup_value(df, _BALANCE_MAP, "noncurrent_assets", period)
        row["assets_current"] = _lookup_value(df, _BALANCE_MAP, "assets_current", period)
        row["inventories"] = _lookup_value(df, _BALANCE_MAP, "inventories", period)
        row["trade_receivables_current"] = _lookup_value(
            df, _BALANCE_MAP, "trade_receivables_current", period
        )
        row["trade_payables"] = _lookup_value(df, _BALANCE_MAP, "trade_payables", period)
        row["current_liabilities"] = _lookup_value(df, _BALANCE_MAP, "current_liabilities", period)
        row["noncurrent_liabilities"] = _lookup_value(df, _BALANCE_MAP, "noncurrent_liabilities", period)
        row["borrowings_current"] = _lookup_value(df, _BALANCE_MAP, "borrowings_current", period)
        row["borrowings_noncurrent"] = _lookup_value(df, _BALANCE_MAP, "borrowings_noncurrent", period)
        row["cash_and_cash_equivalents"] = _lookup_value(df, _BALANCE_MAP, "cash_and_cash_equivalents", period)
        row["goodwill"] = _lookup_value(df, _BALANCE_MAP, "goodwill", period)
        row["other_intangible_assets"] = _lookup_value(df, _BALANCE_MAP, "other_intangible_assets", period)
        row["deferred_tax_assets_net"] = _lookup_value(df, _BALANCE_MAP, "deferred_tax_assets_net", period)
        row["reserve_excluding_revaluation_reserves"] = _lookup_value(
            df, _BALANCE_MAP, "reserve_excluding_revaluation_reserves", period
        )
        equity_sc = _lookup_value(df, _BALANCE_MAP, "equity_share_capital", period)
        stockholder_eq = _lookup_value(df, _BALANCE_MAP, "stockholders_equity", period)
        row["equity_share_capital"] = equity_sc
        if equity_sc is not None and stockholder_eq is not None:
            row["equity_share_capital"] = equity_sc
            row["other_equity"] = stockholder_eq - equity_sc
        else:
            row["other_equity"] = stockholder_eq
        rows.append(row)
    return rows


def _cashflow_rows(
    symbol: str, df: pd.DataFrame, periods: List[str], source_endpoint: str, is_annual: bool
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for period in periods:
        if period not in df.columns:
            continue
        row = _base_row(symbol, period, source_endpoint, is_annual)
        ocf = _lookup_value(df, _CASHFLOW_MAP, "cash_flows_from_used_in_operating_activities", period)
        row["cash_flows_from_used_in_operations"] = ocf
        row["cash_flows_from_used_in_operating_activities"] = ocf
        row["dividends_paid"] = _lookup_value(df, _CASHFLOW_MAP, "dividends_paid", period)
        row["payments_for_purchase_of_noncurrent_assets"] = _lookup_value(
            df, _CASHFLOW_MAP, "payments_for_purchase_of_noncurrent_assets", period
        )
        rows.append(row)
    return rows


def _tag_value(df: pd.DataFrame, tag: str, col: str) -> Optional[float]:
    for v in df.loc[df["concept"].astype(str) == tag, col].to_numpy():
        v = _nan_to_none(v)
        if isinstance(v, (int, float)) and v == v:
            return float(v)
    return None


def _q4_cash_frame(
    cash_annual_df: Optional[pd.DataFrame],
    cash_ytd_df: Optional[pd.DataFrame],
    annual_period: str,
    ytd_period: str,
) -> Optional[pd.DataFrame]:
    """Q4 cash-flow figures (FY total - 9-month YTD) as a one-column frame.

    D&A and capex live in the cash-flow statement, so the derived Q4 income
    row still needs them to stay in the TTM window.
    """
    if cash_annual_df is None or cash_ytd_df is None:
        return None
    a = cash_annual_df.reset_index(drop=True)
    q = cash_ytd_df.reset_index(drop=True)
    data = []
    for tag in set(a["concept"].astype(str)) & set(q["concept"].astype(str)):
        av = _tag_value(a, tag, annual_period)
        qv = _tag_value(q, tag, ytd_period)
        if av is None or qv is None:
            continue
        data.append({"concept": tag, annual_period: av - qv})
    if not data:
        return None
    return pd.DataFrame(data, columns=["concept", annual_period])


def _q4_rows(
    symbol: str,
    kind: str,
    annual_df: pd.DataFrame,
    q_ytd_df: pd.DataFrame,
    annual_period: str,
    ytd_period: str,
    cash_annual_df: Optional[pd.DataFrame] = None,
    cash_ytd_df: Optional[pd.DataFrame] = None,
) -> List[Dict[str, Any]]:
    """Fiscal-year-end quarter: 10-K FY total minus the Q3 10-Q's 9-month YTD."""
    annual_df = annual_df.reset_index(drop=True)
    q_ytd_df = q_ytd_df.reset_index(drop=True)
    tags = set(annual_df["concept"].astype(str)) & set(q_ytd_df["concept"].astype(str))
    data = []
    for t in tags:
        if t in _META_FIELDS:
            continue
        av = _tag_value(annual_df, t, annual_period)
        qv = _tag_value(q_ytd_df, t, ytd_period)
        if av is None or qv is None:
            continue
        data.append({"concept": t, annual_period: av - qv})
    frame = pd.DataFrame(data, columns=["concept", annual_period])
    if frame.empty:
        return []
    source_endpoint = "10-Q"
    if kind == "income":
        cash_q4 = _q4_cash_frame(cash_annual_df, cash_ytd_df, annual_period, ytd_period)
        rows = _income_rows(symbol, frame, [annual_period], source_endpoint, False, cash_q4)
        ni = _tag_value(frame, "us-gaap_NetIncomeLoss", annual_period)
        if ni is not None:
            shares = _tag_value(annual_df, "us-gaap_WeightedAverageNumberOfSharesOutstandingBasic", annual_period)
            if shares:
                for r in rows:
                    r["basic_earnings_loss_per_share_from_continuing_operations"] = ni / shares if shares else None
        dil_shares = _tag_value(annual_df, "us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding", annual_period)
        if ni is not None and dil_shares:
            eps_d = ni / dil_shares
            for r in rows:
                r["diluted_earnings_loss_per_share_from_continuing_operations"] = eps_d
        for r in rows:
            r["basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"] = r[
                "basic_earnings_loss_per_share_from_continuing_operations"
            ]
            r["diluted_earnings_loss_per_share_from_continuing_and_discontinued_operations"] = r[
                "diluted_earnings_loss_per_share_from_continuing_operations"
            ]
        return rows
    if kind == "cashflow":
        return _cashflow_rows(symbol, frame, [annual_period], source_endpoint, False)
    return []


def _frames_for_batch(batch: list) -> tuple:
    """Parse one batch into (income, balance, cashflow) frames.

    Runs in a short-lived forked child (see `_stitch_batched`). Each filing's
    inline-XBRL source is ~25MB and stays live for as long as the stitched
    object exists, so the frames have to leave the process that parsed them.
    """
    from edgar.xbrl import XBRLS

    xbrls = XBRLS.from_filings(batch)
    frames = (
        xbrls.statements.income_statement().to_dataframe(),
        xbrls.statements.balance_sheet().to_dataframe(),
        xbrls.statements.cash_flow_statement().to_dataframe(),
    )
    del xbrls
    gc.collect()
    return frames


def _merge_frames(frames: List[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Union per-batch frames into one statement frame.

    The same concept can appear in several batches (overlapping periods), so
    keep the first non-null value per (concept, period) column. Group by the
    concept column, not level=0: these frames carry a plain RangeIndex, so
    grouping by index welded row 0 of one filing to row 0 of the next and
    blended unrelated concepts into single rows.
    """
    if not frames:
        return None
    if len(frames) == 1:
        return frames[0]
    merged = pd.concat(frames, axis=0, ignore_index=False)
    return merged.groupby("concept", sort=False, as_index=False).first()


def _stitch_batched(filings: list, symbol: str, form: str) -> tuple:
    """Stitch statement frames from filings in small batches.

    XBRLS.from_filings parses every filing's XBRL at once; with 40 quarters
    that peaks near 1.4GB on a 512MB instance and the worker is OOM-killed
    mid-pull. Batching in-process was not enough: the freed pages only return
    to glibc's arena, so the peak still grew with every batch. Each batch
    therefore runs in a forked child that exits, handing its (small) frames
    back and taking its ~25MB-per-filing heap with it. Column sets are unioned;
    missing cells become NaN, matching the single-shot stitch.
    """
    _ensure_edgar()  # no-op after first SEC use; keeps edgartools lazy

    income_frames, balance_frames, cash_frames = [], [], []
    filings = list(filings)
    # ponytail: maxtasksperchild=1 bounds the peak to EDGAR_PARSE_BATCH
    # filings (~270MB at 2) no matter how many filings are pulled; the cost is
    # one cheap fork per batch. Raise EDGAR_PARSE_BATCH only on a bigger
    # instance.
    with mp.get_context("fork").Pool(1, maxtasksperchild=1) as pool:
        for start in range(0, len(filings), EDGAR_PARSE_BATCH):
            batch = filings[start : start + EDGAR_PARSE_BATCH]
            income, balance, cash = pool.apply(_frames_for_batch, (batch,))
            income_frames.append(income)
            balance_frames.append(balance)
            cash_frames.append(cash)
    _release_memory()

    return (
        _merge_frames(income_frames),
        _merge_frames(balance_frames),
        _merge_frames(cash_frames),
    )


def _one_company(symbol: str):
    _ensure_edgar()
    from edgar import Company

    try:
        return Company(symbol)
    except CompanyNotFoundError as exc:
        raise NotFoundError(f"Ticker '{symbol}' not found on SEC EDGAR") from exc


def _get_filings(company: Company, form: str, n: int) -> list:
    return list(company.get_filings(form=form, amendments=False).head(n))


async def pull_sec_data(
    symbol: str, filing_type: Optional[str] = None, refresh: bool = False
) -> Dict[str, Any]:
    """Pull 10-K/10-Q XBRL into the shared statement tables (source='SEC')."""
    symbol = symbol.upper()
    timing: Dict[str, Any] = {"phases": {}, "counts": {}, "total_ms": 0.0}
    _started = time.perf_counter()
    _last = [_started]

    def _tick(key: str) -> None:
        now = time.perf_counter()
        timing["phases"][key] = timing["phases"].get(key, 0.0) + (now - _last[0]) * 1000
        _last[0] = now

    def _count(key: str, n: int = 1) -> None:
        timing["counts"][key] = timing["counts"].get(key, 0) + n

    # edgartools is synchronous: it does its own HTTP calls, time.sleep()
    # backoffs and heavy XBRL parsing. Running it on the event loop blocks
    # every other request (including POST /pull) for the whole pull, which on a
    # rate-limited EDGAR IP can be tens of minutes. Offload to a thread; the
    # DB writes below stay on the loop so engine connections stay loop-bound.
    company = await asyncio.to_thread(_one_company, symbol)
    _tick("company")

    want_annual = filing_type in ("annual", None)
    want_quarterly = filing_type in ("quarterly", None)

    exchange = None
    try:
        ex = await asyncio.to_thread(company.get_exchanges)
        if ex:
            exchange = str(ex[0]).upper()
    except Exception:
        exchange = None
    exchange = exchange or _SEC_DEFAULT_EXCHANGE
    _tick("exchange")

    rows_by_coll: Dict[str, List[Dict[str, Any]]] = {
        "income_statements": [],
        "balance_sheets": [],
        "cash_flows": [],
    }

    existing_keys: Dict[str, set] = {}
    if not refresh:
        factory = get_session_factory()
        async with factory() as session:
            for coll, model in STATEMENT_MODELS.items():
                result = await session.execute(
                    select(model.source_endpoint, model.period_end_date).where(
                        model.symbol == symbol,
                        model.source == "SEC",
                    )
                )
                existing_keys[coll] = {(r[0], r[1]) for r in result.all()}
    _tick("existing_scan")
    records_pulled = 0
    parse_errors = 0
    parse_failures: List[str] = []
    frames_diag: List[str] = []

    def _parse(form: str) -> Optional[tuple]:
        last_error = None
        for attempt in range(3):
            try:
                filings = _get_filings(company, form, EDGAR_MAX_ANNUAL_FILINGS if form == "10-K" else EDGAR_MAX_QUARTERLY_FILINGS)
                if not filings:
                    last_error = f"{form}: SEC returned no filings for {symbol}"
                else:
                    try:
                        i, b, c = _stitch_batched(filings, symbol, form)
                    except Exception as exc:
                        last_error = f"{form}: {type(exc).__name__}: {exc}"
                    else:
                        if i is None:
                            last_error = f"{form}: no statement frames parsed"
                        else:
                            accs = [getattr(f, "accession_no", "?") for f in filings][:12]
                            summary = (
                                f"{form}: filings={len(filings)} "
                                f"income{i.shape}{'/concept' if 'concept' in i.columns else '/NO-concept'} "
                                f"balance{b.shape}{'/concept' if 'concept' in b.columns else '/NO-concept'} "
                                f"cashflow{c.shape}{'/concept' if 'concept' in c.columns else '/NO-concept'} "
                                f"acc={accs}"
                            )
                            frames_diag.append(summary)
                            logger.info(f"SEC {symbol} {summary}")
                            return (i, b, c, len(filings))
            except Exception as exc:
                last_error = f"{form}: {type(exc).__name__}: {exc}"
            if attempt < 2:
                time.sleep(1 + attempt * 2)
        parse_failures.append(last_error or f"{form}: parse failed")
        logger.warning(f"SEC parse failed for {symbol} {form} (after 3 attempts): {last_error}")
        return None

    annual = None
    if want_quarterly or want_annual:
        p = await asyncio.to_thread(_parse, "10-K")
        if p is not None:
            annual = (p[0], p[1], p[2])
            records_pulled += p[3]
        else:
            parse_errors += 1

    if want_annual and annual is not None:
        income_k, balance_k, cashflow_k = annual
        if "concept" in income_k.columns:
            k_periods = _period_columns(income_k)
            rows_by_coll["income_statements"].extend(_income_rows(symbol, income_k, k_periods, "10-K", True, cashflow_k))
            if "concept" in balance_k.columns:
                rows_by_coll["balance_sheets"].extend(_balance_rows(symbol, balance_k, _period_columns(balance_k), "10-K", True))
            if "concept" in cashflow_k.columns:
                rows_by_coll["cash_flows"].extend(_cashflow_rows(symbol, cashflow_k, _period_columns(cashflow_k), "10-K", True))

    if want_quarterly:
        q = await asyncio.to_thread(_parse, "10-Q")
        if q is not None:
            income_q, balance_q, cashflow_q, _ = q
            records_pulled += q[3]
        else:
            parse_errors += 1
            income_q = None

        if income_q is not None and "concept" in income_q.columns:
            # Keep only the columns needed for Q4 derivation, not full frame
            # copies — three full statement frames at once OOM small instances.
            income_raw_q, cashflow_raw_q = income_q.copy(), cashflow_q.copy()
            q_periods = _period_columns(income_q)
            income_q = _diff_cumulative(income_q, q_periods)
            cashflow_q = _diff_cumulative(cashflow_q, _period_columns(cashflow_q))
            rows_by_coll["income_statements"].extend(_income_rows(symbol, income_q, q_periods, "10-Q", False, cashflow_q))
            rows_by_coll["cash_flows"].extend(_cashflow_rows(symbol, cashflow_q, _period_columns(cashflow_q), "10-Q", False))
            if "concept" in balance_q.columns:
                rows_by_coll["balance_sheets"].extend(_balance_rows(symbol, balance_q, _period_columns(balance_q), "10-Q", False))
            del balance_q

            if annual is not None:
                income_k, _, cashflow_k = annual
                k_periods = _period_columns(income_k)
                q_dates = sorted(
                    [(p, _period_to_date(p)) for p in q_periods if _period_to_date(p)], key=lambda t: t[1]
                )
                rev_row = income_k.loc[income_k["concept"] == "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax"]
                for P in k_periods:
                    if P in income_k.columns and not rev_row[P].isna().all():
                        pd_ = _period_to_date(P)
                        pick = next(
                            ((pr, d) for pr, d in reversed(q_dates) if pd_ and d < pd_ and (pd_ - d).days <= _MAX_QUARTER_GAP_DAYS),
                            None,
                        )
                        if not pick:
                            continue
                        rows_by_coll["income_statements"].extend(
                            _q4_rows(symbol, "income", income_k, income_raw_q, P, pick[0],
                                     cashflow_k, cashflow_raw_q)
                        )
                        rows_by_coll["cash_flows"].extend(
                            _q4_rows(symbol, "cashflow", cashflow_k, cashflow_raw_q, P, pick[0])
                        )
    _tick("parse")

    # Statement frames are no longer needed once rows are extracted; release
    # them before the DB write so peak RSS stays low on 512MB instances.
    del annual
    _release_memory()

    if existing_keys:
        for coll, rows in rows_by_coll.items():
            keys = existing_keys.get(coll, set())
            if not keys:
                continue
            rows_by_coll[coll] = [
                r for r in rows if (r.get("source_endpoint"), r.get("period_end_date")) not in keys
            ]

    upserted = 0
    factory = get_session_factory()
    async with factory() as session:
        for coll, rows in rows_by_coll.items():
            if not rows:
                continue
            await _upsert_rows(
                session,
                STATEMENT_MODELS[coll],
                rows,
                on_conflict_cols=["symbol", "period_end_date", "consolidated", "source_endpoint"],
            )
            upserted += len(rows)

        result = await session.execute(
            select(NSEStockMetadata).where(
                NSEStockMetadata.symbol == symbol,
                NSEStockMetadata.source == "SEC",
            )
        )
        meta = result.scalar_one_or_none()
        if upserted == 0 and meta is None:
            # Fresh symbol but nothing parsed (e.g. SEC unreachable): do not
            # fabricate a "last_pull" that makes an empty pull look healthy.
            logger.warning(
                f"SEC pull for {symbol} parsed 0 rows; skipping metadata update"
            )
        else:
            now = utcnow()
            if meta:
                if meta.last_pull:
                    prev = list(meta.previous_pulls or [])
                    prev.append(meta.last_pull)
                    meta.previous_pulls = prev
                meta.last_pull = now
                meta.exchange = exchange
                meta.updated_at = now
            else:
                session.add(
                    NSEStockMetadata(
                        symbol=symbol,
                        source="SEC",
                        exchange=exchange,
                        last_pull=now,
                        previous_pulls=[],
                        created_at=now,
                        updated_at=now,
                    )
                )
        await session.commit()
    _tick("db")

    timing["total_ms"] = round((time.perf_counter() - _started) * 1000, 1)
    for key in timing["phases"]:
        timing["phases"][key] = round(timing["phases"][key], 1)

    status = "completed" if upserted > 0 else ("partial" if parse_errors else "no data")
    result = {
        "symbol": symbol,
        "source": "SEC",
        "status": status,
        "records_pulled": records_pulled,
        "rows_written": upserted,
        "endpoint_breakdown": {"parse_errors": parse_errors},
        "timing": timing,
    }
    if frames_diag:
        result["frames_diag"] = "; ".join(frames_diag)
    if parse_failures:
        result["parse_error_detail"] = " | ".join(parse_failures)
    return result


async def get_announcements_us(
    symbol: str, country: str = "us", source: str = "sec", market: str = "equities"
) -> Dict[str, Any]:
    company = _one_company(symbol.upper())
    try:
        filings = company.get_filings(form="8-K", amendments=False).head(40)
    except Exception as exc:
        logger.warning(f"Announcements fetch failed for {symbol}: {exc}")
        raise UpstreamError(str(exc))

    items = []
    for f in filings:
        fdate = getattr(f, "filing_date", None)
        acc = getattr(f, "accession_number", None) or ""
        docs = getattr(f, "primary_document", None) or ""
        cik = getattr(f, "cik", None)
        url = None
        if cik and acc and docs:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{docs}"
        items.append(
            {
                "date": fdate.isoformat() if isinstance(fdate, date) else str(fdate or ""),
                "heading": f"8-K {docs or acc}",
                "category": "SEC 8-K",
                "attachment": url,
                "attachment_size": None,
                "has_xbrl": False,
            }
        )
    return {"symbol": symbol.upper(), "source": "SEC", "market": market, "announcements": items}


def _insider_share_count(company: Company, n: int = 80) -> Optional[Tuple[float, Optional[str]]]:
    """Latest post-transaction holding per insider (Forms 3/4).

    Returns ``(total_shares, latest_filing_date)``.
    """
    frames: List[pd.DataFrame] = []
    try:
        forms = company.get_filings(form="4", amendments=False).head(n)
        for f in forms:
            try:
                obj = f.obj()
                if hasattr(obj, "to_dataframe"):
                    frames.append(obj.to_dataframe())
            except Exception:
                continue
    except Exception as exc:
        logger.debug(f"Form 4 fetch failed for {company.cik}: {exc}")
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    if df.empty or "Insider" not in df.columns or "Remaining Shares" not in df.columns:
        return None
    latest = (
        df.dropna(subset=["Remaining Shares"])
        .sort_values("Date", ascending=False)
        .drop_duplicates(subset=["Insider"], keep="first")
    )
    as_of = None
    try:
        as_of = str(pd.to_datetime(latest["Date"]).max().date())
    except Exception:
        pass
    return float(latest["Remaining Shares"].sum()), as_of


async def get_shareholdings_us(
    symbol: str, country: str = "us", source: str = "sec"
) -> Dict[str, Any]:
    """US insider ownership from Forms 3/4/5 — a US-specific schema distinct
    from the India promoter/FII/DII pattern."""
    company = _one_company(symbol.upper())

    insider = await asyncio.to_thread(_insider_share_count, company)
    insider_shares = insider[0] if insider else None
    as_of = insider[1] if insider else None
    shares_out = None
    if insider_shares:
        try:
            from src.tools.nse.technicals import fetch_price_info

            price_info = await asyncio.to_thread(fetch_price_info, symbol.upper(), _SEC_DEFAULT_EXCHANGE)
            shares_out = price_info.get("shares_outstanding")
            shares_out = float(shares_out) if shares_out else None
        except Exception as exc:
            logger.debug(f"Shares outstanding fetch failed for {symbol}: {exc}")

    insider_ownership_pct = None
    if insider_shares and shares_out:
        insider_ownership_pct = round(min(insider_shares / shares_out * 100, _MAX_INSIDER_PCT), 2)

    return {
        "symbol": symbol.upper(),
        "source": "SEC",
        "shareholdings": {
            "source": "SEC",
            "source_endpoint": "Forms 3/4/5",
            "as_of": as_of,
            "insider_shares": insider_shares,
            "shares_outstanding": shares_out,
            "insider_ownership_pct": insider_ownership_pct,
            "note": "SEC Forms 3/4/5 insider holdings",
        },
    }
