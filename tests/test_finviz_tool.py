import json

import pytest

from tools import finviz_tool as fv
from utils.resilience import ToolError


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(fv, "_cache", fv.DiskCache("finviz", 3600, root=tmp_path))
    monkeypatch.setattr(fv, "_last_filters", None)


# ── Chei ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, key", [
    ("P/FCF", "Price/Free Cash Flow"),
    ("ROIC", "Return on Investment"),
    ("roe", "Return on Equity"),
    ("EPS growth next 5 years", "EPS growthnext 5 years"),
    ("Sales growth past 5 years", "Sales growthpast 5 years"),
    ("Insider Transactions", "InsiderTransactions"),
    ("Institutional Ownership", "InstitutionalOwnership"),
    ("market cap", "Market Cap."),
    ("LT Debt/Equity", "LT Debt/Equity"),
    ("P/E", "P/E"),
])
def test_resolve_key_aliases(raw, key):
    assert fv.resolve_key(raw) == key


def test_resolve_key_unknown_suggests():
    with pytest.raises(ToolError) as exc:
        fv.resolve_key("Price/Free Cashflow ratio")
    assert "Price/Free Cash Flow" in exc.value.hint


# ── Valori ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key, raw, value", [
    ("LT Debt/Equity", "<0.5", "Under 0.5"),
    ("LT Debt/Equity", "under 0.5", "Under 0.5"),
    ("P/E", "Under 20", "Under 20"),
    ("P/E", "< 20", "Under 20"),
    ("Return on Equity", "Over 15%", "Over +15%"),
    ("Return on Equity", ">15%", "Over +15%"),
    ("EPS growthnext 5 years", ">15%", "Over 15%"),
    ("Market Cap.", "+Mid (over $2bln)", "+Mid (over $2bln)"),
])
def test_resolve_value(key, raw, value):
    assert fv.resolve_value(key, raw) == value


def test_resolve_value_invalid_lists_options():
    with pytest.raises(ToolError) as exc:
        fv.resolve_value("PEG", "Under 1.5")
    assert "Under 1" in exc.value.hint and "Under 2" in exc.value.hint


def test_resolve_filters_drops_any_and_validates_type():
    assert fv.resolve_filters({"P/E": "Any", "PEG": "<2"}) == {"PEG": "Under 2"}
    with pytest.raises(ToolError):
        fv.resolve_filters(["P/E"])


def test_build_filter_param_is_sorted_and_coded():
    param = fv.build_filter_param({"PEG": "Under 2", "P/E": "Under 25"})
    assert param == "fa_pe_u25,fa_peg_u2"


def test_valid_options_hide_truncated_labels():
    assert all(o.count("(") == o.count(")") for o in fv.valid_options("Return on Equity"))


# ── Guidance ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("count, prefix", [
    (0, "PREA PUȚINE"), (2, "PREA PUȚINE"), (3, "APROAPE"), (5, "ȚINTĂ"), (12, "ȚINTĂ"),
    (20, "APROAPE"), (30, "APROAPE"), (31, "PREA MULTE"),
])
def test_guidance_thresholds(count, prefix):
    assert fv.guidance(count).startswith(prefix)


# ── Parsare JSON ─────────────────────────────────────────────────────────────

SAMPLE_ROW = {"text": {"ticker": "GOOG", "company": "Alphabet Inc", "sector": "Communication Services",
                       "industry": "Internet Content & Information", "country": "USA"},
              "numbers": {"marketCap": 3957451.75, "PE": 16.1514, "PFCF": 74.28, "ltdebtEq": 0.17939,
                          "insiderTrans": -0.000176}}


def test_map_row():
    row = fv._map_row(SAMPLE_ROW)
    assert row["ticker"] == "GOOG"
    assert row["mcap_bln"] == 3957.45
    assert row["pe"] == 16.15
    assert row["lt_debt_eq"] == 0.18
    assert row["insider_trans"] == -0.0002
    assert row["peg"] is None


def test_parse_json_island():
    from bs4 import BeautifulSoup
    props = {"data": {"rows": [SAMPLE_ROW], "totalCount": 290}}
    html = (f'<script data-ssr-props="x" type="application/json">{{"other": 1}}</script>'
            f'<script data-ssr-props="ssr-island-1" type="application/json">{json.dumps(props)}</script>')
    parsed = fv._parse_json_island(BeautifulSoup(html, "html.parser"))
    assert parsed["count"] == 290 and parsed["rows"][0]["ticker"] == "GOOG"


def test_parse_json_island_missing():
    from bs4 import BeautifulSoup
    assert fv._parse_json_island(BeautifulSoup("<html></html>", "html.parser")) is None


# ── Tool-ul (fetch simulat) ──────────────────────────────────────────────────

def _fake_fetch(count):
    calls = {"n": 0}

    def fetch(filters):
        calls["n"] += 1
        return {"count": count, "rows": [fv._map_row(SAMPLE_ROW)] * min(count, 20), "parser": "json"}
    return fetch, calls


def test_tool_success_and_cache(monkeypatch):
    fetch, calls = _fake_fetch(9)
    monkeypatch.setattr(fv, "_fetch", fetch)
    out = json.loads(fv.finviz_dynamic_screener({"P/E": "<25", "ROIC": ">15%"}, angle="GARP"))
    assert out["ok"] and out["count"] == 9 and out["guidance"].startswith("ȚINTĂ")
    assert out["filters_applied"] == {"P/E": "Under 25", "Return on Investment": "Over +15%"}
    assert len(out["rows"]) == 9
    fv.finviz_dynamic_screener({"ROIC": ">15%", "P/E": "<25"})
    assert calls["n"] == 1  # a doua oară din cache (ordinea cheilor nu contează)


def test_tool_samples_rows_when_too_many(monkeypatch):
    fetch, _ = _fake_fetch(290)
    monkeypatch.setattr(fv, "_fetch", fetch)
    out = json.loads(fv.finviz_dynamic_screener({"P/E": "<25"}))
    assert out["count"] == 290 and len(out["rows"]) == fv.SAMPLE_ROWS


def test_tool_invalid_filter_returns_error_json():
    out = json.loads(fv.finviz_dynamic_screener({"PEG": "Under 1.5"}))
    assert out["ok"] is False and "Under 1" in out["hint"]


def test_filter_catalog_compact():
    text = fv.filter_catalog()
    assert "Price/Free Cash Flow: Under {5,10" in text
    assert len(text) < 8000


# ── Live ─────────────────────────────────────────────────────────────────────

@pytest.mark.live
def test_live_garp_returns_real_rows():
    out = json.loads(fv.finviz_dynamic_screener(
        {"Market Cap.": "+Mid (over $2bln)", "P/E": "<25", "PEG": "<2", "LT Debt/Equity": "<0.5",
         "P/FCF": "<20", "ROE": ">15%"}, angle="live GARP"))
    assert out["ok"], out
    assert out["count"] > 0
    row = out["rows"][0]
    assert row["ticker"] and isinstance(row["pe"], float) and row["pe"] < 25
    assert row["lt_debt_eq"] is None or row["lt_debt_eq"] < 0.5


@pytest.mark.live
def test_live_impossible_filters_give_few():
    out = json.loads(fv.finviz_dynamic_screener(
        {"Market Cap.": "Mega ($200bln and more)", "P/E": "Under 5", "Return on Equity": "Over +50%"}))
    assert out["ok"] and out["count"] <= 2 and out["guidance"].startswith("PREA PUȚINE")
