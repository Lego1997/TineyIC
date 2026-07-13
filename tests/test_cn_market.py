"""China-market (A-share / HK) data source tests.

akshare is an OPTIONAL extra and is fully stubbed here via sys.modules
injection -- this file must pass with akshare absent from the environment.
No test touches the network.
"""

import importlib.machinery
import re
import sys
import time
import tomllib
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import tinyic.debate as debate_module
from tinyic.data.cnmarket import (
    _a_share_symbols,
    _call_with_timeout,
    _hk_symbol,
    cn_market_dependency_missing,
    detect_cn_market,
    fetch_cn_market_data,
)
from tinyic.data.models import (
    CNMarketData,
    CNStatements,
    DataPackage,
)
from tinyic.data.pipeline import build_data_package
from tinyic.events import EventEnvelope, EventLog, read_event_log


DEBATE_ID = "aapl-20260713-a3f2"
FIXED_NOW = datetime(2026, 7, 13, 1, 2, 3, tzinfo=timezone.utc)

MISSING_EXTRA_WARNING = (
    "China market data disabled: install the cn extra (uv sync --extra cn)"
)


def _stub_akshare(monkeypatch, **functions) -> types.ModuleType:
    """Install a fake ``akshare`` module for the duration of a test."""
    module = types.ModuleType("akshare")
    module.__spec__ = importlib.machinery.ModuleSpec("akshare", loader=None)
    for name, function in functions.items():
        setattr(module, name, function)
    monkeypatch.setitem(sys.modules, "akshare", module)
    return module


# --------------------------------------------------------------------------- #
# Market detection and symbol conversion
# --------------------------------------------------------------------------- #

class TestMarketDetection:
    @pytest.mark.parametrize(
        ("ticker", "expected"),
        [
            ("600519.SS", "a_share"),
            ("000001.SZ", "a_share"),
            ("600519.ss", "a_share"),
            (" 0700.HK ", "hk"),
            ("9988.HK", "hk"),
            ("AAPL", None),
            ("SAP.DE", None),
            ("7203.T", None),
            ("BRK.B", None),  # non-numeric code
            ("MC.PA", None),
            ("", None),
        ],
    )
    def test_detect_matrix(self, ticker, expected):
        assert detect_cn_market(ticker) == expected

    def test_a_share_symbol_conversion(self):
        assert _a_share_symbols("600519.SS") == ("SH600519", "600519")
        assert _a_share_symbols("000001.SZ") == ("SZ000001", "000001")

    def test_hk_symbol_zero_pads_to_five_digits(self):
        assert _hk_symbol("0700.HK") == "00700"
        assert _hk_symbol("9988.HK") == "09988"

    def test_non_cn_ticker_returns_none_immediately(self, monkeypatch):
        # Poison the import so any akshare import attempt would raise.
        monkeypatch.setitem(sys.modules, "akshare", None)
        assert fetch_cn_market_data("AAPL", "Apple Inc.") is None

    def test_missing_akshare_returns_none_not_raises(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", None)  # import -> ImportError
        assert fetch_cn_market_data("600519.SS", "Kweichow Moutai") is None

    def test_dependency_probe(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "akshare", None)
        assert cn_market_dependency_missing() is True
        _stub_akshare(monkeypatch)
        assert cn_market_dependency_missing() is False


# --------------------------------------------------------------------------- #
# Timeout discipline
# --------------------------------------------------------------------------- #

class TestCallWithTimeout:
    def test_returns_value(self):
        assert _call_with_timeout(lambda: 42, timeout_s=5) == 42

    def test_propagates_exceptions(self):
        with pytest.raises(ValueError, match="boom"):
            _call_with_timeout(
                lambda: (_ for _ in ()).throw(ValueError("boom")), timeout_s=5
            )

    def test_times_out(self):
        with pytest.raises(TimeoutError):
            _call_with_timeout(lambda: time.sleep(0.5), timeout_s=0.05)


# --------------------------------------------------------------------------- #
# A-share normalization (wide-format EastMoney statements)
# --------------------------------------------------------------------------- #

def _a_share_profit_df() -> pd.DataFrame:
    # *_by_yearly_em frames: one wide row per FISCAL YEAR (annual reports only).
    return pd.DataFrame(
        [
            {
                "SECUCODE": "600519.SH",
                "REPORT_DATE": "2024-12-31 00:00:00",
                "TOTAL_OPERATE_INCOME": 150_000_000_000.0,
                "OPERATE_COST": 12_000_000_000.0,
                "OPERATE_PROFIT": 104_000_000_000.0,
                "TOTAL_PROFIT": 103_000_000_000.0,
                "NETPROFIT": 78_000_000_000.0,
                "PARENT_NETPROFIT": 76_000_000_000.0,
                "BASIC_EPS": 60.1,
            },
            {
                "SECUCODE": "600519.SH",
                "REPORT_DATE": "2025-12-31 00:00:00",
                "TOTAL_OPERATE_INCOME": 174_000_000_000.0,
                "OPERATE_COST": 14_000_000_000.0,
                "OPERATE_PROFIT": 120_000_000_000.0,
                "TOTAL_PROFIT": 119_000_000_000.0,
                "NETPROFIT": 90_000_000_000.0,
                "PARENT_NETPROFIT": 88_000_000_000.0,
                "BASIC_EPS": 68.64,
            },
        ]
    )


def _a_share_balance_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "REPORT_DATE": "2025-12-31 00:00:00",
                "TOTAL_ASSETS": 300_000_000_000.0,
                "TOTAL_LIABILITIES": 60_000_000_000.0,
                "TOTAL_EQUITY": 240_000_000_000.0,
                "TOTAL_PARENT_EQUITY": 235_000_000_000.0,
            }
        ]
    )


def _a_share_cash_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "REPORT_DATE": "2025-12-31 00:00:00",
                "NETCASH_OPERATE": 12_000_000_000.0,
                "NETCASH_INVEST": -1_000_000_000.0,
                "NETCASH_FINANCE": -20_000_000_000.0,
            }
        ]
    )


def _a_share_indicator_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "日期": "2024-12-31",
                "摊薄每股收益(元)": 60.1,
                "净资产收益率(%)": 32.1,
                "销售毛利率(%)": 91.5,
                "销售净利率(%)": 51.0,
                "资产负债率(%)": 21.3,
                "主营业务收入增长率(%)": 17.2,
                "净利润增长率(%)": 18.1,
            },
            {
                "日期": "2025-12-31",
                "摊薄每股收益(元)": 68.64,
                "净资产收益率(%)": 34.2,
                "销售毛利率(%)": 91.8,
                "销售净利率(%)": 52.3,
                "资产负债率(%)": 20.1,
                "主营业务收入增长率(%)": 15.7,
                "净利润增长率(%)": 14.9,
            },
        ]
    )


def _a_share_value_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "数据日期": "2026-07-10",
                "PE(TTM)": 21.5,
                "市净率": 7.9,
                "总市值": 1_900_000_000_000.0,
            },
            {
                "数据日期": "2026-07-11",
                "PE(TTM)": 21.8,
                "市净率": 8.0,
                "总市值": 1_920_000_000_000.0,
            },
        ]
    )


def _a_share_forecast_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": "000001",
                "名称": "平安银行",
                "研报数": 12,
                "机构投资评级(近六个月)-买入": 5,
                "机构投资评级(近六个月)-增持": 6,
                "机构投资评级(近六个月)-中性": 1,
                "机构投资评级(近六个月)-减持": 0,
                "机构投资评级(近六个月)-卖出": 0,
                "2026预测每股收益": 2.31,
                "2027预测每股收益": 2.52,
            },
            {
                "代码": "600519",
                "名称": "贵州茅台",
                "研报数": 31,
                "机构投资评级(近六个月)-买入": 26,
                "机构投资评级(近六个月)-增持": 4,
                "机构投资评级(近六个月)-中性": 1,
                "机构投资评级(近六个月)-减持": 0,
                "机构投资评级(近六个月)-卖出": 0,
                "2026预测每股收益": 75.3,
                "2027预测每股收益": 83.1,
            },
        ]
    )


def _install_a_share_stub(monkeypatch, overrides: dict | None = None):
    functions = {
        "stock_profit_sheet_by_yearly_em": lambda symbol: _a_share_profit_df(),
        "stock_balance_sheet_by_yearly_em": lambda symbol: _a_share_balance_df(),
        "stock_cash_flow_sheet_by_yearly_em": lambda symbol: _a_share_cash_df(),
        "stock_financial_analysis_indicator": (
            lambda symbol, start_year: _a_share_indicator_df()
        ),
        "stock_value_em": lambda symbol: _a_share_value_df(),
        "stock_profit_forecast_em": lambda: _a_share_forecast_df(),
    }
    functions.update(overrides or {})
    return _stub_akshare(monkeypatch, **functions)


class TestAShareNormalization:
    def test_full_fetch_wide_format(self, monkeypatch):
        seen_symbols: list[str] = []

        def profit(symbol):
            seen_symbols.append(symbol)
            return _a_share_profit_df()

        _install_a_share_stub(
            monkeypatch, {"stock_profit_sheet_by_yearly_em": profit}
        )
        result = fetch_cn_market_data("600519.SS", "Kweichow Moutai")

        assert result is not None
        assert result.market == "a_share"
        assert result.failed_sections == []
        assert seen_symbols == ["SH600519"]  # yfinance form converted for akshare

        # Statements: latest fiscal year wins, English keys, margins derived.
        statements = result.statements
        assert statements.period == "2025-12-31"
        assert statements.currency == "CNY"
        assert statements.income["total_revenue"] == 174_000_000_000.0
        assert statements.income["net_profit_attributable"] == 88_000_000_000.0
        assert statements.income["gross_margin_pct"] == pytest.approx(91.95)
        assert statements.income["net_margin_pct"] == pytest.approx(50.57)
        assert statements.balance["total_assets"] == 300_000_000_000.0
        assert statements.balance["total_liabilities"] == 60_000_000_000.0
        assert statements.cash_flow["operating_cash_flow"] == 12_000_000_000.0
        # Raw EastMoney column dump must be trimmed to the mapped subset.
        assert "SECUCODE" not in statements.income

        # Ratios: latest Sina row, Chinese labels preserved alongside.
        assert result.ratios.period == "2025-12-31"
        assert result.ratios.ratios["roe_pct"] == 34.2
        assert result.ratios.ratios["gross_margin_pct"] == 91.8
        assert "净资产收益率" in result.ratios.source_labels["roe_pct"]

        # Valuation: most recent row of the time series.
        assert result.valuation.as_of == "2026-07-11"
        assert result.valuation.pe_ttm == 21.8
        assert result.valuation.pb == 8.0
        assert result.valuation.market_cap == 1_920_000_000_000.0

        # Consensus: the per-stock row is filtered by bare code.
        assert result.consensus.report_count == 31
        assert result.consensus.ratings["buy"] == 26
        assert result.consensus.ratings["sell"] == 0
        assert result.consensus.consensus_eps == {"2026": 75.3, "2027": 83.1}

    def test_sub_fetch_failures_degrade_independently(self, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("Sina TLS handshake failed")

        _install_a_share_stub(
            monkeypatch,
            {
                "stock_financial_analysis_indicator": broken,
                "stock_profit_forecast_em": broken,
            },
        )
        result = fetch_cn_market_data("600519.SS", "Kweichow Moutai")

        assert result is not None
        assert result.statements is not None
        assert result.valuation is not None
        assert result.ratios is None
        assert result.consensus is None
        assert sorted(result.failed_sections) == ["consensus", "ratios"]

    def test_all_sections_failing_returns_none(self, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("endpoint unreachable")

        _stub_akshare(
            monkeypatch,
            stock_profit_sheet_by_yearly_em=broken,
            stock_balance_sheet_by_yearly_em=broken,
            stock_cash_flow_sheet_by_yearly_em=broken,
            stock_financial_analysis_indicator=broken,
            stock_value_em=broken,
            stock_profit_forecast_em=broken,
        )
        assert fetch_cn_market_data("000001.SZ", "Ping An Bank") is None

    def test_empty_dataframes_return_none(self, monkeypatch):
        empty = lambda *args, **kwargs: pd.DataFrame()  # noqa: E731
        _stub_akshare(
            monkeypatch,
            stock_profit_sheet_by_yearly_em=empty,
            stock_balance_sheet_by_yearly_em=empty,
            stock_cash_flow_sheet_by_yearly_em=empty,
            stock_financial_analysis_indicator=empty,
            stock_value_em=empty,
            stock_profit_forecast_em=empty,
        )
        assert fetch_cn_market_data("600519.SS", "Kweichow Moutai") is None

    def test_operating_cost_falls_back_when_first_column_is_null(
        self, monkeypatch
    ):
        """Wide EM frames keep every column; present-but-NaN first candidates
        (e.g. OPERATE_COST for securities/insurance company types) must fall
        through to the next candidate instead of dropping the field."""
        df = _a_share_profit_df()
        df["OPERATE_COST"] = None  # all-null column, as EM emits it
        df["TOTAL_OPERATE_COST"] = [30_000_000_000.0, 34_800_000_000.0]
        _install_a_share_stub(
            monkeypatch, {"stock_profit_sheet_by_yearly_em": lambda symbol: df}
        )
        result = fetch_cn_market_data("600519.SS", "Kweichow Moutai")

        income = result.statements.income
        assert income["operating_cost"] == 34_800_000_000.0
        # Margin derivation runs off the fallback column, not silently skipped.
        assert income["gross_margin_pct"] == pytest.approx(80.0)

    def test_ratios_start_year_walks_forward_for_recent_ipos(self, monkeypatch):
        """Sina returns an EMPTY frame when start_year predates the stock's
        year list (recent IPOs); the fetcher must retry with later years."""
        seen_years: list[str] = []
        year = datetime.now().year

        def indicator(symbol, start_year):
            seen_years.append(start_year)
            if start_year == str(year - 3):
                return pd.DataFrame()  # start_year not in the page's year list
            return _a_share_indicator_df()

        _install_a_share_stub(
            monkeypatch, {"stock_financial_analysis_indicator": indicator}
        )
        result = fetch_cn_market_data("600519.SS", "Kweichow Moutai")

        assert seen_years == [str(year - 3), str(year - 1)]
        assert result.failed_sections == []
        assert result.ratios is not None
        assert result.ratios.ratios["roe_pct"] == 34.2

    def test_ratios_empty_after_all_retries_is_a_failed_section(
        self, monkeypatch
    ):
        """Persistently-empty indicator frames must surface as a failed
        section (degraded warning), not silently vanish with status ok."""
        seen_years: list[str] = []

        def indicator(symbol, start_year):
            seen_years.append(start_year)
            return pd.DataFrame()

        _install_a_share_stub(
            monkeypatch, {"stock_financial_analysis_indicator": indicator}
        )
        result = fetch_cn_market_data("600519.SS", "Kweichow Moutai")

        year = datetime.now().year
        assert seen_years == [str(year - 3), str(year - 1), str(year)]
        assert result is not None  # other sections still present
        assert result.ratios is None
        assert "ratios" in result.failed_sections


# --------------------------------------------------------------------------- #
# HK normalization (long-format item/value statements)
# --------------------------------------------------------------------------- #

def _hk_report_rows(sheet: str) -> pd.DataFrame:
    if sheet == "利润表":
        items = [
            ("营业额", 660_000_000_000.0),
            ("毛利", 320_000_000_000.0),
            ("毛利率", 48.5),  # ratio trap: must not be picked as 毛利
            ("经营溢利", 208_000_000_000.0),
            ("除税前溢利", 230_000_000_000.0),
            ("股东应占溢利", 194_000_000_000.0),
            ("基本每股收益", 21.0),
        ]
    elif sheet == "资产负债表":
        items = [
            ("总资产", 1_800_000_000_000.0),
            ("总负债", 800_000_000_000.0),
            ("归属母公司股东权益", 950_000_000_000.0),
            ("股东权益合计", 1_000_000_000_000.0),
        ]
    else:  # 现金流量表
        items = [
            ("经营业务现金净额", 260_000_000_000.0),
            ("投资业务现金净额", -90_000_000_000.0),
            ("融资业务现金净额", -60_000_000_000.0),
        ]
    # Real column set of stock_financial_hk_report_em (akshare 1.18.64):
    # SECUCODE, SECURITY_CODE, SECURITY_NAME_ABBR, ORG_CODE, REPORT_DATE,
    # DATE_TYPE_CODE, FISCAL_YEAR, START_DATE/STD_REPORT_DATE, STD_ITEM_CODE,
    # STD_ITEM_NAME, AMOUNT.  There is NO currency column.
    rows = []
    for report_date, scale in (("2025-12-31 00:00:00", 1.0), ("2024-12-31 00:00:00", 0.9)):
        for index, (name, amount) in enumerate(items):
            rows.append(
                {
                    "SECUCODE": "00700.HK",
                    "SECURITY_CODE": "00700",
                    "REPORT_DATE": report_date,
                    "FISCAL_YEAR": report_date[:4],
                    "STD_ITEM_CODE": f"00{index:02d}",
                    "STD_ITEM_NAME": name,
                    "AMOUNT": amount * scale,
                }
            )
    return pd.DataFrame(rows)


def _hk_indicator_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "SECUCODE": "00700.HK",
                "REPORT_DATE": "2025-12-31 00:00:00",
                "BASIC_EPS": 21.0,
                "ROE_AVG": 21.9,
                "GROSS_PROFIT_RATIO": 48.5,
                "NET_PROFIT_RATIO": 29.4,
                "DEBT_ASSET_RATIO": 44.3,
                "OPERATE_INCOME": 660_000_000_000.0,
                "OPERATE_INCOME_YOY": 8.2,
                "HOLDER_PROFIT": 194_000_000_000.0,
                "HOLDER_PROFIT_YOY": 68.4,
            },
            {
                "SECUCODE": "00700.HK",
                "REPORT_DATE": "2024-12-31 00:00:00",
                "BASIC_EPS": 12.5,
                "ROE_AVG": 14.1,
                "GROSS_PROFIT_RATIO": 46.0,
                "NET_PROFIT_RATIO": 21.0,
                "DEBT_ASSET_RATIO": 45.0,
                "OPERATE_INCOME": 610_000_000_000.0,
                "OPERATE_INCOME_YOY": 7.8,
                "HOLDER_PROFIT": 115_000_000_000.0,
                "HOLDER_PROFIT_YOY": 10.2,
            },
        ]
    )


def _hk_profile_df() -> pd.DataFrame:
    return pd.DataFrame(
        [{"公司名称": "腾讯控股有限公司", "公司介绍": "腾讯是一家互联网综合服务提供商。" * 100}]
    )


def _install_hk_stub(monkeypatch, overrides: dict | None = None):
    functions = {
        "stock_financial_hk_report_em": (
            lambda stock, symbol, indicator: _hk_report_rows(symbol)
        ),
        "stock_financial_hk_analysis_indicator_em": (
            lambda symbol, indicator: _hk_indicator_df()
        ),
        "stock_hk_company_profile_em": lambda symbol: _hk_profile_df(),
    }
    functions.update(overrides or {})
    return _stub_akshare(monkeypatch, **functions)


class TestHKNormalization:
    def test_long_format_statements(self, monkeypatch):
        seen: list[tuple[str, str, str]] = []

        def report(stock, symbol, indicator):
            seen.append((stock, symbol, indicator))
            return _hk_report_rows(symbol)

        _install_hk_stub(
            monkeypatch, {"stock_financial_hk_report_em": report}
        )
        result = fetch_cn_market_data("0700.HK", "Tencent Holdings")

        assert result is not None
        assert result.market == "hk"
        assert result.failed_sections == []
        # akshare gets the zero-padded 5-digit code and annual statements.
        assert {entry[0] for entry in seen} == {"00700"}
        assert {entry[1] for entry in seen} == {"利润表", "资产负债表", "现金流量表"}
        assert {entry[2] for entry in seen} == {"年度"}

        statements = result.statements
        assert statements.period == "2025-12-31"  # latest FY only
        # The source has no currency column (HK issuers report in HKD, CNY,
        # or USD); the absence must be explicit, never a guessed label.
        assert statements.currency is None
        assert statements.currency_note is not None
        for token in ("HKD", "CNY", "USD"):
            assert token in statements.currency_note
        assert statements.income["total_revenue"] == 660_000_000_000.0
        assert statements.income["gross_profit"] == 320_000_000_000.0
        assert statements.income["net_profit_attributable"] == 194_000_000_000.0
        assert statements.income["gross_margin_pct"] == pytest.approx(48.48)
        assert statements.balance["total_assets"] == 1_800_000_000_000.0
        assert statements.balance["equity_attributable"] == 950_000_000_000.0
        assert statements.cash_flow["operating_cash_flow"] == 260_000_000_000.0
        # The 毛利率 (ratio) row must not shadow the 毛利 (gross profit) item.
        assert statements.income["gross_profit"] != 48.5
        # Original Chinese labels ride alongside the English keys.
        assert statements.source_labels["total_revenue"] == "营业额"
        assert statements.source_labels["operating_cash_flow"] == "经营业务现金净额"

        assert result.ratios.period == "2025-12-31"
        assert result.ratios.ratios["roe_pct"] == 21.9
        assert result.ratios.ratios["net_margin_pct"] == 29.4
        assert result.ratios.ratios["revenue"] == 660_000_000_000.0

        assert result.valuation is None  # A-share only sections
        assert result.consensus is None
        assert result.profile is not None
        assert len(result.profile) <= 800
        assert result.profile.startswith("腾讯")

    def test_hk_partial_failure(self, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("F10 endpoint 500")

        _install_hk_stub(
            monkeypatch, {"stock_hk_company_profile_em": broken}
        )
        result = fetch_cn_market_data("0700.HK", "Tencent Holdings")

        assert result is not None
        assert result.profile is None
        assert result.failed_sections == ["profile"]
        assert result.statements is not None
        assert result.ratios is not None

    def test_hk_roe_falls_back_when_first_column_is_null(self, monkeypatch):
        """ROE_AVG present-but-null (e.g. a first-year listing) must fall
        through to ROE_YEARLY instead of dropping roe_pct."""
        df = _hk_indicator_df()
        df["ROE_AVG"] = None  # all-null column, as EM emits it
        df["ROE_YEARLY"] = [23.4, 15.0]
        _install_hk_stub(
            monkeypatch,
            {
                "stock_financial_hk_analysis_indicator_em": (
                    lambda symbol, indicator: df
                )
            },
        )
        result = fetch_cn_market_data("0700.HK", "Tencent Holdings")

        assert result.ratios is not None
        assert result.ratios.ratios["roe_pct"] == 23.4


# --------------------------------------------------------------------------- #
# Pipeline wiring (source status, warnings, prompt integration)
# --------------------------------------------------------------------------- #

def _build_package(ticker: str, company: str, cn_result, dependency_missing: bool):
    with (
        patch(
            "tinyic.data.pipeline.resolve_ticker",
            return_value=(True, ticker, company),
        ),
        patch("tinyic.data.pipeline._fetch_description", return_value=None),
        patch("tinyic.data.pipeline.fetch_financials", return_value=None),
        patch("tinyic.data.pipeline.fetch_filings", return_value=None),
        patch("tinyic.data.pipeline.fetch_news", return_value=None),
        patch("tinyic.data.pipeline.fetch_social_sentiment", return_value=None),
        patch(
            "tinyic.data.pipeline.fetch_cn_market_data", return_value=cn_result
        ) as cn_mock,
        patch(
            "tinyic.data.pipeline.cn_market_dependency_missing",
            return_value=dependency_missing,
        ),
    ):
        package = build_data_package(ticker, deep_research=False)
    return package, cn_mock


def _cn_source(payload: dict):
    return next(
        (source for source in payload["sources"] if source["name"] == "cn_market"),
        None,
    )


class TestPipelineCNWiring:
    def test_non_cn_ticker_source_does_not_appear(self):
        package, cn_mock = _build_package("AAPL", "Apple Inc.", None, True)

        cn_mock.assert_not_called()
        assert package.cn_market is None
        assert not any("China market" in warning for warning in package.warnings)

        payload = debate_module._data_ready_payload(package)
        assert _cn_source(payload) is None
        # The always-applicable sources are still all present.
        names = [source["name"] for source in payload["sources"]]
        assert names == [
            "financials", "filing_10k", "filing_10q", "news", "social", "research",
        ]

    def test_cn_ticker_missing_extra_is_unavailable_with_warning(self):
        package, cn_mock = _build_package(
            "600519.SS", "Kweichow Moutai", None, True
        )

        cn_mock.assert_called_once_with("600519.SS", "Kweichow Moutai")
        assert MISSING_EXTRA_WARNING in package.warnings

        source = _cn_source(debate_module._data_ready_payload(package))
        assert source is not None
        # A missing pip extra is NOT a credential problem: schema v1 statuses
        # are frozen, so this must map to "unavailable" + warning text.
        assert source["status"] == "unavailable"
        assert source["warning"] == MISSING_EXTRA_WARNING

    def test_cn_ticker_fetch_failure_is_unavailable(self):
        package, _ = _build_package("600519.SS", "Kweichow Moutai", None, False)

        assert "China market data unavailable" in package.warnings
        source = _cn_source(debate_module._data_ready_payload(package))
        assert source["status"] == "unavailable"

    def test_cn_ticker_success_is_ok_and_reaches_prompt_context(self):
        cn_result = CNMarketData(
            market="a_share",
            statements=CNStatements(
                period="2026-03-31",
                currency="CNY",
                income={"total_revenue": 46_000_000_000.0, "net_margin_pct": 56.52},
            ),
        )
        package, _ = _build_package(
            "600519.SS", "Kweichow Moutai", cn_result, False
        )

        assert package.cn_market is cn_result
        assert not any("China market" in warning for warning in package.warnings)

        source = _cn_source(debate_module._data_ready_payload(package))
        assert source["status"] == "ok"
        assert "warning" not in source

        # Prompt integration: the persona-facing context string carries the
        # CN data with English keys and the report-period label.
        context = package.to_context_string()
        assert "cn_market" in context
        assert "total_revenue" in context
        assert "2026-03-31" in context

    def test_cn_ticker_partial_sections_is_degraded(self):
        cn_result = CNMarketData(
            market="hk",
            statements=CNStatements(period="2025-12-31"),
            failed_sections=["ratios", "profile"],
        )
        package, _ = _build_package("0700.HK", "Tencent Holdings", cn_result, False)

        assert (
            "China market data partial: ratios, profile unavailable"
            in package.warnings
        )
        source = _cn_source(debate_module._data_ready_payload(package))
        assert source["status"] == "degraded"
        assert "partial" in source["warning"]

    def test_package_serialization_round_trip(self):
        package = DataPackage(
            ticker="0700.HK",
            company_name="Tencent Holdings",
            fetched_at=FIXED_NOW,
            cn_market=CNMarketData(
                market="hk",
                statements=CNStatements(
                    period="2025-12-31",
                    income={"total_revenue": 660_000_000_000.0},
                    source_labels={"total_revenue": "营业额"},
                ),
                profile="腾讯是一家互联网综合服务提供商。",
            ),
        )
        restored = DataPackage.model_validate_json(package.model_dump_json())
        assert restored.cn_market.market == "hk"
        assert restored.cn_market.statements.income["total_revenue"] == (
            660_000_000_000.0
        )
        assert restored.cn_market.statements.source_labels["total_revenue"] == "营业额"


# --------------------------------------------------------------------------- #
# Event contract: cn_market as a data_ready source (schema v1, additive-only)
# --------------------------------------------------------------------------- #

def _started_payload() -> dict:
    return {
        "ticker": "600519.SS",
        "company_name": "Kweichow Moutai",
        "preset": "default",
        "personas": [
            {
                "name": "Warren Buffett",
                "model_ref": "openai/gpt-5.2",
                "auth_profile": "openai:default",
                "thinking_level": "xhigh",
                "temperament": "independent",
            },
        ],
        "moderator": "rules",
        "aggregator": "openai/gpt-5.2",
        "caps": {"opening": 1, "cross_exam": 2, "rebuttal": 1, "verdict": 1},
        "config_hash": "sha256:test-config",
        "tinyic_version": "0.1.0",
    }


def _cn_data_ready_payload() -> dict:
    return {
        "sources": [
            {"name": "financials", "status": "ok"},
            {
                "name": "cn_market",
                "status": "unavailable",
                "warning": MISSING_EXTRA_WARNING,
            },
        ],
        "financials_summary": {"pe_ratio": {"value": 21.8, "unit": "x"}},
        "description": "Chinese baijiu producer.",
        "fetched_at": "2026-07-13T01:02:03.000Z",
    }


class TestCNMarketEventContract:
    def test_envelope_validates_and_round_trips(self):
        raw = {
            "v": 1,
            "seq": 2,
            "ts": "2026-07-13T01:02:03.000Z",
            "debate_id": DEBATE_ID,
            "type": "data_ready",
            "payload": _cn_data_ready_payload(),
        }
        event = EventEnvelope.model_validate(raw)
        assert EventEnvelope.model_validate(event.model_dump()).model_dump() == (
            event.model_dump()
        )
        source = _cn_source(event.payload)
        assert source["status"] == "unavailable"

        # "degraded" (partial CN data) is equally valid for the new source.
        degraded = dict(raw)
        degraded["payload"] = {
            **_cn_data_ready_payload(),
            "sources": [
                {
                    "name": "cn_market",
                    "status": "degraded",
                    "warning": "China market data partial: consensus unavailable",
                }
            ],
        }
        assert EventEnvelope.model_validate(degraded).payload["sources"][0][
            "status"
        ] == "degraded"

    def test_event_log_fixture_round_trip(self, tmp_path: Path):
        log = EventLog(
            debate_id=DEBATE_ID,
            path=tmp_path / f"{DEBATE_ID}.jsonl",
            clock=lambda: FIXED_NOW,
        )
        log.emit("debate_started", _started_payload())
        log.emit("data_ready", _cn_data_ready_payload())

        events = read_event_log(log.path)
        assert [event.type for event in events] == ["debate_started", "data_ready"]
        source = _cn_source(events[1].payload)
        assert source is not None
        assert source["status"] == "unavailable"
        assert source["warning"] == MISSING_EXTRA_WARNING


# --------------------------------------------------------------------------- #
# Packaging: the documented install command must actually work
# --------------------------------------------------------------------------- #

class TestCNExtraPackaging:
    def test_workspace_root_defines_cn_extra(self):
        """``uv sync --extra cn`` resolves extras against the workspace ROOT
        project, not the tinyic member -- so the exact command baked into the
        pipeline warning / module docstring must be declared at the root and
        forwarded to the member that owns the akshare dependency."""
        root = Path(__file__).resolve().parents[1]

        root_project = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        )
        assert root_project["project"]["optional-dependencies"]["cn"] == (
            ["tinyic[cn]"]
        )

        member = tomllib.loads(
            (root / "src" / "tinyic" / "pyproject.toml").read_text(
                encoding="utf-8"
            )
        )
        cn_deps = member["project"]["optional-dependencies"]["cn"]
        assert any(dep.startswith("akshare") for dep in cn_deps)


# --------------------------------------------------------------------------- #
# Live smoke tier (network + the cn extra; run: uv run pytest -m live_api)
# --------------------------------------------------------------------------- #

class TestLiveCNMarket:
    """Pins the real EastMoney/Sina response shapes that the offline stubs
    only echo back (wide/long column names, Chinese labels, akshare function
    signatures).  akshare renames columns across minor versions; this tier
    catches that drift instead of letting every section silently degrade to
    ``failed_sections`` in production.
    """

    @pytest.mark.live_api
    @pytest.mark.timeout(300)
    def test_live_a_share_moutai(self):
        pytest.importorskip("akshare")
        result = fetch_cn_market_data("600519.SS", "Kweichow Moutai")

        assert result is not None
        assert result.market == "a_share"
        # Statements + valuation are EastMoney datacenter/F10 endpoints
        # (reachable internationally) -- hard-assert the mapped English keys.
        assert result.statements is not None, result.failed_sections
        assert result.statements.currency == "CNY"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", result.statements.period)
        assert result.statements.income.get("total_revenue")
        assert result.statements.income.get("net_profit_attributable")
        assert result.statements.balance.get("total_assets")
        assert result.statements.cash_flow.get("operating_cash_flow")
        assert result.valuation is not None, result.failed_sections
        assert (
            result.valuation.pe_ttm is not None
            or result.valuation.pb is not None
            or result.valuation.market_cap is not None
        )
        # Sina ratios and the whole-market consensus crawl are best-effort
        # (TLS trouble seen from some overseas exits), but when they do come
        # back they must carry mapped keys.
        if result.ratios is not None:
            assert result.ratios.ratios
        if result.consensus is not None:
            assert (
                result.consensus.report_count is not None
                or result.consensus.ratings
                or result.consensus.consensus_eps
            )

    @pytest.mark.live_api
    @pytest.mark.timeout(300)
    def test_live_hk_tencent(self):
        pytest.importorskip("akshare")
        result = fetch_cn_market_data("0700.HK", "Tencent Holdings")

        assert result is not None
        assert result.market == "hk"
        assert result.statements is not None, result.failed_sections
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", result.statements.period)
        assert result.statements.income.get("total_revenue")
        assert result.statements.income.get("net_profit_attributable")
        assert result.statements.balance.get("total_assets")
        # The source has no currency column: absence stays explicit.
        assert result.statements.currency is None
        assert result.statements.currency_note
        assert result.ratios is not None, result.failed_sections
        assert result.ratios.ratios.get("roe_pct") is not None
        assert result.profile
