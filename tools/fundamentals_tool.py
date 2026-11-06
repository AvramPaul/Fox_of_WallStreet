"""deep_fundamental_inspector — autopsia contabilă a unei companii (yfinance, ultimii 3-4 ani).

Două straturi: `_fetch_raw` (rețea, retry, cache → JSON) și `analyze` (pur, fără rețea), care
calculează raporturile și aplică euristicile de „value trap”.
"""

import logging
import re

import yfinance as yf

import config
from utils import console as ui
from utils.resilience import (DiskCache, ToolError, make_key, rate_limiter,
                              safe_tool, with_retry)

SETTINGS = config.load()
MAX_YEARS = 4
_cache = DiskCache("yfinance", SETTINGS.cache_ttl["yfinance"])
logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # erorile le raportăm noi, structurat

# metrică → (situație financiară, nume alternative de rânduri, în ordinea preferinței)
ROWS = {
    "revenue": ("income", ["Total Revenue", "Operating Revenue"]),
    "op_income": ("income", ["Operating Income", "Total Operating Income As Reported", "EBIT"]),
    "net_income": ("income", ["Net Income", "Net Income Common Stockholders",
                              "Net Income Continuous Operations"]),
    "unusual": ("income", ["Total Unusual Items", "Total Unusual Items Excluding Goodwill"]),
    "fcf": ("cashflow", ["Free Cash Flow"]),
    "ocf": ("cashflow", ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]),
    "capex": ("cashflow", ["Capital Expenditure"]),
    "total_debt": ("balance", ["Total Debt"]),
    "cash": ("balance", ["Cash Cash Equivalents And Short Term Investments",
                         "Cash And Cash Equivalents"]),
    "shares": ("balance", ["Ordinary Shares Number", "Share Issued"]),
}
CORE = ("revenue", "fcf", "op_income", "total_debt")


# ── Descărcare ───────────────────────────────────────────────────────────────

def normalize_ticker(ticker: str) -> str:
    t = ticker.strip().upper()
    # FinViz scrie clasele de acțiuni cu punct (BRK.B), Yahoo cu cratimă (BRK-B)
    return t.replace(".", "-") if re.fullmatch(r"[A-Z]+\.[A-Z]", t) else t


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN → None


def _extract(df, names: list[str]) -> dict[int, float]:
    """{an: valoare} din primul rând existent dintre `names`."""
    if df is None or getattr(df, "empty", True):
        return {}
    for name in names:
        if name in df.index:
            out = {}
            for col, v in df.loc[name].items():
                v = _num(v)
                year = getattr(col, "year", None)
                if v is not None and year and year not in out:
                    out[year] = v
            if out:
                return out
    return {}


def _ttm(df, names: list[str]) -> float | None:
    """Suma ultimelor 4 trimestre disponibile."""
    if df is None or getattr(df, "empty", True):
        return None
    for name in names:
        if name in df.index:
            vals = [v for v in (_num(x) for x in df.loc[name].values[:4]) if v is not None]
            return sum(vals) if len(vals) == 4 else None
    return None


@with_retry("yfinance")
def _fetch_raw(ticker: str) -> dict:
    rate_limiter.wait("yfinance")
    t = yf.Ticker(ticker)
    statements = {"income": t.income_stmt, "cashflow": t.cashflow, "balance": t.balance_sheet}
    series = {m: _extract(statements[src], names) for m, (src, names) in ROWS.items()}
    q = t.quarterly_income_stmt
    try:
        info = t.info or {}
    except Exception:  # noqa: BLE001 — .info e notoriu instabil; continuăm fără el
        info = {}
    return {
        "ticker": ticker,
        "info": {k: info.get(k) for k in ("longName", "sector", "industry", "marketCap",
                                          "trailingPE", "forwardPE", "financialCurrency")},
        # chei JSON → string; le convertim înapoi în analyze()
        "series": {m: {str(y): v for y, v in s.items()} for m, s in series.items()},
        "ttm": {"unusual": _ttm(q, ROWS["unusual"][1]), "net_income": _ttm(q, ROWS["net_income"][1])},
    }


# ── Analiză (pură) ───────────────────────────────────────────────────────────

def _pct(a, b):
    return None if a is None or not b else a / b


def _cagr(first, last, years):
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def _r(v, nd=1):
    return None if v is None else round(v, nd)


def analyze(raw: dict) -> dict:
    s = {m: {int(y): v for y, v in vals.items()} for m, vals in raw["series"].items()}
    years = sorted(s["revenue"] or s["fcf"])[-MAX_YEARS:]
    if not years:
        raise ToolError(f"Nu am găsit situații financiare pentru {raw['ticker']}.",
                        hint="Verifică tickerul (Yahoo Finance) sau sari peste companie.")
    col = {m: [s[m].get(y) for y in years] for m in s}

    # FCF lipsă → OCF + CapEx
    col["fcf"] = [f if f is not None else (o + c if o is not None and c is not None else None)
                  for f, o, c in zip(col["fcf"], col["ocf"], col["capex"])]

    first, last = 0, len(years) - 1
    span = years[last] - years[first]
    latest = {m: col[m][last] for m in col}
    op_margin = [_pct(o, r) for o, r in zip(col["op_income"], col["revenue"])]
    fcf_margin = [_pct(f, r) for f, r in zip(col["fcf"], col["revenue"])]
    rev_growth = [None] + [_pct(b - a, a) if a and b is not None else None
                           for a, b in zip(col["revenue"], col["revenue"][1:])]
    net_debt = (latest["total_debt"] - latest["cash"]
                if latest["total_debt"] is not None and latest["cash"] is not None else None)
    fcfs = [f for f in col["fcf"] if f is not None]
    nis = [n for n in col["net_income"] if n is not None]
    sum_fcf, sum_ni = (sum(fcfs) if fcfs else None), (sum(nis) if nis else None)
    pe = raw["info"].get("trailingPE")
    rev_cagr = _cagr(col["revenue"][first], col["revenue"][last], span)
    shares_cagr = _cagr(col["shares"][first], col["shares"][last], span)

    ratios = {
        "revenue_cagr_pct": _r(rev_cagr * 100) if rev_cagr is not None else None,
        "op_margin_change_pp": (_r((op_margin[last] - op_margin[first]) * 100)
                                if op_margin[first] is not None and op_margin[last] is not None else None),
        "ni_to_fcf_cumulative": _r(_pct(sum_ni, sum_fcf), 2) if sum_fcf and sum_fcf > 0 else None,
        "ni_to_fcf_latest": (_r(latest["net_income"] / latest["fcf"], 2)
                             if latest["net_income"] is not None and latest["fcf"] and latest["fcf"] > 0 else None),
        # ponderea one-off-urilor are sens doar când profitul e pozitiv
        "unusual_share_latest_pct": _r(latest["unusual"] / latest["net_income"] * 100
                                       if latest["unusual"] is not None and (latest["net_income"] or 0) > 0 else None),
        "unusual_share_ttm_pct": _r(raw["ttm"]["unusual"] / raw["ttm"]["net_income"] * 100
                                    if raw["ttm"]["unusual"] is not None and (raw["ttm"]["net_income"] or 0) > 0
                                    else None),
        "net_debt_mln": _r(net_debt / 1e6, 0) if net_debt is not None else None,
        "net_debt_to_fcf": (_r(net_debt / latest["fcf"], 2)
                            if net_debt is not None and latest["fcf"] and latest["fcf"] > 0 else None),
        "shares_cagr_pct": _r(shares_cagr * 100, 2) if shares_cagr is not None else None,
        "trailing_pe": _r(pe, 1) if isinstance(pe, (int, float)) else None,
    }

    # La bănci/asigurători depozitele apar ca datorie și FCF-ul nu măsoară nimic util.
    is_financial = (raw["info"].get("sector") or "") == "Financial Services"
    red, green = [], []
    neg_fcf = sum(1 for f in fcfs if f < 0)
    if not is_financial and ((latest["fcf"] is not None and latest["fcf"] < 0) or neg_fcf >= 2):
        red.append(("high", f"ARDE BANI: FCF negativ în {neg_fcf} din {len(fcfs)} ani"))
    if not is_financial and sum_ni and sum_ni > 0 and sum_fcf is not None:
        if sum_fcf <= 0:
            red.append(("high", "PROFIT DE HÂRTIE: profit contabil pozitiv, dar cash-ul liber cumulat ≤ 0"))
        elif sum_ni / sum_fcf > 1.5:
            red.append(("high", f"PROFIT DE HÂRTIE: profitul e de {sum_ni / sum_fcf:.1f}× cash-ul liber"))
        elif (ratios["ni_to_fcf_latest"] or 0) > 1.5:
            red.append(("medium", f"PROFITUL NU AJUNGE ÎN BANCĂ (ultimul an): profit = "
                                  f"{ratios['ni_to_fcf_latest']}× FCF — verifică dacă e capex de investiții "
                                  f"sau cash blocat în stocuri/creanțe"))
    for label, share in (("ultimul an", ratios["unusual_share_latest_pct"]),
                         ("ultimele 4 trimestre", ratios["unusual_share_ttm_pct"])):
        if share is not None and share > 30:
            red.append(("high", f"PROFIT UMFLAT DE ONE-OFF: {share:.0f}% din profit vine din elemente "
                                f"neobișnuite ({label}, ex. vânzare de active)"))
            break
    fcf_falling = len(fcfs) >= 2 and fcfs[-1] < fcfs[0]
    if ratios["trailing_pe"] is not None and ratios["trailing_pe"] < 12 and fcf_falling:
        red.append(("medium", f"P/E-UL MINTE: P/E {ratios['trailing_pe']} dar FCF-ul scade"))
    if ratios["revenue_cagr_pct"] is not None and ratios["revenue_cagr_pct"] < 0:
        red.append(("medium", f"AFACERE ÎN CONTRACȚIE: venituri {ratios['revenue_cagr_pct']}%/an"))
    if ratios["op_margin_change_pp"] is not None and ratios["op_margin_change_pp"] < -5:
        red.append(("medium", f"MARJE ERODATE: marja operațională {ratios['op_margin_change_pp']} pp"))
    nd_fcf = ratios["net_debt_to_fcf"]
    debt_up = (col["total_debt"][first] is not None and latest["total_debt"] is not None
               and latest["total_debt"] > col["total_debt"][first] * 1.1)
    cash_down = (col["cash"][first] is not None and latest["cash"] is not None
                 and latest["cash"] < col["cash"][first] * 0.9)
    if is_financial:
        pass
    elif nd_fcf is not None and nd_fcf > 4:
        red.append(("high", f"ÎNDATORARE PERICULOASĂ: datoria netă = {nd_fcf} ani de FCF"))
    elif net_debt is not None and net_debt > 0 and debt_up and cash_down:
        red.append(("medium", "ÎNDATORARE ÎN CREȘTERE: datoria urcă în timp ce cash-ul scade"))
    if ratios["shares_cagr_pct"] is not None and ratios["shares_cagr_pct"] > 5:
        red.append(("medium", f"DILUARE: numărul de acțiuni crește {ratios['shares_cagr_pct']}%/an"))

    if not is_financial and fcfs and len(fcfs) >= 3 and all(f > 0 for f in fcfs):
        green.append("FCF pozitiv în fiecare an" + (" și în creștere" if fcfs[-1] > fcfs[0] else ""))
    if not is_financial and net_debt is not None and net_debt < 0:
        green.append("Cash net: are mai mulți bani decât datorii")
    if ratios["revenue_cagr_pct"] is not None and ratios["revenue_cagr_pct"] >= 10:
        green.append(f"Venituri în creștere solidă: {ratios['revenue_cagr_pct']}%/an")
    if ratios["op_margin_change_pp"] is not None and ratios["op_margin_change_pp"] > 2:
        green.append(f"Marje în expansiune: +{ratios['op_margin_change_pp']} pp")
    if ratios["shares_cagr_pct"] is not None and ratios["shares_cagr_pct"] < -1:
        green.append("Răscumpără acțiuni (buybacks)")

    missing = [m for m in CORE if all(v is None for v in col[m])
               and not (is_financial and m == "op_income")]
    notes = []
    if missing:
        notes.append(f"Lipsesc date pentru: {', '.join(missing)}")
    if is_financial:
        notes.append("Companie financiară: testele de FCF și datorie nu se aplică (depozitele apar ca "
                     "datorie) — judecă după profit, ROE și calitatea creditelor")

    severities = {sev for sev, _ in red}
    verdict = ("FAIL" if "high" in severities
               else "WARN" if severities or missing or is_financial else "PASS")

    def mln(vals):
        return [None if v is None else round(v / 1e6) for v in vals]

    def pct(vals):
        return [None if v is None else round(v * 100, 1) for v in vals]

    info = raw["info"]
    return {
        "ticker": raw["ticker"],
        "name": info.get("longName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": info.get("financialCurrency"),
        "units": "milioane în moneda raportării",
        "years": years,
        "series": {
            "revenue": mln(col["revenue"]), "revenue_growth_pct": pct(rev_growth),
            "op_margin_pct": pct(op_margin), "net_income": mln(col["net_income"]),
            "fcf": mln(col["fcf"]), "fcf_margin_pct": pct(fcf_margin), "capex": mln(col["capex"]),
            "total_debt": mln(col["total_debt"]), "cash": mln(col["cash"]),
            "unusual_items": mln(col["unusual"]),
        },
        "ratios": ratios,
        "red_flags": [f"[{sev}] {msg}" for sev, msg in red],
        "green_flags": green,
        "verdict": verdict,
        "data_quality": "partial" if missing else "full",
        **({"notes": notes} if notes else {}),
    }


# ── Tool-ul ──────────────────────────────────────────────────────────────────

def _show(result: dict) -> None:
    s = result["series"]
    ui.show_table(f"📊 {result['ticker']} — {result.get('name') or ''} ({result.get('currency') or '?'}, mil.)",
                  ["An", "Venituri", "Creștere %", "Marjă op. %", "Profit net", "FCF", "Datorie", "Cash"],
                  [[y, s["revenue"][i], s["revenue_growth_pct"][i], s["op_margin_pct"][i],
                    s["net_income"][i], s["fcf"][i], s["total_debt"][i], s["cash"][i]]
                   for i, y in enumerate(result["years"])])
    for flag in result["red_flags"]:
        (ui.error if flag.startswith("[high]") else ui.warn)(flag)
    for flag in result["green_flags"]:
        ui.success(flag)
    for note in result.get("notes", []):
        ui.info(note)
    color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}[result["verdict"]]
    ui.console.print(f"[bold {color}]Verdict: {result['verdict']}[/bold {color}]"
                     f"  [dim](date: {result['data_quality']})[/dim]")


@safe_tool
def deep_fundamental_inspector(ticker: str) -> dict:
    """Autopsia contabilă: FCF, venituri, marje, datorii pe 3-4 ani + semnale de value trap."""
    symbol = normalize_ticker(ticker)
    key = make_key(symbol)
    raw = _cache.get(key)
    if raw is None:
        raw = _fetch_raw(symbol)
        if any(raw["series"].values()):
            _cache.set(key, raw)
    else:
        ui.info(f"yfinance: {symbol} din cache")
    result = analyze(raw)
    _show(result)
    return result


if __name__ == "__main__":
    import sys
    for t in sys.argv[1:] or ["MSFT"]:
        print(deep_fundamental_inspector(t))
