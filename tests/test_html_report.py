"""Raportul HTML: citirea Markdown-ului, fișele cu date reale, jurnalul și siguranța (escapare)."""

import json

import pytest

import config
from tools import report_tool as rt
from utils import html_report as hr
from utils import journal
from utils import console as ui

REPORT = """# 🦊 Raportul Vulpii

Am vânat azi cu **trei unghiuri**.

---

## Sterling Infrastructure (STRL)

### Povestea pe șleau
Omul cu excavatorul.

### De ce cifrele arată beton
FCF pozitiv în fiecare an.

### Testul meu de Paranoia (Ce am săpat pe net)
Nimic grav.

---

## Exelixis (NASDAQ: EXEL)

### 1. Povestea pe sleau
Vaca de lapte.

### 2. De ce cifrele arata beton
Marje de bijutier.

### 3. Testul meu de Paranoia
Brevete.

## Cimitirul Iluziilor (Firme pe care le-am aruncat la gunoi azi)
- **Micron (MU)**: vârf de ciclu.

## Concluzie
Patru solide.
"""

FUND = {"ok": True, "ticker": "STRL", "name": "Sterling Infrastructure, Inc.", "currency": "USD",
        "years": [2022, 2023, 2024, 2025],
        "series": {"revenue": [1769, 1972, 2116, 2490], "op_margin_pct": [9.1, 10.4, 12.8, 16.6],
                   "fcf": [158, 414, -16, 363], "total_debt": [491, 399, 369, 350], "cash": [182, 472, 664, 391]},
        "ratios": {"revenue_cagr_pct": 12.1, "op_margin_change_pp": 7.5, "net_debt_mln": -41.0,
                   "shares_cagr_pct": 0.11, "trailing_pe": 13.7},
        "red_flags": [], "green_flags": ["Cash net"], "verdict": "PASS", "data_quality": "full"}
MU = {"ok": True, "ticker": "MU", "name": "Micron", "verdict": "FAIL", "red_flags": ["[high] vârf de ciclu"]}
SCREEN = {"ok": True, "angle": "GARP", "count": 8, "filters_applied": {"P/E": "Under 25"},
          "rows": [{"ticker": "STRL", "company": "Sterling", "sector": "Industrials", "pe": 14.0, "p_fcf": 17.1,
                    "roe": 43.3, "mcap_bln": 8.0, "price": 261.0},
                   {"ticker": "EXEL", "company": "Exelixis", "p_fcf": 14.4, "mcap_bln": 16.8}]}
OSINT = {"ok": True, "ticker": "STRL", "sources_status": {"ddg_news": "ok", "reddit": "blocked"},
         "threat_summary": {"distress": 1},
         "top_threats": [{"source": "ddg_news", "title": "Stock dips", "url": "https://example.com/a", "tags": ["distress"]},
                         {"source": "ddg_web", "title": "Rău", "url": "javascript:alert(1)", "tags": []}]}
RESULTS = [{"name": "finviz_dynamic_screener", "args": {"angle": "GARP"}, "result": SCREEN},
           {"name": "deep_fundamental_inspector", "args": {"ticker": "STRL"}, "result": FUND},
           {"name": "deep_fundamental_inspector", "args": {"ticker": "MU"}, "result": MU},
           {"name": "deep_fundamental_inspector", "args": {"ticker": "EXEL"},
            "result": {"ok": True, "ticker": "EXEL", "verdict": "PASS", "ratios": {"revenue_cagr_pct": 12.9}}},
           {"name": "paranoid_osint_radar", "args": {"ticker": "STRL"}, "result": OSINT}]
META = {"model": "claude-test", "when": "2026-11-06 12:00", "run": "r1", "disclaimer": "Nu este o recomandare."}


def test_parse_report_finds_companies_tickers_and_sections():
    parsed = hr.parse_report(REPORT)
    assert parsed["title"] == "🦊 Raportul Vulpii"
    assert "trei unghiuri" in parsed["intro"] and "---" not in parsed["intro"]
    assert [(c["name"], c["ticker"]) for c in parsed["companies"]] == [("Sterling Infrastructure", "STRL"),
                                                                       ("Exelixis", "EXEL")]
    assert set(parsed["companies"][1]["parts"]) == {"story", "numbers", "paranoia"}  # și fără diacritice
    assert "Micron" in parsed["graveyard"]["body"]
    assert [h for h, _ in parsed["other"]] == ["Concluzie"]


def test_render_contains_cards_charts_graveyard_and_journal():
    events = [{"kind": "phase", "phase": 1, "title": "Faza 1", "t": "12:00:00"},
              {"kind": "agent", "text": "Pornesc **vânătoarea**.", "t": "12:00:01"},
              {"kind": "count", "count": 8, "guidance": "ȚINTĂ", "level": "ok", "t": "12:00:02"}]
    page = hr.render(REPORT, events, RESULTS, META)
    assert page.startswith("<!doctype html>") and 'id="raport"' in page and 'id="jurnal"' in page
    assert 'id="firma-STRL"' in page and 'id="firma-EXEL"' in page
    assert "13,7" in page and "+12,1%" in page and "41 mil" in page  # P/E, creștere, cash net (format RO)
    assert '<td class="neg">-16 mil</td>' in page  # FCF negativ marcat în tabelul anual
    assert "<td>MU</td>" in page and "vârf de ciclu" in page  # MU în tabelul cimitirului, cu semnalele autopsiei
    assert 'class="tbl sortable"' in page  # comparația finaliștilor
    # tot ce a scris agentul despre fiecare firmă e afișat (nu ascuns în taburi)
    for text in ("Omul cu excavatorul.", "FCF pozitiv în fiecare an.", "Nimic grav.", "Vaca de lapte.", "Brevete."):
        assert text in page
    assert "🦊" not in page and "<h1>Raportul Vulpii</h1>" in page  # fără iconițe
    assert "Pornesc <strong>vânătoarea</strong>." in page  # textul Vulpii randat în jurnal
    assert "claude-test" in page and "Nu este o recomandare." in page


def test_render_escapes_untrusted_text_and_unsafe_links():
    evil = REPORT.replace("Omul cu excavatorul.", "<script>alert(1)</script> <img src=x onerror=alert(2)>")
    events = [{"kind": "info", "text": "<script>bad()</script>", "t": ""}]
    page = hr.render(evil, events, RESULTS, META)
    body = page.split("<body", 1)[1].rsplit("<script>", 1)[0]  # fără scriptul propriu al paginii
    assert "<script>" not in body and "<img" not in body
    assert "&lt;script&gt;" in body
    assert "javascript:alert" not in page  # link-ul periculos din OSINT rămâne doar text
    assert 'href="https://example.com/a"' in page


def test_render_without_report_opens_journal():
    page = hr.render(None, [{"kind": "error", "text": "API căzut", "t": ""}], [], META | {"stop_reason": "api_error"})
    assert 'data-start="jurnal"' in page and "fără raport final" in page and "api_error" in page


def test_render_survives_minimal_tool_data():
    results = [{"name": "deep_fundamental_inspector", "args": {"ticker": "STRL"}, "result": {"ok": True, "ticker": "STRL"}}]
    page = hr.render(REPORT, [], results, META)
    assert 'id="firma-STRL"' in page and "Cifre anuale" not in page  # fără serii → fără tabel, fără erori


def test_console_helpers_write_to_journal():
    journal.reset()
    ui.phase_banner(2)
    ui.show_tool_call("deep_fundamental_inspector", {"ticker": "MSFT"})
    ui.show_table("[bold]📊 MSFT[/bold]", ["An"], [[2024]])
    ui.verdict("WARN", "partial")
    kinds = [e["kind"] for e in journal.EVENTS]
    assert kinds == ["phase", "tool", "table", "verdict"]
    assert journal.EVENTS[2]["title"] == "📊 MSFT"  # marcajele rich scoase
    journal.reset()
    assert journal.EVENTS == []


def test_dispatch_records_tool_results(monkeypatch):
    import tools
    journal.reset()
    monkeypatch.setitem(tools.FUNCTIONS, "deep_fundamental_inspector", lambda ticker: json.dumps(FUND))
    tools.dispatch("deep_fundamental_inspector", {"ticker": "STRL"})
    assert journal.TOOL_RESULTS[-1]["result"]["verdict"] == "PASS"
    journal.reset()


def test_failed_run_writes_only_run_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPORT_PATH", tmp_path / "raport_sateliti.html")
    rt.configure(run_dir=tmp_path / "run2", model="m")
    paths = rt.write_html(summary=[["Oprire", "api_error"]], stop_reason="api_error")
    assert paths == [str(tmp_path / "run2" / "raport_sateliti.html")]
    assert not config.REPORT_PATH.exists()  # nu suprascrie raportul bun anterior
    rt.configure(run_dir=None)


@pytest.mark.parametrize("value, expected", [(1234.5, "1,23 mld"), (158, "158 mil"), (None, "–"), (-41, "-41 mil")])
def test_fmt_money(value, expected):
    assert hr.fmt_money(value) == expected
