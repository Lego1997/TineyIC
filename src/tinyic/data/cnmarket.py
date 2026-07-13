"""China-market (A-share / HK) fundamentals via akshare -- an OPTIONAL extra.

akshare is heavy (~16 transitive deps incl. a JS engine), so it is an
optional dependency (``uv sync --extra cn``) and is lazy-imported inside
:func:`fetch_cn_market_data`.  When it is missing the fetcher degrades to
``None`` and the pipeline surfaces a warning -- never a hard dependency.

Only EastMoney datacenter/F10-backed akshare functions are used (they are
reachable internationally, unlike the push2 quote endpoints which are
geo-blocked from some overseas IPs).  Quotes remain yfinance's job; this
module deliberately fetches no quote data.

Each sub-fetch (statements, ratios, valuation, consensus, profile) is wrapped
independently with try/except and a bounded timeout so partial data survives.
Field names emitted downstream are English (language-neutral prompts); the
original Chinese label is preserved in ``source_labels`` where ambiguous.
"""

import importlib.util
import logging
import re
import threading
from datetime import datetime
from typing import Callable, Optional

from .models import (
    CNAnalystConsensus,
    CNMarketData,
    CNRatios,
    CNStatements,
    CNValuation,
)

logger = logging.getLogger(__name__)

# Bounded wall-clock budget per sub-fetch (a sub-fetch may make up to three
# akshare calls, e.g. the three statements).
_FETCH_TIMEOUT_S = 60.0

_DATE_KEYS = ("REPORT_DATE", "日期", "数据日期")


# --------------------------------------------------------------------------- #
# Market detection and symbol conversion
# --------------------------------------------------------------------------- #

def detect_cn_market(ticker: str) -> Optional[str]:
    """Classify a yfinance-style ticker: ``a_share`` | ``hk`` | None.

    ``600519.SS`` / ``000001.SZ`` -> ``a_share``; ``0700.HK`` -> ``hk``;
    anything else (including non-numeric codes like ``BRK.B``) -> None.
    """
    symbol = (ticker or "").strip().upper()
    if "." not in symbol:
        return None
    code, _, suffix = symbol.rpartition(".")
    if not code.isdigit():
        return None
    if suffix in ("SS", "SZ") and len(code) == 6:
        return "a_share"
    if suffix == "HK" and 1 <= len(code) <= 5:
        return "hk"
    return None


def _a_share_symbols(ticker: str) -> tuple[str, str]:
    """``600519.SS`` -> (``SH600519``, ``600519``); ``.SZ`` -> ``SZ`` prefix."""
    symbol = ticker.strip().upper()
    code, _, suffix = symbol.rpartition(".")
    prefix = "SH" if suffix == "SS" else "SZ"
    return f"{prefix}{code}", code


def _hk_symbol(ticker: str) -> str:
    """``0700.HK`` -> zero-padded 5-digit ``00700``."""
    code, _, _suffix = ticker.strip().upper().rpartition(".")
    return code.zfill(5)


def cn_market_dependency_missing() -> bool:
    """True when the optional ``akshare`` extra is not installed."""
    try:
        return importlib.util.find_spec("akshare") is None
    except (ImportError, ValueError):
        # A module object is present in sys.modules with an odd/missing
        # __spec__ (e.g. a test stub) -- treat as installed.
        return False


# --------------------------------------------------------------------------- #
# Small dataframe/value helpers (duck-typed; no pandas import needed)
# --------------------------------------------------------------------------- #

def _call_with_timeout(func: Callable, timeout_s: float = _FETCH_TIMEOUT_S):
    """Run a blocking akshare call with a bounded wall-clock budget.

    akshare exposes no timeout knobs, so the call runs on a short-lived
    daemon worker joined with a timeout; a hung DNS/TLS handshake then cannot
    stall the pipeline (or interpreter exit) indefinitely.
    """
    outcome: dict = {}

    def _runner() -> None:
        try:
            outcome["value"] = func()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller
            outcome["error"] = exc

    worker = threading.Thread(
        target=_runner, name="tinyic-cn-market-fetch", daemon=True
    )
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise TimeoutError(f"akshare call exceeded {timeout_s:.0f}s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _records(df) -> list[dict]:
    """DataFrame -> list of row dicts; [] for None/empty."""
    if df is None or getattr(df, "empty", True):
        return []
    return df.to_dict("records")


def _to_float(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.replace(",", "").replace("%", "").strip()
        if not text or text in {"-", "--", "nan", "None"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _to_int(value) -> Optional[int]:
    number = _to_float(value)
    return int(number) if number is not None else None


def _period_label(value) -> Optional[str]:
    """``2026-03-31 00:00:00`` -> ``2026-03-31``; None for blanks/NaN."""
    text = "" if value is None else str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text[:10]


def _latest_record(records: list[dict]) -> Optional[dict]:
    """Row with the max date-ish column (ISO strings compare lexically)."""
    def _key(record: dict) -> str:
        for date_key in _DATE_KEYS:
            value = record.get(date_key)
            if value is not None and str(value).strip():
                return str(value)
        return ""

    dated = [record for record in records if _key(record)]
    if not dated:
        return records[-1] if records else None
    return max(dated, key=_key)


def _pick(record: dict, candidates: tuple[str, ...]):
    """(value, matched column) for the first candidate: exact, then substring."""
    for candidate in candidates:
        if candidate in record:
            return record[candidate], candidate
    for candidate in candidates:
        for key in record:
            if candidate in str(key):
                return record[key], str(key)
    return None, None


def _map_record(record: dict, field_map: dict, labels: dict) -> dict:
    """Map source columns to English keys; keep non-ASCII labels alongside."""
    values: dict = {}
    for english, candidates in field_map.items():
        raw, matched = _pick(record, candidates)
        number = _to_float(raw)
        if number is None:
            continue
        values[english] = number
        if matched and any(ord(ch) > 127 for ch in matched):
            labels[english] = matched
    return values


def _add_margins(income: dict) -> None:
    """Derive gross/net margins (percent) from mapped income line items."""
    revenue = income.get("total_revenue")
    if not revenue:
        return
    gross_profit = income.get("gross_profit")
    operating_cost = income.get("operating_cost")
    if gross_profit is not None:
        income["gross_margin_pct"] = round(gross_profit / revenue * 100, 2)
    elif operating_cost is not None:
        income["gross_margin_pct"] = round(
            (revenue - operating_cost) / revenue * 100, 2
        )
    net = income.get("net_profit_attributable")
    if net is None:
        net = income.get("net_profit")
    if net is not None:
        income["net_margin_pct"] = round(net / revenue * 100, 2)


# --------------------------------------------------------------------------- #
# A-share sub-fetches (EastMoney report endpoints; wide format)
# --------------------------------------------------------------------------- #

_A_INCOME_FIELDS = {
    "total_revenue": ("TOTAL_OPERATE_INCOME", "OPERATE_INCOME"),
    "operating_cost": ("OPERATE_COST", "TOTAL_OPERATE_COST"),
    "operating_profit": ("OPERATE_PROFIT",),
    "total_profit": ("TOTAL_PROFIT",),
    "net_profit": ("NETPROFIT",),
    "net_profit_attributable": ("PARENT_NETPROFIT",),
    "basic_eps": ("BASIC_EPS",),
}
_A_BALANCE_FIELDS = {
    "total_assets": ("TOTAL_ASSETS",),
    "total_liabilities": ("TOTAL_LIABILITIES",),
    "total_equity": ("TOTAL_EQUITY",),
    "equity_attributable": ("TOTAL_PARENT_EQUITY",),
}
_A_CASHFLOW_FIELDS = {
    "operating_cash_flow": ("NETCASH_OPERATE",),
    "investing_cash_flow": ("NETCASH_INVEST",),
    "financing_cash_flow": ("NETCASH_FINANCE",),
}
_A_RATIO_FIELDS = {
    "eps": ("摊薄每股收益",),
    "roe_pct": ("净资产收益率",),
    "gross_margin_pct": ("销售毛利率",),
    "net_margin_pct": ("销售净利率",),
    "debt_to_assets_pct": ("资产负债率",),
    "revenue_growth_pct": ("主营业务收入增长率",),
    "net_profit_growth_pct": ("净利润增长率",),
}


def _fetch_a_share_statements(ak, em_symbol: str) -> Optional[CNStatements]:
    labels: dict = {}
    income_rec = _latest_record(
        _records(ak.stock_profit_sheet_by_report_em(symbol=em_symbol))
    )
    balance_rec = _latest_record(
        _records(ak.stock_balance_sheet_by_report_em(symbol=em_symbol))
    )
    cash_rec = _latest_record(
        _records(ak.stock_cash_flow_sheet_by_report_em(symbol=em_symbol))
    )
    income = _map_record(income_rec or {}, _A_INCOME_FIELDS, labels)
    balance = _map_record(balance_rec or {}, _A_BALANCE_FIELDS, labels)
    cash_flow = _map_record(cash_rec or {}, _A_CASHFLOW_FIELDS, labels)
    _add_margins(income)
    if not (income or balance or cash_flow):
        return None
    period = None
    for record in (income_rec, balance_rec, cash_rec):
        if record:
            period = _period_label(_pick(record, ("REPORT_DATE",))[0])
            if period:
                break
    return CNStatements(
        period=period,
        currency="CNY",
        income=income,
        balance=balance,
        cash_flow=cash_flow,
        source_labels=labels,
    )


def _fetch_a_share_ratios(ak, bare_code: str) -> Optional[CNRatios]:
    start_year = str(datetime.now().year - 3)
    records = _records(
        ak.stock_financial_analysis_indicator(
            symbol=bare_code, start_year=start_year
        )
    )
    record = _latest_record(records)
    if not record:
        return None
    labels: dict = {}
    ratios = _map_record(record, _A_RATIO_FIELDS, labels)
    if not ratios:
        return None
    period = _period_label(_pick(record, ("日期",))[0])
    return CNRatios(period=period, ratios=ratios, source_labels=labels)


def _fetch_a_share_valuation(ak, bare_code: str) -> Optional[CNValuation]:
    record = _latest_record(_records(ak.stock_value_em(symbol=bare_code)))
    if not record:
        return None
    pe_ttm = _to_float(_pick(record, ("PE(TTM)",))[0])
    pb = _to_float(_pick(record, ("市净率",))[0])
    market_cap = _to_float(_pick(record, ("总市值",))[0])
    if pe_ttm is None and pb is None and market_cap is None:
        return None
    as_of = _period_label(_pick(record, ("数据日期",))[0])
    return CNValuation(as_of=as_of, pe_ttm=pe_ttm, pb=pb, market_cap=market_cap)


_A_RATING_TERMS = (
    ("buy", "买入"),
    ("overweight", "增持"),
    ("neutral", "中性"),
    ("underweight", "减持"),
    ("sell", "卖出"),
)
_EPS_COLUMN = re.compile(r"(\d{4}).*每股收益")


def _fetch_a_share_consensus(ak, bare_code: str) -> Optional[CNAnalystConsensus]:
    records = _records(ak.stock_profit_forecast_em())
    row = next(
        (
            record
            for record in records
            if str(record.get("代码") or record.get("股票代码") or "").strip()
            == bare_code
        ),
        None,
    )
    if row is None:
        return None
    ratings: dict = {}
    for english, term in _A_RATING_TERMS:
        count = _to_int(_pick(row, (term,))[0])
        if count is not None:
            ratings[english] = count
    consensus_eps: dict = {}
    for key, value in row.items():
        match = _EPS_COLUMN.search(str(key))
        if match:
            number = _to_float(value)
            if number is not None:
                consensus_eps[match.group(1)] = number
    report_count = _to_int(_pick(row, ("研报数",))[0])
    if not ratings and not consensus_eps and report_count is None:
        return None
    return CNAnalystConsensus(
        report_count=report_count, ratings=ratings, consensus_eps=consensus_eps
    )


# --------------------------------------------------------------------------- #
# HK sub-fetches (EastMoney F10; statements are LONG format item/value rows)
# --------------------------------------------------------------------------- #

_HK_INCOME_ITEMS = {
    "total_revenue": ("营业额", "营运收入", "营业收入", "总收入"),
    "gross_profit": ("毛利",),
    "operating_profit": ("经营溢利", "营业利润"),
    "pretax_profit": ("除税前溢利", "除税前利润", "税前利润"),
    "net_profit": ("除税后溢利", "净利润"),
    "net_profit_attributable": ("股东应占溢利", "归母净利润"),
    "basic_eps": ("基本每股收益", "每股基本盈利", "基本每股盈利"),
}
_HK_BALANCE_ITEMS = {
    "total_assets": ("总资产", "资产总计"),
    "total_liabilities": ("总负债", "负债总计"),
    "equity_attributable": ("归属母公司股东权益", "股东权益(不含少数股东权益)"),
    "total_equity": ("股东权益合计", "总权益", "股东权益"),
}
_HK_CASHFLOW_ITEMS = {
    "operating_cash_flow": (
        "经营业务现金净额",
        "经营活动产生的现金流量净额",
        "经营活动现金流量净额",
    ),
    "investing_cash_flow": ("投资业务现金净额", "投资活动产生的现金流量净额"),
    "financing_cash_flow": ("融资业务现金净额", "筹资活动产生的现金流量净额"),
}
_HK_RATIO_FIELDS = {
    "eps": ("BASIC_EPS",),
    "roe_pct": ("ROE_AVG", "ROE_YEARLY"),
    "gross_margin_pct": ("GROSS_PROFIT_RATIO",),
    "net_margin_pct": ("NET_PROFIT_RATIO",),
    "debt_to_assets_pct": ("DEBT_ASSET_RATIO",),
    "revenue": ("OPERATE_INCOME",),
    "revenue_growth_pct": ("OPERATE_INCOME_YOY",),
    "net_profit": ("HOLDER_PROFIT",),
    "net_profit_growth_pct": ("HOLDER_PROFIT_YOY",),
}
_HK_STATEMENTS = (
    ("income", "利润表", _HK_INCOME_ITEMS),
    ("balance", "资产负债表", _HK_BALANCE_ITEMS),
    ("cash_flow", "现金流量表", _HK_CASHFLOW_ITEMS),
)


def _map_hk_items(rows: list[dict], item_map: dict, labels: dict) -> dict:
    """Map long-format (item name, amount) rows to English keys.

    Exact item-name matches win; substring matches never let a plain
    candidate (e.g. ``毛利``) grab a ratio row (e.g. ``毛利率``).
    """
    named: list[tuple[str, float]] = []
    for row in rows:
        name = str(
            row.get("STD_ITEM_NAME") or row.get("ITEM_NAME") or ""
        ).strip()
        if not name:
            continue
        amount = _to_float(row.get("AMOUNT"))
        if amount is None:
            continue
        named.append((name, amount))

    values: dict = {}
    for english, candidates in item_map.items():
        match = None
        for candidate in candidates:
            match = next(
                ((name, amount) for name, amount in named if name == candidate),
                None,
            )
            if match:
                break
        if match is None:
            for candidate in candidates:
                match = next(
                    (
                        (name, amount)
                        for name, amount in named
                        if candidate in name
                        and ("率" in candidate or "率" not in name)
                    ),
                    None,
                )
                if match:
                    break
        if match:
            values[english] = match[1]
            labels[english] = match[0]
    return values


def _fetch_hk_statements(ak, hk_code: str) -> Optional[CNStatements]:
    labels: dict = {}
    sections: dict = {"income": {}, "balance": {}, "cash_flow": {}}
    period = None
    currency = None
    for attribute, sheet_name, item_map in _HK_STATEMENTS:
        records = _records(
            ak.stock_financial_hk_report_em(
                stock=hk_code, symbol=sheet_name, indicator="年度"
            )
        )
        if not records:
            continue
        latest_date = max(
            (str(record.get("REPORT_DATE") or "") for record in records),
            default="",
        )
        latest_rows = [
            record
            for record in records
            if str(record.get("REPORT_DATE") or "") == latest_date
        ]
        period = period or _period_label(latest_date)
        if currency is None:
            for record in latest_rows:
                raw = record.get("CURRENCY")
                if raw is not None and str(raw).strip():
                    currency = str(raw).strip()
                    break
        sections[attribute] = _map_hk_items(latest_rows, item_map, labels)
    income = sections["income"]
    _add_margins(income)
    if not (income or sections["balance"] or sections["cash_flow"]):
        return None
    return CNStatements(
        period=period,
        currency=currency,
        income=income,
        balance=sections["balance"],
        cash_flow=sections["cash_flow"],
        source_labels=labels,
    )


def _fetch_hk_ratios(ak, hk_code: str) -> Optional[CNRatios]:
    records = _records(
        ak.stock_financial_hk_analysis_indicator_em(
            symbol=hk_code, indicator="年度"
        )
    )
    record = _latest_record(records)
    if not record:
        return None
    labels: dict = {}
    ratios = _map_record(record, _HK_RATIO_FIELDS, labels)
    if not ratios:
        return None
    period = _period_label(_pick(record, ("REPORT_DATE",))[0])
    return CNRatios(period=period, ratios=ratios, source_labels=labels)


_HK_PROFILE_COLUMNS = ("公司介绍", "公司简介", "主营业务")
_PROFILE_MAX_CHARS = 800


def _fetch_hk_profile(ak, hk_code: str) -> Optional[str]:
    records = _records(ak.stock_hk_company_profile_em(symbol=hk_code))
    if not records:
        return None
    first = records[0]
    for column in _HK_PROFILE_COLUMNS:
        raw, _ = _pick(first, (column,))
        text = "" if raw is None else str(raw).strip()
        if text and text.lower() != "nan":
            return text[:_PROFILE_MAX_CHARS]
    # Two-column item/value layout: find the profile-ish row.
    keys = list(first.keys())
    if len(keys) == 2:
        for record in records:
            label = str(record.get(keys[0]) or "")
            if any(term in label for term in ("介绍", "简介", "主营")):
                text = str(record.get(keys[1]) or "").strip()
                if text and text.lower() != "nan":
                    return text[:_PROFILE_MAX_CHARS]
    return None


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def fetch_cn_market_data(
    ticker: str, company_name: str
) -> Optional[CNMarketData]:
    """Fetch A-share/HK fundamentals for a CN/HK ticker; None otherwise.

    Detects the market from the ticker suffix (``.SS``/``.SZ`` = A-share,
    ``.HK`` = HK; anything else returns None immediately).  akshare is
    lazy-imported; if missing this logs and returns None.  Each sub-fetch
    degrades independently and failures are recorded in
    ``CNMarketData.failed_sections``.
    """
    market = detect_cn_market(ticker)
    if market is None:
        return None

    try:
        import akshare as ak  # heavy optional extra -- keep the import lazy
    except ImportError:
        logger.warning(
            "akshare not installed; skipping China market data for %s (%s). "
            "Install the cn extra: uv sync --extra cn",
            ticker,
            company_name,
        )
        return None

    failed_sections: list[str] = []

    def _safe(section: str, func: Callable):
        try:
            return _call_with_timeout(func)
        except Exception as exc:
            logger.warning(
                "China market %s fetch failed for %s: %s", section, ticker, exc
            )
            failed_sections.append(section)
            return None

    if market == "a_share":
        em_symbol, bare_code = _a_share_symbols(ticker)
        statements = _safe(
            "statements", lambda: _fetch_a_share_statements(ak, em_symbol)
        )
        ratios = _safe("ratios", lambda: _fetch_a_share_ratios(ak, bare_code))
        valuation = _safe(
            "valuation", lambda: _fetch_a_share_valuation(ak, bare_code)
        )
        consensus = _safe(
            "consensus", lambda: _fetch_a_share_consensus(ak, bare_code)
        )
        profile = None
    else:
        hk_code = _hk_symbol(ticker)
        statements = _safe(
            "statements", lambda: _fetch_hk_statements(ak, hk_code)
        )
        ratios = _safe("ratios", lambda: _fetch_hk_ratios(ak, hk_code))
        valuation = None
        consensus = None
        profile = _safe("profile", lambda: _fetch_hk_profile(ak, hk_code))

    if all(
        section is None
        for section in (statements, ratios, valuation, consensus, profile)
    ):
        logger.warning("China market data: no sections available for %s", ticker)
        return None

    return CNMarketData(
        market=market,
        statements=statements,
        ratios=ratios,
        valuation=valuation,
        consensus=consensus,
        profile=profile,
        failed_sections=failed_sections,
    )
