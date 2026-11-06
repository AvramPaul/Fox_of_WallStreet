"""finviz_dynamic_screener — screener FinViz cu filtre setate dinamic de agent.

Filtrele se validează și se traduc în cod URL cu catalogul `filter_dict` din finvizfinance.
Pagina se descarcă prin sesiunea finvizfinance (retry intern pe 5xx/Cloudflare), iar datele
se citesc din JSON-ul React încorporat (`script[data-ssr-props]`): FinViz nu mai servește
tabelul HTML pe care îl parsa finvizfinance 1.5.0. Parserul vechi rămâne ca fallback.
"""

import difflib
import json
import re

from finvizfinance.constants import filter_dict
from finvizfinance.exceptions import FinvizBlockedError
from finvizfinance.util import web_scrap

import config
from utils import console as ui
from utils.resilience import (DiskCache, ToolError, TransientError, make_key,
                              rate_limiter, safe_tool, with_retry)

SETTINGS = config.load()
URL = "https://finviz.com/screener.ashx"
MAX_ROWS = 15
SAMPLE_ROWS = 8
_cache = DiskCache("finviz", SETTINGS.cache_ttl["finviz"])
_last_filters: dict[str, str] | None = None

# Coloanele vistei Custom (v=151); JSON-ul întoarce oricum toți indicatorii de mai jos.
COLUMNS = "0,1,2,3,4,5,6,7,8,9,13,20,21,27,28,33,34,35,37,40,41,65"

# câmp JSON FinViz → nume compact în rezultatul pentru agent
NUMBER_FIELDS = {
    "marketCap": "mcap_bln", "PE": "pe", "forwardPE": "fwd_pe", "PEG": "peg", "PFCF": "p_fcf",
    "estLTGrowth": "eps_next5y", "sales5Years": "sales_past5y", "roe": "roe", "roi": "roi",
    "operMargin": "op_margin", "netMargin": "net_margin", "ltdebtEq": "lt_debt_eq",
    "curRatio": "cur_ratio", "insiderTrans": "insider_trans", "instOwn": "inst_own", "price": "price",
}

# Filtrele prezentate agentului în descrierea tool-ului (restul din filter_dict sunt și ele valide).
CATALOG_KEYS = [
    "Market Cap.", "Country", "Sector", "P/E", "Forward P/E", "PEG", "Price/Free Cash Flow",
    "EPS growthnext 5 years", "EPS growthpast 5 years", "Sales growthpast 5 years",
    "Sales growthqtr over qtr", "Return on Investment", "Return on Equity", "Operating Margin",
    "Net Profit Margin", "Gross Margin", "LT Debt/Equity", "Debt/Equity", "Current Ratio",
    "InsiderTransactions", "InsiderOwnership", "InstitutionalOwnership", "Float Short",
    "200-Day Simple Moving Average", "RSI (14)", "Beta", "Average Volume",
]


# ── Rezolvarea cheilor și valorilor ──────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9<>%.\-]", "", str(s).lower().replace("+", ""))


_EXTRA_ALIASES = {
    "pfcf": "Price/Free Cash Flow", "p/fcf": "Price/Free Cash Flow", "price/fcf": "Price/Free Cash Flow",
    "roic": "Return on Investment", "roi": "Return on Investment", "roe": "Return on Equity",
    "roa": "Return on Assets", "fwdp/e": "Forward P/E", "forwardpe": "Forward P/E", "pe": "P/E",
    "ltdebt/eq": "LT Debt/Equity", "longtermdebt/equity": "LT Debt/Equity", "d/e": "Debt/Equity",
    "marketcap": "Market Cap.", "mcap": "Market Cap.", "epsnext5y": "EPS growthnext 5 years",
    "epsgrowthnext5y": "EPS growthnext 5 years", "salespast5y": "Sales growthpast 5 years",
    "salesgrowth5y": "Sales growthpast 5 years", "sma200": "200-Day Simple Moving Average",
    "sma50": "50-Day Simple Moving Average", "rsi": "RSI (14)", "opmargin": "Operating Margin",
    "netmargin": "Net Profit Margin", "currentratio": "Current Ratio",
}
ALIASES = {_norm(k): k for k in filter_dict} | {_norm(k): v for k, v in _EXTRA_ALIASES.items()}


def valid_options(key: str) -> list[str]:
    # finvizfinance 1.5.0 are câteva etichete trunchiate (ex. "Very Negative (") — le ascundem.
    return [o for o in filter_dict[key]["option"] if o != "Any" and o.count("(") == o.count(")")]


def resolve_key(raw: str) -> str:
    key = ALIASES.get(_norm(raw))
    if key:
        return key
    close = difflib.get_close_matches(_norm(raw), ALIASES, n=4, cutoff=0.5)
    suggestions = sorted({ALIASES[c] for c in close})
    raise ToolError(f"Filtru necunoscut: {raw!r}.",
                    hint=f"Ai vrut: {suggestions}? Filtre uzuale: {CATALOG_KEYS}")


def resolve_value(key: str, raw) -> str:
    raw = str(raw).strip()
    options = valid_options(key) + ["Any"]
    if raw in options:
        return raw
    # „<0.5” → „Under 0.5”, „>15%” → „Over 15%”
    m = re.fullmatch(r"([<>])\s*([-+]?\d+(?:\.\d+)?\s*%?)", raw)
    candidate = f"{'Under' if m.group(1) == '<' else 'Over'} {m.group(2)}" if m else raw
    by_norm = {_norm(o): o for o in options}
    hit = by_norm.get(_norm(candidate))
    if hit:
        return hit
    raise ToolError(f"Valoare invalidă pentru {key!r}: {raw!r}.",
                    hint=f"Valori valide: {valid_options(key)}")


def resolve_filters(filters: dict) -> dict[str, str]:
    if not isinstance(filters, dict):
        raise ToolError("`filters` trebuie să fie un obiect {filtru: valoare}.")
    resolved = {}
    for raw_key, raw_value in filters.items():
        key = resolve_key(raw_key)
        value = resolve_value(key, raw_value)
        if value != "Any":
            resolved[key] = value
    return resolved


def build_filter_param(filters: dict[str, str]) -> str:
    codes = (f"{filter_dict[k]['prefix']}_{filter_dict[k]['option'][v]}" for k, v in filters.items())
    return ",".join(sorted(codes))


# ── Descărcare și parsare ────────────────────────────────────────────────────

def _map_row(row: dict) -> dict:
    text, nums = row.get("text", {}), row.get("numbers", {})
    out = {k: text.get(k) for k in ("ticker", "company", "sector", "industry", "country")}
    for src, dst in NUMBER_FIELDS.items():
        v = nums.get(src)
        if isinstance(v, (int, float)):
            v = v / 1000 if src == "marketCap" else v  # FinViz dă market cap în milioane
            v = round(v, 4 if src == "insiderTrans" else 2)
        out[dst] = v
    return out


def _parse_json_island(soup) -> dict | None:
    for script in soup.find_all("script", attrs={"data-ssr-props": True}):
        try:
            props = json.loads(script.string or "")
        except ValueError:
            continue
        data = props.get("data") if isinstance(props, dict) else None
        if isinstance(data, dict) and "rows" in data:
            return {"count": int(data.get("totalCount") or 0),
                    "rows": [_map_row(r) for r in data["rows"]]}
    return None


def _fetch_legacy(filters: dict[str, str]) -> dict:
    """Parserul HTML original din finvizfinance (pentru cazul în care FinViz revine la vechiul layout)."""
    from finvizfinance.screener.custom import Custom
    screener = Custom()
    screener.set_filter(filters_dict=filters)
    df = screener.screener_view(order="Market Cap.", ascend=False, limit=MAX_ROWS, verbose=0,
                                columns=[int(c) for c in COLUMNS.split(",")])
    if df is None:
        return {"count": 0, "rows": []}
    rows = [{"ticker": r.get("Ticker"), "company": r.get("Company"), "sector": r.get("Sector"),
             "industry": r.get("Industry"), "pe": r.get("P/E"), "p_fcf": r.get("P/Free Cash Flow"),
             "peg": r.get("PEG"), "roe": r.get("Return on Equity")} for r in df.to_dict("records")]
    return {"count": len(rows), "count_is_lower_bound": True, "rows": rows}


@with_retry("finviz", attempts=2)  # finvizfinance face deja retry intern
def _fetch(filters: dict[str, str]) -> dict:
    rate_limiter.wait("finviz")
    params = {"v": "151", "o": "-marketcap", "c": COLUMNS}
    if filters:
        params["f"] = build_filter_param(filters)
    try:
        soup = web_scrap(URL, params)
    except FinvizBlockedError as exc:
        raise TransientError(f"FinViz a blocat cererea: {exc}") from exc
    parsed = _parse_json_island(soup)
    if parsed is not None:
        return parsed | {"parser": "json"}
    ui.warn("FinViz: JSON-ul încorporat lipsește — încerc parserul HTML vechi")
    return _fetch_legacy(filters) | {"parser": "legacy-html"}


# ── Regula de iterare ────────────────────────────────────────────────────────

def guidance(count: int) -> str:
    if count <= 2:
        return ("PREA PUȚINE: relaxează UN singur filtru (cel mai restrictiv) și reîncearcă.")
    if count > 30:
        return ("PREA MULTE: strânge șurubul (ex. P/FCF mai mic, LT Debt/Equity mai mic, "
                "ROE/ROI mai mare) și reîncearcă.")
    if 5 <= count <= 12:
        return "ȚINTĂ ATINSĂ: listă scurtă de elită (5-12)."
    return "APROAPE: ajustează fin un filtru ca să ajungi la 5-12 companii."


# ── Tool-ul ──────────────────────────────────────────────────────────────────

@safe_tool
def finviz_dynamic_screener(filters: dict, angle: str = "") -> dict:
    """Rulează screener-ul FinViz cu filtrele date; întoarce numărul total și top companii."""
    global _last_filters
    resolved = resolve_filters(filters)
    ui.show_filters_diff(_last_filters, resolved, angle)
    _last_filters = resolved

    key = make_key(build_filter_param(resolved))
    result = _cache.get(key)
    if result is None:
        result = _fetch(resolved)
        _cache.set(key, result)
    else:
        ui.info("FinViz: rezultat din cache")

    count = result["count"]
    # Peste 30 de rezultate agentul oricum trebuie să strângă filtrele → îi dăm doar un eșantion.
    rows = result["rows"][:MAX_ROWS if count <= 30 else SAMPLE_ROWS]
    advice = guidance(count)
    ui.show_screener_count(count, advice)
    if rows:
        ui.show_table(f"Top {len(rows)} (după market cap)",
                      ["Ticker", "Companie", "P/E", "P/FCF", "PEG", "ROE%", "LTD/E", "EPS 5Y%"],
                      [[r["ticker"], (r.get("company") or "")[:28], r.get("pe"), r.get("p_fcf"),
                        r.get("peg"), r.get("roe"), r.get("lt_debt_eq"), r.get("eps_next5y")]
                       for r in rows])
    return {"angle": angle, "filters_applied": resolved, "count": count, "guidance": advice,
            "rows": rows, **({"count_is_lower_bound": True} if result.get("count_is_lower_bound") else {})}


# ── Catalog pentru descrierea tool-ului ──────────────────────────────────────

def _compress(options: list[str]) -> str:
    """„Under 5, Under 10, …” → „Under {5,10,…}” ca să economisim tokeni."""
    groups: dict[str, list[str]] = {}
    other = []
    for o in options:
        m = re.fullmatch(r"(Under|Over) ([-+]?[\d.]+%?)", o)
        if m:
            groups.setdefault(m.group(1), []).append(m.group(2))
        else:
            other.append(o)
    parts = [f"{w} {{{','.join(vals)}}}" for w, vals in groups.items()] + other
    return " | ".join(parts)


def filter_catalog() -> str:
    lines = []
    for key in CATALOG_KEYS:
        opts = valid_options(key)
        if len(opts) > 20 and not any(re.fullmatch(r"(Under|Over) .*", o) for o in opts):
            opts = opts[:12] + ["… (eroarea de validare listează toate valorile)"]
        lines.append(f"- {key}: {_compress(opts)}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(filter_catalog())
    print(finviz_dynamic_screener({"Market Cap.": "+Mid (over $2bln)", "P/E": "<25", "PEG": "<2",
                                   "LT Debt/Equity": "<0.5"}, angle="GARP demo"))
