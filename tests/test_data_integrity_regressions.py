"""Red acceptance tests for review defect classes B6-B7."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from numbers import Real
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tinyic.data.filings import _extract_sections, fetch_filings
from tinyic.data.financials import fetch_financials
from tinyic.data.models import DataPackage, FinancialData


def _substantive(text: str, repetitions: int = 30) -> str:
    return (text + " ") * repetitions


# ---------------------------------------------------------------------------
# B6: SEC filing section extraction must ignore tables of contents
# ---------------------------------------------------------------------------


def test_b6_10k_toc_entries_do_not_mask_real_sections() -> None:
    markdown = "\n".join(
        [
            "# Table of Contents",
            "Item 1. Business ................................ 3",
            "Item 1A. Risk Factors .......................... 10",
            "Item 7. Management's Discussion and Analysis .. 40",
            "# Item 1. Business",
            "REAL_BUSINESS_BODY_SENTINEL "
            + _substantive("Products customers operations and competition."),
            "# Item 1A. Risk Factors",
            "REAL_RISK_BODY_SENTINEL "
            + _substantive("Competition regulation supply chains and litigation."),
            "# Item 7. Management's Discussion and Analysis",
            "REAL_MDA_BODY_SENTINEL "
            + _substantive("Revenue margins liquidity and cash flow discussion."),
        ]
    )

    sections = _extract_sections(
        markdown,
        section_keys=["Item 1", "Item 1A", "Item 7"],
        max_per_section=1000,
    )
    extracted = "\n\n".join(sections)

    assert len(sections) == 3
    assert "REAL_BUSINESS_BODY_SENTINEL" in extracted
    assert "REAL_RISK_BODY_SENTINEL" in extracted
    assert "REAL_MDA_BODY_SENTINEL" in extracted
    assert "................................" not in extracted


@patch("edgar.set_identity")
@patch("edgar.Company")
def test_b6_10q_uses_part_one_sections_without_overlap_or_final_truncation(
    company_cls, _set_identity
) -> None:
    markdown = "\n".join(
        [
            "# Table of Contents",
            "Part I. Financial Information .................. 3",
            "Item 1. Financial Statements ................... 3",
            "Item 2. Management's Discussion and Analysis ... 8",
            "Part II. Other Information ..................... 20",
            "Item 1. Legal Proceedings ...................... 20",
            "# Part I. Financial Information",
            "# Item 1. Financial Statements",
            "REAL_FINANCIAL_STATEMENTS_SENTINEL "
            + _substantive("Balance sheet income statement and cash flows."),
            "# Item 2. Management's Discussion and Analysis",
            "REAL_QUARTERLY_MDA_SENTINEL "
            + _substantive("Quarterly revenue margins liquidity and outlook."),
            "# Part II. Other Information",
            "# Item 1. Legal Proceedings",
            "PART_TWO_LEGAL_PROCEEDINGS_SENTINEL "
            + _substantive("Claims proceedings and legal contingencies."),
        ]
    )
    filing = MagicMock()
    filing.filing_date = "2026-06-30"
    filing.html.return_value = None
    filing.markdown.return_value = markdown
    filings = MagicMock()
    filings.__bool__.return_value = True
    filings.latest.return_value = filing
    company_cls.return_value.get_filings.return_value = filings

    result = fetch_filings("AAPL", "10-Q")

    assert result is not None
    assert result.text_summary is not None
    assert len(result.text_summary) <= 2000
    assert "REAL_FINANCIAL_STATEMENTS_SENTINEL" in result.text_summary
    assert "REAL_QUARTERLY_MDA_SENTINEL" in result.text_summary
    assert "PART_TWO_LEGAL_PROCEEDINGS_SENTINEL" not in result.text_summary
    assert "................" not in result.text_summary


# ---------------------------------------------------------------------------
# B7: normalize and label financial units before LLM context construction
# ---------------------------------------------------------------------------


def _mock_yfinance_ticker(info: dict) -> MagicMock:
    ticker = MagicMock()
    ticker.info = info
    ticker.income_stmt = pd.DataFrame()
    ticker.balance_sheet = pd.DataFrame()
    return ticker


@patch("yfinance.Ticker")
def test_b7_yfinance_debt_to_equity_percent_is_normalized_to_ratio(
    ticker_cls,
) -> None:
    # Exact value from the review and the repository's AAPL fixture: yfinance
    # reports 178.7 percent, which is approximately 1.787 as a ratio.
    ticker_cls.return_value = _mock_yfinance_ticker(
        {
            "debtToEquity": 178.7,
            "dividendYield": 0.0055,
            "currency": "USD",
        }
    )

    result = fetch_financials("AAPL")

    assert result is not None
    assert result.debt_to_equity == pytest.approx(1.787)


@pytest.mark.parametrize(
    ("raw_yield", "expected_source_unit"),
    [
        (0.0055, "fraction"),
        (0.55, "percent"),
    ],
)
@patch("yfinance.Ticker")
def test_b7_dividend_yield_convention_is_detected_normalized_and_labeled(
    ticker_cls, raw_yield: float, expected_source_unit: str
) -> None:
    # dividendRate/currentPrice corroborates a normalized yield of 0.0055,
    # allowing the two yfinance conventions to be distinguished deterministically.
    ticker_cls.return_value = _mock_yfinance_ticker(
        {
            "dividendYield": raw_yield,
            "dividendRate": 1.10,
            "currentPrice": 200.0,
            "currency": "USD",
        }
    )

    result = fetch_financials("AAPL")

    assert result is not None
    assert result.dividend_yield == pytest.approx(0.0055)
    assert result.dividend_yield_source_unit == expected_source_unit


def _assert_numeric_values_have_sibling_units(node, path: str = "financials") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            child_path = f"{path}.{key}"
            if isinstance(value, Real) and not isinstance(value, bool):
                assert "unit" in node, f"{child_path} is numeric but has no unit"
            else:
                _assert_numeric_values_have_sibling_units(value, child_path)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_numeric_values_have_sibling_units(value, f"{path}[{index}]")


def test_b7_every_structured_financial_number_in_llm_context_has_units() -> None:
    financials = FinancialData(
        pe_ratio=28.5,
        profit_margin=0.246,
        roe=1.56,
        debt_to_equity=1.787,
        market_cap=2_800_000_000_000,
        revenue=394_328_000_000,
        dividend_yield=0.0055,
        income_summary={"Total Revenue": 394_328_000_000},
        balance_summary={"Total Debt": 111_088_000_000},
        currency="USD",
        dividend_yield_source_unit="fraction",
    )
    package = DataPackage(
        ticker="AAPL",
        company_name="Apple Inc.",
        fetched_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        financials=financials,
    )

    context = json.loads(package.to_context_string())
    context_financials = context["financials"]

    assert context_financials["pe_ratio"] == {"value": 28.5, "unit": "x"}
    assert context_financials["profit_margin"] == {
        "value": 0.246,
        "unit": "ratio",
    }
    assert context_financials["debt_to_equity"] == {
        "value": pytest.approx(1.787),
        "unit": "ratio",
    }
    assert context_financials["market_cap"] == {
        "value": 2_800_000_000_000,
        "unit": "USD",
    }
    assert context_financials["dividend_yield"] == {
        "value": pytest.approx(0.0055),
        "unit": "ratio",
        "source_unit": "fraction",
    }
    assert context_financials["income_summary"]["Total Revenue"] == {
        "value": 394_328_000_000,
        "unit": "USD",
    }
    assert context_financials["balance_summary"]["Total Debt"] == {
        "value": 111_088_000_000,
        "unit": "USD",
    }
    _assert_numeric_values_have_sibling_units(context_financials)
