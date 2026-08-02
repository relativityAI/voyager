import math
from typing import Any, Dict, List, Optional


def to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        val = float(str(v).replace(",", ""))
        return None if math.isnan(val) or math.isinf(val) else val
    except (ValueError, TypeError):
        return None


def safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    result = a / b
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def compute_shares_outstanding(data: Dict[str, Any]) -> Optional[float]:
    paid_up = to_float(data.get("paid_up_value_of_equity_share_capital"))
    face_value = to_float(data.get("face_value_of_equity_share_capital"))
    if paid_up is not None and face_value is not None and face_value != 0:
        return paid_up / face_value
    return None


def compute_valuation(
    data: Dict[str, Any],
    current_price: Optional[float],
    shares_outstanding: Optional[float],
    eps_growth: Optional[float] = None,
    ttm_eps: Optional[float] = None,
    single_eps: Optional[float] = None,
    ttm_revenue: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {
        "pe_ratio": None,
        "pb_ratio": None,
        "ps_ratio": None,
        "pcf_ratio": None,
        "peg_ratio": None,
    }

    if not _is_valid_positive(current_price):
        return result

    eps = (
        ttm_eps
        if ttm_eps is not None
        else (
            single_eps
            if single_eps is not None
            else to_float(
                data.get(
                    "basic_earnings_loss_per_share_from_continuing_and_discontinued_operations"
                )
            )
        )
    )
    if _is_valid_number(eps) and eps != 0:
        result["pe_ratio"] = round(current_price / eps, 4)

    if not _is_valid_positive(shares_outstanding):
        shares_outstanding = compute_shares_outstanding(data)
    if not _is_valid_positive(shares_outstanding):
        return result

    reserves_excl_reval = to_float(data.get("reserve_excluding_revaluation_reserves"))
    share_capital = to_float(data.get("paid_up_value_of_equity_share_capital"))
    if _is_valid_number(reserves_excl_reval) and _is_valid_number(share_capital):
        equity = share_capital + reserves_excl_reval
    else:
        equity = (to_float(data.get("equity_share_capital")) or 0) + (
            to_float(data.get("other_equity")) or 0
        )
    bvps = safe_div(equity, shares_outstanding)
    if _is_valid_positive(bvps):
        result["pb_ratio"] = round(current_price / bvps, 4)

    revenue = (
        ttm_revenue
        if ttm_revenue is not None
        else to_float(data.get("revenue_from_operations"))
    )
    sps = safe_div(revenue, shares_outstanding)
    if _is_valid_positive(sps):
        result["ps_ratio"] = round(current_price / sps, 4)

    ocf = to_float(data.get("cash_flows_from_used_in_operating_activities"))
    cfps = safe_div(ocf, shares_outstanding)
    if _is_valid_positive(cfps):
        result["pcf_ratio"] = round(current_price / cfps, 4)

    pe = result["pe_ratio"]
    if _is_valid_positive(pe) and _is_valid_number(eps_growth) and eps_growth > 0:
        result["peg_ratio"] = round(pe / eps_growth, 4)

    return result


def _is_valid_number(v: Any) -> bool:
    if v is None:
        return False
    try:
        f = float(v)
        return not (math.isnan(f) or math.isinf(f))
    except (ValueError, TypeError):
        return False


def _is_valid_positive(v: Any) -> bool:
    if not _is_valid_number(v):
        return False
    return float(v) != 0


VALUATION_METRICS: List[Dict[str, Any]] = [
    {"id": "pe_ratio", "name": "Price to Earnings (P/E)", "type": "multiple"},
    {"id": "pb_ratio", "name": "Price to Book Value (P/BV)", "type": "multiple"},
    {"id": "ps_ratio", "name": "Price to Sales (P/S)", "type": "multiple"},
    {"id": "pcf_ratio", "name": "Price to Cash Flow (P/CF)", "type": "multiple"},
    {"id": "peg_ratio", "name": "PEG Ratio", "type": "multiple"},
]


def get_valuation_catalog() -> Dict[str, Any]:
    return {
        "id": "ratio_valuation",
        "name": "Valuation Ratios",
        "type": "ratio",
        "metrics": VALUATION_METRICS,
    }
