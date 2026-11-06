import json

import pandas as pd
import pytest

from tools import fundamentals_tool as ft
from utils.resilience import ToolError

M = 1e6  # valorile din yfinance sunt în unități, nu milioane


def make_raw(years=(2022, 2023, 2024, 2025), sector="Technology", pe=20.0, ttm=None, **series):
    """Construiește un `raw` ca cel din _fetch_raw; seriile se dau ca liste în milioane."""
    defaults = {
        "revenue": [100, 110, 121, 133], "op_income": [20, 23, 26, 29], "net_income": [15, 17, 19, 21],
        "unusual": [0, 0, 0, 0], "fcf": [14, 16, 18, 20], "ocf": [None] * 4, "capex": [-5, -5, -6, -6],
        "total_debt": [30, 28, 26, 24], "cash": [40, 45, 50, 55], "shares": [100, 99, 98, 97],
    }
    defaults.update(series)
    return {
        "ticker": "TEST",
        "info": {"longName": "Test Corp", "sector": sector, "industry": "Software", "trailingPE": pe,
                 "financialCurrency": "USD"},
        "series": {m: {str(y): (None if v is None else v * M) for y, v in zip(years, vals) if v is not None}
                   for m, vals in defaults.items()},
        "ttm": ttm or {"unusual": None, "net_income": None},
    }


def flags(result):
    return " | ".join(result["red_flags"])


# ── Firme sănătoase ──────────────────────────────────────────────────────────

def test_healthy_company_passes():
    r = ft.analyze(make_raw())
    assert r["verdict"] == "PASS", r["red_flags"]
    assert r["years"] == [2022, 2023, 2024, 2025]
    assert r["series"]["revenue"] == [100, 110, 121, 133]
    assert r["ratios"]["revenue_cagr_pct"] == pytest.approx(10.0, abs=0.1)
    assert r["ratios"]["net_debt_mln"] == -31
    assert any("Cash net" in g for g in r["green_flags"])
    assert any("buybacks" in g for g in r["green_flags"])


def test_keeps_only_last_4_years():
    years = (2020, 2021, 2022, 2023, 2024)
    raw = make_raw(years=years, **{m: [1, 2, 3, 4, 5] for m in
                                   ("revenue", "op_income", "net_income", "fcf", "total_debt", "cash", "shares")},
                   unusual=[0] * 5, ocf=[None] * 5, capex=[0] * 5)
    assert ft.analyze(raw)["years"] == [2021, 2022, 2023, 2024]


# ── Cele 8 euristici ─────────────────────────────────────────────────────────

def test_burning_cash():
    r = ft.analyze(make_raw(fcf=[5, -3, -4, -2]))
    assert r["verdict"] == "FAIL" and "ARDE BANI" in flags(r)


def test_paper_profit_cumulative():
    r = ft.analyze(make_raw(net_income=[15, 17, 19, 21], fcf=[5, 6, 7, 8]))
    assert r["verdict"] == "FAIL" and "PROFIT DE HÂRTIE" in flags(r)


def test_paper_profit_latest_year_only_is_medium():
    r = ft.analyze(make_raw(net_income=[15, 17, 19, 40], fcf=[14, 16, 18, 20]))
    assert r["verdict"] == "WARN" and "PROFITUL NU AJUNGE ÎN BANCĂ" in flags(r)


def test_one_off_profit_annual():
    r = ft.analyze(make_raw(unusual=[0, 0, 0, 12]))
    assert r["verdict"] == "FAIL" and "ONE-OFF" in flags(r)


def test_one_off_profit_ttm():
    r = ft.analyze(make_raw(ttm={"unusual": 9 * M, "net_income": 20 * M}))
    assert "ONE-OFF" in flags(r) and "trimestre" in flags(r)


def test_one_off_ignored_when_profit_negative():
    r = ft.analyze(make_raw(ttm={"unusual": 9 * M, "net_income": -20 * M}))
    assert "ONE-OFF" not in flags(r)


def test_low_pe_lies():
    r = ft.analyze(make_raw(pe=8.0, fcf=[20, 18, 16, 14]))
    assert "P/E-UL MINTE" in flags(r)


def test_shrinking_business():
    r = ft.analyze(make_raw(revenue=[133, 121, 110, 100]))
    assert "CONTRACȚIE" in flags(r)


def test_eroding_margins():
    r = ft.analyze(make_raw(op_income=[30, 25, 20, 15]))
    assert "MARJE ERODATE" in flags(r)


def test_dangerous_debt():
    r = ft.analyze(make_raw(total_debt=[100, 110, 120, 130], cash=[10, 10, 10, 10]))
    assert r["verdict"] == "FAIL" and "ÎNDATORARE PERICULOASĂ" in flags(r)


def test_rising_debt_falling_cash():
    r = ft.analyze(make_raw(total_debt=[30, 40, 50, 60], cash=[40, 35, 30, 25]))
    assert "ÎNDATORARE ÎN CREȘTERE" in flags(r)


def test_dilution():
    r = ft.analyze(make_raw(shares=[100, 110, 121, 133]))
    assert "DILUARE" in flags(r)


# ── Cazuri speciale ──────────────────────────────────────────────────────────

def test_fcf_fallback_from_ocf_and_capex():
    r = ft.analyze(make_raw(fcf=[None] * 4, ocf=[20, 22, 24, 26], capex=[-5, -5, -6, -6]))
    assert r["series"]["fcf"] == [15, 17, 18, 20]


def test_financial_company_skips_fcf_and_debt_tests():
    r = ft.analyze(make_raw(sector="Financial Services", fcf=[50, -40, -60, -150], op_income=[None] * 4,
                            total_debt=[300, 400, 450, 500], cash=[500, 400, 300, 200]))
    assert r["red_flags"] == []
    assert r["verdict"] == "WARN" and "financiară" in r["notes"][0]
    assert r["data_quality"] == "full"


def test_missing_core_data_is_partial_and_not_pass():
    r = ft.analyze(make_raw(total_debt=[None] * 4))
    assert r["data_quality"] == "partial" and r["verdict"] == "WARN"


def test_no_data_raises_tool_error():
    raw = make_raw(**{m: [None] * 4 for m in ft.ROWS})
    with pytest.raises(ToolError):
        ft.analyze(raw)


# ── Helperi ──────────────────────────────────────────────────────────────────

def test_extract_uses_first_available_row_and_skips_nan():
    df = pd.DataFrame({pd.Timestamp("2025-06-30"): [None, 5.0], pd.Timestamp("2024-06-30"): [None, float("nan")]},
                      index=["Free Cash Flow", "Operating Revenue"])
    assert ft._extract(df, ["Free Cash Flow"]) == {}
    assert ft._extract(df, ["Total Revenue", "Operating Revenue"]) == {2025: 5.0}


@pytest.mark.parametrize("raw, norm", [("brk.b", "BRK-B"), (" msft ", "MSFT"), ("SHEL", "SHEL")])
def test_normalize_ticker(raw, norm):
    assert ft.normalize_ticker(raw) == norm


def test_tool_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ft, "_cache", ft.DiskCache("yf", 3600, root=tmp_path))
    calls = {"n": 0}

    def fake_fetch(symbol):
        calls["n"] += 1
        return make_raw() | {"ticker": symbol}

    monkeypatch.setattr(ft, "_fetch_raw", fake_fetch)
    out = json.loads(ft.deep_fundamental_inspector("test"))
    assert out["ok"] and out["ticker"] == "TEST" and out["verdict"] == "PASS"
    ft.deep_fundamental_inspector("TEST")
    assert calls["n"] == 1


# ── Live ─────────────────────────────────────────────────────────────────────

@pytest.mark.live
def test_live_msft_has_real_series():
    out = json.loads(ft.deep_fundamental_inspector("MSFT"))
    assert out["ok"] and out["data_quality"] == "full"
    assert len(out["years"]) >= 3
    assert all(v and v > 100_000 for v in out["series"]["revenue"])  # > 100 mld. USD
    assert all(v and v > 0 for v in out["series"]["fcf"])
    assert out["verdict"] in ("PASS", "WARN")


@pytest.mark.live
def test_live_intc_is_flagged():
    out = json.loads(ft.deep_fundamental_inspector("INTC"))
    assert out["ok"] and out["verdict"] == "FAIL"
    assert any("ARDE BANI" in f for f in out["red_flags"])


@pytest.mark.live
def test_live_unknown_ticker_returns_error():
    out = json.loads(ft.deep_fundamental_inspector("ZZZZXQ"))
    assert out["ok"] is False and out["hint"]
