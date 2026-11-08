"""Raportul HTML, într-un singur fișier fără dependențe externe (merge și offline). Design minimalist, fundal alb.

Pagina 1, „Raport”: câte o secțiune pe toată lățimea pentru fiecare companie, cu tot ce a scris agentul despre ea,
tabelul de indicatori și cifrele anuale (din datele uneltelor), apoi comparația finaliștilor, Cimitirul Iluziilor
și cum a ales agentul. Pagina 2, „Jurnal”: tot ce a apărut în terminal, cu filtre și căutare.

Tot textul venit de la agent sau de pe internet e escapat; Markdown-ul e randat cu HTML-ul brut dezactivat.
"""

from __future__ import annotations

import html
import json
import math
import re
import unicodedata
from typing import Any

from markdown_it import MarkdownIt

_MD = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")

SUBSECTIONS = (("povestea", "story", "Povestea"),
               ("cifrele", "numbers", "Cifrele"),
               ("paranoia", "paranoia", "Paranoia"))


# ── Utilitare ────────────────────────────────────────────────────────────────

def e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def md(text: str | None) -> str:
    """Markdown → HTML, fără iconițe (emoji): designul e minimalist."""
    return _MD.render(_EMOJI.sub("", text or ""))


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _key(ticker: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (ticker or "").upper())


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _ro(value: float, digits: int = 1) -> str:
    """Număr în format românesc: 1.234,5"""
    s = f"{value:,.{digits}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def fmt_money(value: Any) -> str:
    v = _num(value)
    if v is None:
        return "–"
    if abs(v) >= 1000:
        return f"{_ro(v / 1000, 2)} mld"
    return f"{_ro(v, 0)} mil"


def fmt_pct(value: Any, digits: int = 1, sign: bool = False) -> str:
    v = _num(value)
    if v is None:
        return "–"
    return ("+" if sign and v > 0 else "") + f"{_ro(v, digits)}%"


def fmt_x(value: Any, digits: int = 1) -> str:
    v = _num(value)
    return "–" if v is None else _ro(v, digits)


def _safe_url(url: Any) -> str | None:
    return url if isinstance(url, str) and re.match(r"^https?://", url, re.I) else None


def _last(values: list | None) -> Any:
    for v in reversed(values or []):
        if _num(v) is not None:
            return v
    return None


# ── Citirea raportului Markdown ──────────────────────────────────────────────

def _split(text: str, level: int) -> tuple[str, list[tuple[str, str]]]:
    pattern = re.compile(rf"^{'#' * level}[ \t]+(.+?)[ \t#]*$", re.M)
    found = list(pattern.finditer(text))
    if not found:
        return text, []
    sections = []
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        sections.append((m.group(1).strip(), _trim(text[m.end():end])))
    return text[:found[0].start()], sections


def _trim(body: str) -> str:
    """Scoate liniile orizontale (---) de la capete."""
    return re.sub(r"^(?:\s*(?:-{3,}|\*{3,}|_{3,})\s*\n)+|(?:\n\s*(?:-{3,}|\*{3,}|_{3,})\s*)+$", "",
                  body.strip()).strip()


def _ticker_from_heading(heading: str) -> str | None:
    m = re.search(r"\(([^()]*)\)\s*$", heading)
    if not m:
        return None
    tokens = re.findall(r"[A-Z][A-Z0-9.\-]{0,9}", m.group(1))
    return tokens[-1] if tokens else None


def parse_report(text: str) -> dict:
    """Markdown → titlu, introducere, companii (cu cele 3 subsecțiuni), cimitir, alte secțiuni."""
    pre, sections = _split(text or "", 2)
    title_match = re.search(r"^#[ \t]+(.+?)[ \t#]*$", pre, re.M)
    title = title_match.group(1).strip() if title_match else "Raport Sateliți"
    intro = _trim(pre[title_match.end():] if title_match else pre)
    out = {"title": title, "intro": intro, "companies": [], "graveyard": None, "other": []}
    for heading, body in sections:
        if "cimitirul" in _plain(heading):
            out["graveyard"] = {"heading": heading, "body": body}
            continue
        lead, subs = _split(body, 3)
        parts: dict[str, str] = {}
        extra = []
        for sub_heading, sub_body in subs:
            plain = _plain(sub_heading)
            slot = next((slot for word, slot, _ in SUBSECTIONS if word in plain), None)
            if slot and slot not in parts:
                parts[slot] = sub_body
            else:
                extra.append((sub_heading, sub_body))
        if parts:
            ticker = _ticker_from_heading(heading)
            name = re.sub(r"\s*\([^()]*\)\s*$", "", heading).strip() or heading
            out["companies"].append({"heading": heading, "name": name, "ticker": ticker, "lead": _trim(lead),
                                     "parts": parts, "extra": extra})
        else:
            out["other"].append((heading, body))
    return out


# ── Datele uneltelor ─────────────────────────────────────────────────────────

def collect_data(tool_results: list[dict]) -> dict:
    """Ultimul rezultat valid per ticker, pentru fiecare unealtă + istoricul screener-ului."""
    data = {"fund": {}, "row": {}, "osint": {}, "screens": []}
    for item in tool_results:
        name, args, res = item.get("name"), item.get("args") or {}, item.get("result") or {}
        if res.get("ok") is False:
            if name == "finviz_dynamic_screener":
                data["screens"].append({"angle": args.get("angle") or "", "count": None, "error": res.get("error")})
            continue
        if name == "finviz_dynamic_screener":
            data["screens"].append({"angle": res.get("angle") or args.get("angle") or "",
                                    "count": res.get("count"), "filters": res.get("filters_applied") or {}})
            for row in res.get("rows") or []:
                if isinstance(row, dict) and row.get("ticker"):
                    data["row"][_key(row["ticker"])] = row
        elif name == "deep_fundamental_inspector":
            ticker = res.get("ticker") or args.get("ticker")
            if ticker:
                data["fund"][_key(ticker)] = res
        elif name == "paranoid_osint_radar":
            ticker = res.get("ticker") or args.get("ticker")
            if ticker:
                data["osint"][_key(ticker)] = res
    return data


def _match_ticker(company: dict, data: dict) -> str | None:
    if company["ticker"]:
        return _key(company["ticker"])
    known = set(data["fund"]) | set(data["osint"])
    for key in known:  # ticker scris în titlu fără paranteze
        if re.search(rf"\b{re.escape(key)}\b", company["heading"].upper()):
            return key
    return None


# ── Pagina 1: raportul ───────────────────────────────────────────────────────



def _noemoji(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _EMOJI.sub("", text or "")).strip()


def _grade(value: float | None, good, mid, higher_is_better: bool = True) -> str:
    if value is None:
        return ""
    if higher_is_better:
        return "pos" if value >= good else "" if value >= mid else "neg"
    return "pos" if value <= good else "" if value <= mid else "neg"


def _cls(grade: str) -> str:
    return f' class="{grade}"' if grade else ""


def key_figures(fund: dict, row: dict) -> list[tuple[str, str, str, str]]:
    """(etichetă, valoare, notă pos/neg, explicație) pentru tabelul de indicatori."""
    ratios, series = fund.get("ratios") or {}, fund.get("series") or {}
    pe = _num(ratios.get("trailing_pe")) or _num(row.get("pe"))
    pfcf = _num(row.get("p_fcf"))
    cagr = _num(ratios.get("revenue_cagr_pct"))
    margin = _num(_last(series.get("op_margin_pct")))
    net_debt = _num(ratios.get("net_debt_mln"))
    shares = _num(ratios.get("shares_cagr_pct"))
    roe = _num(row.get("roe"))
    mcap = _num(row.get("mcap_bln"))
    return [
        ("P/E", fmt_x(pe), _grade(pe, 15, 25, False) if pe and pe > 0 else "",
         "Prețul acțiunii împărțit la profitul pe acțiune. Mai mic înseamnă mai ieftin."),
        ("P/FCF", fmt_x(pfcf), _grade(pfcf, 15, 25, False) if pfcf and pfcf > 0 else "",
         "Prețul firmei împărțit la cash-ul liber produs într-un an. Mai mic înseamnă mai ieftin."),
        ("Creștere / an", fmt_pct(cagr, sign=True), _grade(cagr, 10, 0),
         "Creșterea medie anuală a veniturilor."),
        ("Marjă op.", fmt_pct(margin), _grade(margin, 15, 5),
         "Cât rămâne din 100 de lei vânduți după costurile de funcționare."),
        ("Cash net" if (net_debt or 0) <= 0 else "Datorie netă",
         fmt_money(abs(net_debt)) if net_debt is not None else "–",
         "" if net_debt is None else "pos" if net_debt <= 0 else "neg",
         "Cash minus datorii."),
        ("Acțiuni / an", fmt_pct(shares, sign=True), _grade(shares, 0, 3, False),
         "Negativ înseamnă că firma își răscumpără acțiunile."),
        ("ROE", fmt_pct(roe), _grade(roe, 15, 8), "Profitul raportat la banii acționarilor."),
        ("Capitalizare", f"{_ro(mcap, 1)} mld $" if mcap is not None else "–", "", "Cât valorează firma la bursă."),
    ]


def figures_table(fund: dict, row: dict) -> str:
    figs = key_figures(fund, row)
    if all(value == "–" for _, value, _, _ in figs):
        return ""
    head = "".join(f'<th title="{e(tip)}">{e(label)}</th>' for label, _, _, tip in figs)
    body = "".join(f"<td{_cls(grade)}>{e(value)}</td>" for _, value, grade, _ in figs)
    return (f'<div class="scroll"><table class="tbl figures"><thead><tr>{head}</tr></thead>'
            f'<tbody><tr>{body}</tr></tbody></table></div>')


def years_table(fund: dict) -> str:
    years, s = fund.get("years") or [], fund.get("series") or {}
    if not years or not s:
        return ""
    cols = [("Venituri", "revenue", fmt_money), ("Creștere", "revenue_growth_pct", lambda v: fmt_pct(v, sign=True)),
            ("Marjă op.", "op_margin_pct", fmt_pct), ("Profit net", "net_income", fmt_money),
            ("Free Cash Flow", "fcf", fmt_money), ("Datorie", "total_debt", fmt_money), ("Cash", "cash", fmt_money)]
    head = "<th>An</th>" + "".join(f"<th>{e(label)}</th>" for label, _, _ in cols)
    rows = []
    for i, year in enumerate(years):
        cells = [f"<td>{e(year)}</td>"]
        for _, key, fmt in cols:
            values = s.get(key) or []
            v = _num(values[i]) if i < len(values) else None
            neg = v is not None and v < 0 and key in ("fcf", "net_income", "revenue_growth_pct")
            cells.append(f'<td{_cls("neg" if neg else "")}>{e(fmt(v) if v is not None else "–")}</td>')
        rows.append(f"<tr>{''.join(cells)}</tr>")
    unit = e(f"{fund.get('currency') or ''}, milioane").strip(", ")
    return (f'<p class="cap">Cifre anuale · {unit}</p><div class="scroll scroll-tight"><table class="tbl years">'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def signals(fund: dict, osint: dict) -> str:
    items = [f'<li class="neg">{e(f)}</li>' for f in fund.get("red_flags") or []]
    items += [f"<li>{e(f)}</li>" for f in fund.get("green_flags") or []]
    items += [f'<li class="muted">{e(f)}</li>' for f in fund.get("notes") or []]
    parts = []
    if items:
        verdict = fund.get("verdict")
        parts.append(f'<p class="label">Verificarea cifrelor{f" · verdict {e(verdict)}" if verdict else ""}</p>'
                     f'<ul class="plain">{"".join(items)}</ul>')
    if osint:
        threats = {k: v for k, v in (osint.get("threat_summary") or {}).items() if isinstance(v, (int, float))}
        total = sum(threats.values())
        detail = ", ".join(f"{e(k)} {v:g}" for k, v in threats.items())
        status = osint.get("sources_status") or {}
        blocked = [k for k, v in status.items() if str(v).lower() != "ok"]
        sources = (f"{len(status) - len(blocked)} din {len(status)} surse au răspuns"
                   + (f" (fără rezultat sau blocate: {e(', '.join(blocked))})" if blocked else "")) if status else ""
        links = []
        for t in (osint.get("top_threats") or [])[:6]:
            url = _safe_url(t.get("url"))
            title = e(t.get("title") or url or "fără titlu")
            meta = " · ".join(x for x in (e(t.get("source")), e(t.get("date"))) if x)
            link = f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer">{title}</a>' if url else title
            links.append(f'<li>{link} <span class="muted">{meta}</span></li>')
        parts.append(f'<p class="label">Radarul de știri · {total:g} semnale de risc{f" ({detail})" if detail else ""}</p>'
                     f'<p class="muted small">{sources}</p>'
                     + (f'<ul class="plain links">{"".join(links)}</ul>' if links else ""))
    return f'<div class="signals">{"".join(parts)}</div>' if parts else ""


def company_section(company: dict, data: dict, index: int) -> str:
    key = _match_ticker(company, data)
    fund, row, osint = data["fund"].get(key, {}), data["row"].get(key, {}), data["osint"].get(key, {})
    ticker = company["ticker"] or fund.get("ticker") or ""
    meta = [x for x in (row.get("sector") or fund.get("sector"), row.get("industry") or fund.get("industry"),
                        row.get("country")) if x]
    if _num(row.get("price")):
        meta.append(f"preț {_ro(_num(row['price']), 2)} $")
    verdict = fund.get("verdict")
    verdict_html = (f' · <span{_cls({"PASS": "pos", "FAIL": "neg"}.get(verdict, ""))}>{e(verdict)}</span>'
                    if verdict else "")
    parts = company["parts"]
    text = lambda slot, title: (f'<h3>{title}</h3><div class="prose">{md(parts[slot])}</div>' if slot in parts else "")
    lead = f'<div class="prose">{md(company["lead"])}</div>' if company["lead"] else ""
    extra = "".join(f'<h3>{e(_noemoji(h))}</h3><div class="prose">{md(b)}</div>' for h, b in company["extra"])
    return f"""
<section class="company" id="firma-{e(ticker or index)}">
  <p class="num">{index + 1:02d}</p>
  <h2>{e(_noemoji(company["name"]))} <span class="ticker">{e(ticker)}</span></h2>
  <p class="meta">{e(" · ".join(meta))}{verdict_html}</p>
  {lead}
  {text("story", "Cu ce se ocupă firma")}
  {figures_table(fund, row)}
  {years_table(fund)}
  {text("numbers", "De ce cifrele arată beton")}
  {text("paranoia", "Testul de paranoia: ce a găsit pe net")}
  {extra}
  {signals(fund, osint)}
</section>"""


def comparison(companies: list[dict], data: dict) -> str:
    """Tabel sortabil cu toți finaliștii."""
    rows = []
    for c in companies:
        key = _match_ticker(c, data)
        fund, row, osint = data["fund"].get(key, {}), data["row"].get(key, {}), data["osint"].get(key, {})
        ratios, series = fund.get("ratios") or {}, fund.get("series") or {}
        threats = sum(v for v in (osint.get("threat_summary") or {}).values() if isinstance(v, (int, float)))
        net_debt = _num(ratios.get("net_debt_mln"))
        cells = [
            (c["ticker"] or c["name"], f'<a href="#firma-{e(c["ticker"] or "")}">{e(c["ticker"] or c["name"])}</a>', ""),
            (_num(ratios.get("trailing_pe")) or _num(row.get("pe")), fmt_x, ""),
            (_num(row.get("p_fcf")), fmt_x, ""),
            (_num(ratios.get("revenue_cagr_pct")), lambda v: fmt_pct(v, sign=True), ""),
            (_num(_last(series.get("op_margin_pct"))), fmt_pct, ""),
            (_num(_last(series.get("fcf"))), fmt_money, ""),
            (-net_debt if net_debt is not None else None, fmt_money, ""),
            (threats if osint else None, lambda v: f"{v:g}", ""),
        ]
        tds = []
        for value, shown, _ in cells:
            html_value = shown if isinstance(shown, str) else (e(shown(value)) if value is not None else "–")
            sort = value if isinstance(value, (int, float)) else (value or "")
            tds.append(f'<td data-v="{e(sort)}">{html_value}</td>')
        rows.append(f"<tr>{''.join(tds)}</tr>")
    heads = ["Firma", "P/E", "P/FCF", "Creștere / an", "Marjă op.", "FCF ultim an", "Cash net", "Semnale risc"]
    thead = "".join(f'<th data-col="{i}" data-type="{"text" if i == 0 else "num"}">{e(h)}</th>'
                    for i, h in enumerate(heads))
    return (f'<div class="scroll"><table class="tbl sortable"><thead><tr>{thead}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="muted small center">Click pe titlul unei coloane ca să sortezi.</p>')


def hunt(data: dict, finalists: int) -> str:
    """Căutările din FinViz + etapele selecției."""
    screens = data["screens"]
    if not screens and not data["fund"]:
        return ""
    rows = []
    for i, s in enumerate(screens, 1):
        count = s["count"]
        grade = "" if count is None else "pos" if 5 <= count <= 12 else "neg" if count <= 2 or count > 30 else ""
        filters = "; ".join(f"{k}: {v}" for k, v in (s.get("filters") or {}).items()) or s.get("error") or ""
        rows.append(f'<tr title="{e(filters)}"><td>{i}</td><td class="left">{e(s["angle"] or "fără nume")}</td>'
                    f'<td{_cls(grade)}>{e(count if count is not None else "eroare")}</td></tr>')
    verdicts = [f.get("verdict") for f in data["fund"].values()]
    steps = [("Căutări în FinViz", len(screens)), ("Companii verificate (cifre)", len(data["fund"])),
             ("Au trecut verificarea cifrelor", sum(v in ("PASS", "WARN") for v in verdicts)),
             ("Verificate pe net (știri, GitHub, Reddit)", len(data["osint"])), ("Finaliști", finalists)]
    funnel = "".join(f'<tr><td class="left">{e(label)}</td><td>{n}</td></tr>' for label, n in steps)
    searches = (f'<table class="tbl"><caption>Căutările din FinViz · ținta: 5-12 companii</caption>'
                f'<thead><tr><th>#</th><th class="left">Unghi</th><th>Companii găsite</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table>') if rows else ""
    return (f'<section class="block" id="vanatoarea"><h2>Cum a ales agentul</h2>{searches}'
            f'<table class="tbl"><caption>Selecția, pas cu pas</caption><tbody>{funnel}</tbody></table></section>')


def graveyard(section: dict | None, data: dict, finalist_keys: set[str]) -> str:
    if not section:
        return ""
    body = section["body"]
    rows = []
    for key, fund in data["fund"].items():
        ticker = fund.get("ticker") or key
        if key in finalist_keys or not re.search(rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])", body):
            continue
        verdict = fund.get("verdict") or "–"
        flags = "; ".join(fund.get("red_flags") or []) or "–"
        rows.append(f'<tr><td>{e(ticker)}</td><td class="left">{e(fund.get("name") or "")}</td>'
                    f'<td{_cls({"PASS": "pos", "FAIL": "neg"}.get(verdict, ""))}>{e(verdict)}</td>'
                    f'<td class="left small">{e(flags)}</td></tr>')
    table = (f'<div class="scroll"><table class="tbl"><thead><tr><th>Ticker</th><th class="left">Firma</th>'
             f'<th>Verdict</th><th class="left">Semnale din cifre</th></tr></thead><tbody>{"".join(rows)}</tbody>'
             f'</table></div>') if rows else ""
    return (f'<section class="block" id="cimitir"><h2>{e(_noemoji(section["heading"]))}</h2>'
            f'<div class="prose">{md(body)}</div>{table}</section>')


def report_page(report_md: str | None, data: dict, meta: dict) -> str:
    if not report_md:
        reason = e(meta.get("stop_reason") or "necunoscut")
        return (f'<header class="top"><p class="kicker">Paranoid Wolf · Raport Satelit</p>'
                f'<h1>Rularea s-a oprit fără raport final</h1><p class="meta">Motiv: {reason}. '
                f'Tot ce s-a întâmplat e în pagina <a href="#jurnal">Jurnal</a>.</p></header>' + hunt(data, 0))
    parsed = parse_report(report_md)
    companies = parsed["companies"]
    finalist_keys = {k for k in (_match_ticker(c, data) for c in companies) if k}
    sections = "".join(company_section(c, data, i) for i, c in enumerate(companies))
    toc = " · ".join(f'<a href="#firma-{e(c["ticker"] or i)}">{e(_noemoji(c["name"]))}</a>'
                     for i, c in enumerate(companies))
    stats = (f"{len(companies)} finaliști din {len(data['fund'])} companii verificate · "
             f"{len(data['screens'])} căutări în FinViz · {len(data['osint'])} verificări pe net")
    intro = f'<section class="block"><div class="prose">{md(parsed["intro"])}</div></section>' if parsed["intro"] else ""
    other = "".join(f'<section class="block"><h2>{e(_noemoji(h))}</h2><div class="prose">{md(b)}</div></section>'
                    for h, b in parsed["other"])
    run = f' · rulare {e(meta["run"])}' if meta.get("run") else ""
    return f"""
<header class="top">
  <p class="kicker">Paranoid Wolf · Raport Satelit</p>
  <h1>{e(_noemoji(parsed["title"]))}</h1>
  <p class="meta">{e(meta.get("when", ""))} · model {e(meta.get("model", ""))}{run}</p>
  <p class="meta">{stats}</p>
  <nav class="toc">{toc}</nav>
</header>
{intro}
{sections}
{f'<section class="block" id="comparatie"><h2>Finaliștii, față în față</h2>{comparison(companies, data)}</section>' if companies else ""}
{graveyard(parsed["graveyard"], data, finalist_keys)}
{other}
{hunt(data, len(companies))}"""


# ── Pagina 2: jurnalul ───────────────────────────────────────────────────────

GROUPS = {"tool": "unelte", "filters": "unelte", "count": "unelte", "verdict": "unelte", "table": "tabele",
          "thought": "ganduri", "agent": "agent", "warn": "probleme", "error": "probleme",
          "info": "info", "success": "info", "usage": "tokeni", "iteration": "info", "markdown": "info"}
GROUP_LABELS = [("unelte", "Unelte"), ("tabele", "Tabele"), ("agent", "Agentul"), ("ganduri", "Gânduri"),
                ("probleme", "Probleme"), ("info", "Info"), ("tokeni", "Tokeni")]
KIND_LABEL = {"tool": "unealtă", "thought": "gând", "agent": "agent", "warn": "atenție", "error": "eroare",
              "success": "ok", "info": "", "count": "", "verdict": "", "usage": "tokeni", "markdown": ""}


def _table(title: str, columns: list, rows: list) -> str:
    head = "".join(f"<th>{e(c)}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in rows)
    cap = f"<caption>{e(_noemoji(title))}</caption>" if title else ""
    return f'<div class="scroll"><table class="tbl small">{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _event_html(ev: dict) -> str:
    kind = ev.get("kind")
    group = GROUPS.get(kind, "info")
    time = f'<span class="t">{e(ev.get("t"))}</span>'
    label = f'<span class="k">{e(KIND_LABEL.get(kind, ""))}</span>'
    if kind == "iteration":
        return f'<div class="ev iter" data-g="{group}">Iterația {e(ev.get("n"))} din {e(ev.get("max"))}</div>'
    if kind in ("filters", "table"):
        if kind == "filters":
            title = f"Filtre FinViz{' · ' + ev['angle'] if ev.get('angle') else ''}"
            inner = _table(title, ["Filtru", "Înainte", "Acum", ""], ev.get("rows") or [])
        else:
            inner = _table(ev.get("title", ""), ev.get("columns") or [], ev.get("rows") or [])
        return f'<div class="ev wide" data-g="{group}">{inner}</div>'
    if kind == "thought":
        body = f'<div class="x quiet">{e(ev.get("text"))}</div>'
    elif kind == "agent":
        body = f'<div class="x quiet prose">{md(ev.get("text"))}</div>'
    elif kind == "tool":
        body = f'<div class="x"><code>{e(ev.get("name"))}({e(ev.get("args"))})</code></div>'
    elif kind == "count":
        grade = {"ok": "pos", "bad": "neg"}.get(ev.get("level"), "")
        body = f'<div class="x"><b{_cls(grade)}>{e(ev.get("count"))} companii</b> <span class="muted">{e(ev.get("guidance"))}</span></div>'
    elif kind == "verdict":
        v = ev.get("verdict")
        body = (f'<div class="x">Verdict: <b{_cls({"PASS": "pos", "FAIL": "neg"}.get(v, ""))}>{e(v)}</b> '
                f'<span class="muted">(date: {e(ev.get("quality"))})</span></div>')
    elif kind == "usage":
        body = (f'<div class="x muted">in {ev.get("inp", 0):,} · cache citit {ev.get("c_read", 0):,} · cache scris '
                f'{ev.get("c_write", 0):,} · out {ev.get("out", 0):,} · cache hit {e(ev.get("hit"))}</div>')
    elif kind == "markdown":
        body = '<div class="x">Raportul final a fost livrat (vezi pagina <a href="#raport">Raport</a>).</div>'
    else:
        css = {"warn": "warn", "error": "neg"}.get(kind, "")
        body = f'<div class="x{" " + css if css else ""}">{e(ev.get("text"))}</div>'
    return f'<div class="ev" data-g="{group}">{time}{label}{body}</div>'


def journal_page(events: list[dict], meta: dict) -> str:
    chunks, current = [], {"title": "Pornire", "items": []}
    for ev in events:
        if ev.get("kind") == "phase":
            chunks.append(current)
            current = {"title": _noemoji(ev.get("title") or f"Faza {ev.get('phase')}"), "items": []}
        else:
            current["items"].append(ev)
    chunks.append(current)
    sections = "".join(f'<section class="phase"><h2>{e(ch["title"])}</h2>'
                       f'{"".join(_event_html(ev) for ev in ch["items"])}</section>'
                       for ch in chunks if ch["items"])
    counts: dict[str, int] = {}
    for ev in events:
        g = GROUPS.get(ev.get("kind"))
        if g:
            counts[g] = counts.get(g, 0) + 1
    toggles = "".join(f'<button class="toggle on" data-g="{g}">{e(label)} {counts[g]}</button>'
                      for g, label in GROUP_LABELS if counts.get(g))
    summary = meta.get("summary") or []
    summary_html = (f'<table class="tbl summary"><caption>Rezumatul rulării</caption><tbody>'
                    + "".join(f'<tr><td class="left">{e(k)}</td><td>{e(v)}</td></tr>' for k, v in summary)
                    + "</tbody></table>") if summary else ""
    return f"""
<header class="top">
  <p class="kicker">Paranoid Wolf · Jurnal</p>
  <h1>Ce a apărut în terminal</h1>
  <p class="meta">{e(meta.get("when", ""))} · {len(events)} mesaje</p>
</header>
{summary_html}
<div class="filters">
  <input type="search" id="q" placeholder="Caută în jurnal" aria-label="Caută în jurnal">
  <div class="toggles">{toggles}</div>
</div>
<div id="log">{sections or '<p class="muted center">Jurnalul e gol.</p>'}</div>
<p class="muted center" id="nohits" hidden>Nimic nu se potrivește căutării.</p>"""


# ── Documentul complet ───────────────────────────────────────────────────────

def render(report_md: str | None, events: list[dict], tool_results: list[dict], meta: dict) -> str:
    data = collect_data(tool_results)
    start = "raport" if report_md else "jurnal"
    return f"""<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Raport Sateliți · {e(meta.get("when", ""))}</title>
<style>{CSS}</style>
</head>
<body data-start="{start}">
<nav class="nav"><a href="#raport">Raport</a><a href="#jurnal">Jurnal</a></nav>
<main>
  <div class="page" id="raport">{report_page(report_md, data, meta)}</div>
  <div class="page" id="jurnal" hidden>{journal_page(events, meta)}</div>
  <footer class="disclaimer">{e(meta.get("disclaimer", ""))}</footer>
</main>
<script>{JS}</script>
</body>
</html>
"""


CSS = r"""
:root{--text:#1d1d1f;--muted:#6e6e73;--faint:#a1a1a6;--rule:#e5e5ea;--soft:#f5f5f7;--link:#0a58ca;--pos:#1a7f37;--neg:#c62828;--warn:#9a6700;
  --sans:-apple-system,"Segoe UI",system-ui,Roboto,"Helvetica Neue",Arial,sans-serif;--mono:ui-monospace,Consolas,"SF Mono",monospace;color-scheme:light}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:64px}
body{margin:0;background:#fff;color:var(--text);font:16px/1.65 var(--sans);-webkit-font-smoothing:antialiased}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
.nav{position:sticky;top:0;z-index:5;display:flex;justify-content:center;gap:28px;padding:14px 16px;background:rgba(255,255,255,.94);backdrop-filter:blur(8px);border-bottom:1px solid var(--rule)}
.nav a{color:var(--muted);font-size:14px;letter-spacing:.02em}.nav a.on{color:var(--text);font-weight:600}
main{max-width:860px;margin:0 auto;padding:0 16px 64px}
.top{text-align:center;padding:56px 0 24px}
.kicker{margin:0 0 10px;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
h1{font-size:clamp(28px,4.5vw,40px);line-height:1.15;font-weight:650;letter-spacing:-.02em;margin:0 0 14px}
h2{font-size:26px;line-height:1.25;font-weight:650;letter-spacing:-.01em;margin:0 0 6px}
h3{font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:34px 0 8px}
.meta{margin:2px 0;color:var(--muted);font-size:14px}
.toc{margin-top:18px;font-size:15px}
.block{padding:40px 0;border-top:1px solid var(--rule)}.block>h2{text-align:center;margin-bottom:22px}
.prose{font-size:15.5px;color:#2c2c2e}.prose p{margin:0 0 .8em}.prose ul,.prose ol{margin:0 0 .8em;padding-left:1.3em}
.prose li{margin:.25em 0}.prose strong{color:var(--text);font-weight:600}
.prose h1,.prose h2,.prose h3,.prose h4{font-size:15.5px;text-transform:none;letter-spacing:0;color:var(--text);margin:1.2em 0 .4em}
.prose blockquote{margin:0 0 .8em;padding-left:14px;border-left:2px solid var(--rule);color:var(--muted)}
.prose table{border-collapse:collapse;margin:0 auto 1em;font-size:14px}.prose th,.prose td{padding:6px 12px;border-bottom:1px solid var(--rule)}
.prose code{font-family:var(--mono);font-size:.88em;background:var(--soft);padding:.1em .35em;border-radius:4px}
.company{padding:48px 0 40px;border-top:1px solid var(--rule)}
.company>.num{margin:0;font:600 13px var(--mono);color:var(--faint)}
.company>h2{font-size:30px;margin:2px 0 4px}
.ticker{font:500 .62em var(--mono);color:var(--muted);letter-spacing:.04em;vertical-align:.18em;margin-left:6px}
.company>.meta{margin-bottom:22px}
.scroll{overflow-x:auto;margin:26px 0 8px;-webkit-overflow-scrolling:touch}.scroll-tight{margin-top:0}
.cap{margin:30px 0 10px;font-size:13px;color:var(--muted);text-align:center}
.tbl{border-collapse:collapse;margin:0 auto;font-size:14.5px;font-variant-numeric:tabular-nums}
.tbl caption{caption-side:top;padding-bottom:10px;font-size:13px;color:var(--muted);text-align:center}
.tbl th{font-weight:500;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding:8px 14px;border-bottom:1px solid var(--text);text-align:center;white-space:nowrap}
.tbl td{padding:9px 14px;border-bottom:1px solid var(--rule);text-align:center;white-space:nowrap}
.tbl tbody tr:last-child td{border-bottom:0}
.tbl .left{text-align:left}.tbl td.small{white-space:normal;font-size:13px;color:var(--muted);min-width:240px}
.tbl.figures td{font-size:17px;font-weight:600;padding:10px 16px}.tbl.figures th{cursor:help}
.tbl.small{font-size:12.5px}.tbl.small td,.tbl.small th{padding:5px 10px}
.block>.tbl,.block>table{margin-bottom:34px}
.sortable th{cursor:pointer;user-select:none}.sortable th:hover{color:var(--text)}
.sortable th.asc::after{content:" ↑"}.sortable th.desc::after{content:" ↓"}
.sortable td:first-child{font-weight:600}
.pos{color:var(--pos)}.neg{color:var(--neg)}.warn{color:var(--warn)}
.muted{color:var(--muted)}.small{font-size:13px}.center{text-align:center}
.signals{margin-top:34px;padding-top:18px;border-top:1px dashed var(--rule);font-size:14px}
.label{margin:16px 0 6px;font-size:13px;font-weight:600;color:var(--muted)}
ul.plain{list-style:none;margin:0;padding:0}ul.plain li{padding:3px 0}
ul.plain li::before{content:"–";color:var(--faint);margin-right:8px}
ul.links li{font-size:13.5px}
.summary{margin:0 auto 34px}.summary td:last-child{font-family:var(--mono);font-size:13.5px}
.filters{display:flex;flex-direction:column;align-items:center;gap:12px;padding:14px 0 26px;border-top:1px solid var(--rule)}
#q{width:min(420px,100%);border:1px solid var(--rule);border-radius:8px;padding:9px 12px;font:15px var(--sans);color:var(--text);background:#fff}
#q:focus{outline:none;border-color:var(--muted)}
.toggles{display:flex;flex-wrap:wrap;justify-content:center;gap:4px 16px}
.toggle{background:none;border:0;padding:2px 0;font:14px var(--sans);color:var(--faint);cursor:pointer;text-decoration:line-through}
.toggle.on{color:var(--text);text-decoration:none}
.phase{padding:28px 0 12px;border-top:1px solid var(--rule)}.phase>h2{font-size:20px;text-align:center;margin-bottom:18px}
.ev{display:grid;grid-template-columns:64px 62px minmax(0,1fr);gap:10px;padding:4px 0;font-size:14px;align-items:baseline}
.ev .t{font:12px var(--mono);color:var(--faint)}.ev .k{font-size:11.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.06em}
.ev .x{min-width:0;overflow-wrap:anywhere}.ev code{font:13px var(--mono);color:var(--text)}
.ev .quiet{font-size:13px;color:var(--muted)}.ev .quiet.prose p{margin:0 0 .4em}
.ev.iter{display:block;text-align:center;font-size:12px;color:var(--faint);padding:14px 0 6px}
.ev.wide{display:block}.ev.wide .scroll{margin:10px 0 14px}
.ev.hide,.phase.hide{display:none}
@media (max-width:600px){.ev{grid-template-columns:52px minmax(0,1fr)}.ev .k{display:none}.company>h2{font-size:25px}}
.disclaimer{margin-top:24px;padding-top:20px;border-top:1px solid var(--rule);font-size:12.5px;color:var(--muted);text-align:center}
@media print{.nav,.filters{display:none}.page[hidden]{display:block!important}}
"""

JS = r"""
(() => {
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const pages = ['raport', 'jurnal'];
  function show() {
    const h = location.hash.slice(1);
    const el = h && !pages.includes(h) && document.getElementById(h);
    const p = pages.includes(h) ? h : el ? el.closest('.page').id : document.body.dataset.start;
    $$('.page').forEach(x => x.hidden = x.id !== p);
    $$('.nav a').forEach(a => a.classList.toggle('on', a.getAttribute('href') === '#' + p));
    if (el) requestAnimationFrame(() => el.scrollIntoView());
    else if (pages.includes(h)) window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', show);

  document.addEventListener('click', ev => {
    const th = ev.target.closest('table.sortable th');
    if (th) return sortBy(th);
    const tg = ev.target.closest('.toggle');
    if (tg) { tg.classList.toggle('on'); filterLog(); }
  });

  function sortBy(th) {
    const table = th.closest('table'), body = table.tBodies[0], col = +th.dataset.col, num = th.dataset.type === 'num';
    const dir = th.classList.contains('desc') ? 1 : -1;
    $$('th', table).forEach(h => h.classList.remove('asc', 'desc'));
    th.classList.add(dir === 1 ? 'asc' : 'desc');
    const val = tr => { const v = tr.cells[col].dataset.v; return num ? (v === '' ? NaN : +v) : v.toLowerCase(); };
    [...body.rows].sort((a, b) => {
      const x = val(a), y = val(b);
      if (num) { if (isNaN(x)) return 1; if (isNaN(y)) return -1; return (x - y) * dir; }
      return x.localeCompare(y) * dir;
    }).forEach(r => body.appendChild(r));
  }

  const q = $('#q');
  function filterLog() {
    const off = new Set($$('.toggle:not(.on)').map(c => c.dataset.g));
    const term = (q && q.value || '').trim().toLowerCase();
    let shown = 0;
    $$('#log .phase').forEach(ph => {
      let any = 0;
      $$('.ev', ph).forEach(ev => {
        const ok = !off.has(ev.dataset.g) && (!term || ev.textContent.toLowerCase().includes(term));
        ev.classList.toggle('hide', !ok); if (ok) any++;
      });
      ph.classList.toggle('hide', !any); shown += any;
    });
    const nh = $('#nohits'); if (nh) nh.hidden = shown > 0;
  }
  if (q) q.addEventListener('input', filterLog);
  $$('.prose a').forEach(a => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
  show();
})();
"""


# ── Reconstruire dintr-o rulare veche: python -m utils.html_report runs/<data-ora> ─────────────────

def events_from_transcript(messages: list[dict]) -> tuple[list[dict], list[dict], str | None]:
    """Transcript → (evenimente aproximative pentru jurnal, rezultatele uneltelor, raportul Markdown).

    Terminalul original nu e salvat în transcript; jurnalul reconstruit conține gândurile, textul agentului,
    apelurile de unelte și rezultatele lor principale.
    """
    from tools import PHASE_OF  # import târziu: tools importă report_tool, care importă acest modul

    events, results, report, uses, phase, iteration = [], [], None, {}, None, 0
    pending_report = None  # agentul se oprește imediat după raport → ultimul tool_result lipsește din transcript
    ev = lambda kind, **d: events.append({"kind": kind, "t": "", **d})
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            continue
        if msg.get("role") == "assistant":
            iteration += 1
            ev("iteration", n=iteration, max="?")
        for block in content:
            kind = block.get("type")
            if kind == "thinking" and (block.get("thinking") or "").strip():
                ev("thought", text=block["thinking"].strip())
            elif kind == "text" and msg.get("role") == "assistant" and block.get("text", "").strip():
                ev("agent", text=block["text"].strip())
            elif kind == "tool_use":
                uses[block["id"]] = block
                name, args = block.get("name"), block.get("input") or {}
                if PHASE_OF.get(name) and PHASE_OF[name] != phase:
                    phase = PHASE_OF[name]
                    ev("phase", phase=phase, title=_PHASE_TITLES.get(phase, f"Faza {phase}"))
                shown = ", ".join(f"{k}={str(v)[:58]}" for k, v in args.items() if k not in ("filters", "markdown_content"))
                ev("tool", name=name, args=shown)
                if name == "generate_client_report":
                    pending_report = args.get("markdown_content")
                if name == "finviz_dynamic_screener" and args.get("filters"):
                    ev("filters", angle=args.get("angle", ""), rows=[[k, "", v, ""] for k, v in args["filters"].items()])
            elif kind == "tool_result" and block.get("tool_use_id") in uses:
                use = uses[block["tool_use_id"]]
                raw = block.get("content")
                raw = raw if isinstance(raw, str) else "".join(c.get("text", "") for c in raw or [] if isinstance(c, dict))
                try:
                    res = json.loads(raw)
                except ValueError:
                    continue
                if use["name"] == "generate_client_report":
                    if res.get("ok") is not False:
                        report = use["input"].get("markdown_content")
                        ev("markdown", title="raport")
                    else:
                        ev("warn", text=f"generate_client_report: {res.get('error')}")
                    continue
                results.append({"name": use["name"], "args": use.get("input") or {}, "result": res})
                _result_events(ev, use["name"], res)
    if report is None and pending_report:
        report = pending_report
        ev("markdown", title="raport")
    return events, results, report


_PHASE_TITLES = {1: "Faza 1 — Jocul cu filtrele FinViz", 2: "Faza 2 — Autopsia Contabilă",
                 3: "Faza 3 — Testul de Paranoia (OSINT)", 4: "Faza 4 — Raportul „Vulpea de pe Wall Street”"}


def _result_events(ev, name: str, res: dict) -> None:
    if res.get("ok") is False:
        ev("warn", text=f"{name}: {res.get('error')}" + (f"  → {res['hint']}" if res.get("hint") else ""))
    elif name == "finviz_dynamic_screener":
        count = res.get("count") or 0
        level = "ok" if 5 <= count <= 12 else "bad" if count <= 2 or count > 30 else "mid"
        ev("count", count=count, guidance=res.get("guidance", ""), level=level)
        rows = res.get("rows") or []
        if rows:
            ev("table", title=f"Top {len(rows)} (după market cap)", columns=["Ticker", "Companie", "Sector", "P/E", "P/FCF"],
               rows=[[r.get("ticker"), r.get("company"), r.get("sector"), r.get("pe"), r.get("p_fcf")] for r in rows])
    elif name == "deep_fundamental_inspector":
        s, years = res.get("series") or {}, res.get("years") or []
        if years and s:
            ev("table", title=f"📊 {res.get('ticker')} — {res.get('name') or ''} ({res.get('currency') or '?'}, mil.)",
               columns=["An", "Venituri", "Creștere %", "Marjă op. %", "Profit net", "FCF", "Datorie", "Cash"],
               rows=[[y, *[(s.get(k) or [None] * len(years))[i] for k in
                           ("revenue", "revenue_growth_pct", "op_margin_pct", "net_income", "fcf", "total_debt", "cash")]]
                     for i, y in enumerate(years)])
        for flag in res.get("red_flags") or []:
            ev("error" if str(flag).startswith("[high]") else "warn", text=flag)
        for flag in res.get("green_flags") or []:
            ev("success", text=flag)
        if res.get("verdict"):
            ev("verdict", verdict=res["verdict"], quality=res.get("data_quality", ""))
    elif name == "paranoid_osint_radar":
        threats = res.get("top_threats") or []
        if threats:
            ev("table", title=f"🕵️  Radar OSINT — {res.get('company') or ''} ({res.get('ticker') or ''})",
               columns=["Sursă", "Riscuri", "Titlu"],
               rows=[[t.get("source"), ", ".join(t.get("tags") or []), t.get("title")] for t in threats])
        ev("info", text="Surse: " + ", ".join(f"{k}={v}" for k, v in (res.get("sources_status") or {}).items()))


def rebuild(run_dir) -> str:
    """Scrie runs/<data-ora>/raport_sateliti.html din transcript.json; întoarce calea."""
    from pathlib import Path
    from tools.report_tool import DISCLAIMER

    run_dir = Path(run_dir)
    data = json.loads((run_dir / "transcript.json").read_text(encoding="utf-8"))
    state = data.get("state") or {}
    events, results, report = events_from_transcript(data.get("messages") or [])
    if not state.get("report_done"):
        report = None
    summary = [["Iterații", state.get("iterations", "–")], ["Oprire", state.get("stop_reason") or "–"],
               *[[f"Apeluri {k}", v] for k, v in (state.get("calls") or {}).items()],
               *[[k, f"{v:,}"] for k, v in (state.get("usage") or {}).items()]]
    when = run_dir.name
    try:
        import datetime as dt
        when = f"{dt.datetime.strptime(run_dir.name, '%Y%m%d-%H%M%S'):%Y-%m-%d %H:%M}"
    except ValueError:
        pass
    old_md = run_dir / "raport_sateliti.md"  # rapoartele vechi aveau modelul în antet
    found = re.search(r"model `([^`]+)`", old_md.read_text(encoding="utf-8")[:400]) if old_md.exists() else None
    model = found.group(1) if found else ""
    meta = {"model": model or "–", "when": when, "run": run_dir.name, "summary": summary,
            "stop_reason": state.get("stop_reason", ""), "disclaimer": DISCLAIMER}
    out = run_dir / "raport_sateliti.html"
    out.write_text(render(report, events, results, meta), encoding="utf-8")
    return str(out)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Folosire: python -m utils.html_report runs/<data-ora> [runs/<alta> ...]")
        sys.exit(2)
    for folder in sys.argv[1:]:
        print(rebuild(folder))
