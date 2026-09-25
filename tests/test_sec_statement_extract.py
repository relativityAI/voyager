"""Guards on SEC statement-frame extraction.

Both regressions here shipped silently: the batched stitch merged rows by
RangeIndex instead of concept (blending unrelated line items into one row), and
capex/D&A were never mapped, so every valuation metric that depends on them
came back null.
"""

import pandas as pd

from src.services import sec

DA = "us-gaap_Depreciation"
DA_INTANGIBLES = "us-gaap_AmortizationOfIntangibleAssets"
CAPEX = "us-gaap_PaymentsToAcquirePropertyPlantAndEquipment"
OCF = "us-gaap_NetCashProvidedByUsedInOperatingActivities"


def _frame(rows: dict) -> pd.DataFrame:
    """A cash-flow frame shaped like edgartools' to_dataframe() output."""
    return pd.DataFrame(
        [
            {"label": t, "concept": t, "2025-09-30": v, "2025-06-30": v2}
            for t, (v, v2) in rows.items()
        ],
        columns=["label", "concept", "2025-09-30", "2025-06-30"],
    )


def test_merge_keeps_concepts_apart_across_batches():
    # Each batch is its own frame with a plain RangeIndex, so both start at
    # row 0 with different concepts. Grouping by index welded them together.
    a = _frame({OCF: (9_153_000_000.0, 6_071_000_000.0)})
    b = _frame({CAPEX: (-709_000_000.0, -454_000_000.0)})

    merged = sec._merge_frames([a, b])

    # Both concepts survive with their own values; neither absorbed the other.
    assert set(merged["concept"]) == {OCF, CAPEX}
    assert sec._lookup_value(
        merged, sec._CASHFLOW_MAP,
        "cash_flows_from_used_in_operating_activities", "2025-09-30",
    ) == 9_153_000_000.0
    assert sec._lookup_value(
        merged, sec._CASHFLOW_MAP,
        "payments_for_purchase_of_noncurrent_assets", "2025-09-30",
    ) == -709_000_000.0


def test_merge_keeps_both_periods_when_a_concept_spans_batches():
    merged = sec._merge_frames([
        _frame({OCF: (9_153_000_000.0, float("nan"))}),
        _frame({OCF: (float("nan"), 6_071_000_000.0)}),
    ])
    assert sec._lookup_value(
        merged, sec._CASHFLOW_MAP,
        "cash_flows_from_used_in_operating_activities", "2025-06-30",
    ) == 6_071_000_000.0


def test_capex_is_read_from_cashflow_frame():
    cash = _frame({CAPEX: (-709_000_000.0, -454_000_000.0)})
    rows = sec._cashflow_rows("IBM", cash, ["2025-09-30"], "10-Q", False)
    assert rows[0]["payments_for_purchase_of_noncurrent_assets"] == -709_000_000.0


def test_depreciation_and_amortisation_are_summed_from_cashflow_frame():
    # US filers tag D&A in the cash-flow statement, split across two concepts,
    # and the income statement carries neither.
    cash = _frame({DA: (1_088_000_000.0, 900_000_000.0),
                   DA_INTANGIBLES: (1_535_000_000.0, 1_200_000_000.0)})
    income = pd.DataFrame(
        [{"label": "Revenue", "concept": "us-gaap_RevenueFromContractWithCustomerExcludingAssessedTax",
          "2025-09-30": 16_330_000_000.0, "2025-06-30": 16_978_000_000.0}],
        columns=["label", "concept", "2025-09-30", "2025-06-30"],
    )
    rows = sec._income_rows("IBM", income, ["2025-09-30"], "10-Q", False, cash)
    assert rows[0]["depreciation_depletion_and_amortisation_expense"] == 1_088_000_000.0 + 1_535_000_000.0
