"""paranoid_osint_radar — red-teaming OSINT împotriva unei companii.

Surse gratuite, izolate (una căzută nu le oprește pe celelalte): DuckDuckGo (știri + web),
HackerNews (Algolia), GitHub Search, Reddit JSON. Reddit refuză adesea clienții neautentificați
(403); atunci îl marcăm blocat pentru toată rularea — fără reîncercări sau endpoint-uri
alternative — și citim discuțiile publice prin rezultatele DuckDuckGo `site:reddit.com`.
"""

import datetime as dt
import math
import re
import time

import requests
from ddgs import DDGS
from ddgs.exceptions import DDGSException

import config
from utils import console as ui
from utils.resilience import (DiskCache, ToolError, make_key, rate_limiter,
                              safe_tool, with_retry)

SETTINGS = config.load()
_cache = DiskCache("osint", SETTINGS.cache_ttl["osint"])
_reddit_blocked = False  # setat la primul 403/429; valabil pe toată rularea

MAX_HITS = 25
TOP_THREATS = 12
MAX_EXTRA_QUERIES = 3

RISK_PATTERNS = {
    "litigation": r"lawsuit|sued|suing|class action|settlement|litigation",
    "regulator": r"\bSEC\b|\bDOJ\b|\bFTC\b|investigation|probe|antitrust|subpoena|\bfined\b|penalt",
    "fraud_short": r"fraud|restatement|irregularit|short[- ]seller|hindenburg|muddy waters|whistleblower",
    "policy_subsidy": r"subsid|tariff|tax credit|sanction|export control|\bban(ned|s)?\b|regulation",
    "open_source_clone": r"open[- ]source|self[- ]hosted|alternative to|\bclone\b|free alternative",
    "ai_disruption": r"\bAI\b|ChatGPT|\bLLMs?\b|\bGPT|AI agent|generative",
    "customer_churn": r"leaving|cancel|switch(ed|ing)? (from|to|away)|migrat|churn|\bquit\b|moved away|ditch",
    "china_competition": r"\bChina\b|Chinese|Huawei|\bBYD\b|Temu|Shein|Alibaba",
    "distress": r"bankrupt|chapter 11|layoffs?|lay(s|ing)? off|going concern|default|delist|plunge|collapse|"
                r"wiped out|last legs",
}
_RISK_RE = {tag: re.compile(p, re.I) for tag, p in RISK_PATTERNS.items()}

_SUFFIXES = re.compile(r"[,.]?\s+(inc|corp|corporation|co|company|ltd|plc|holdings?|group|sa|se|nv|ag|"
                       r"adr|limited|the)\.?$", re.I)


# ── Funcții pure ─────────────────────────────────────────────────────────────

def short_name(company: str) -> str:
    """„Alphabet Inc.” → „Alphabet”, „Shell Plc ADR” → „Shell”."""
    name = company.strip()
    while True:
        stripped = _SUFFIXES.sub("", name).strip(" ,.")
        if stripped == name or not stripped:
            return name
        name = stripped


def tag_text(text: str) -> list[str]:
    return [tag for tag, rx in _RISK_RE.items() if rx.search(text or "")]


def _hit(source, title, url, date=None, signal=0, snippet="", extra_tags=()):
    title, snippet = (title or "").strip(), re.sub(r"\s+", " ", snippet or "").strip()
    return {"source": source, "title": title[:160], "url": url, "date": (date or "")[:10],
            "signal": int(signal or 0), "tags": sorted(set(tag_text(f"{title} {snippet}")) | set(extra_tags)),
            "snippet": snippet[:200]}


def _title_words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 3}


def _same_story(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= 0.4


def rank(hits: list[dict]) -> list[dict]:
    """Ordonare (câte riscuri atinge, apoi viralitatea) + deduplicare după URL și după aceeași știre
    preluată de mai multe publicații (titluri asemănătoare) — păstrăm varianta cea mai bine clasată."""
    ordered = sorted(hits, key=lambda h: (len(h["tags"]), math.log1p(max(h["signal"], 0))), reverse=True)
    seen_urls, kept, kept_words = set(), [], []
    for h in ordered:
        words = _title_words(h["title"])
        if not h["url"] or h["url"] in seen_urls or any(_same_story(words, k) for k in kept_words):
            continue
        seen_urls.add(h["url"])
        kept.append(h)
        kept_words.append(words)
    return kept


def threat_summary(hits: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for h in hits:
        for t in h["tags"]:
            counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def build_queries(name: str, product: str) -> dict[str, list[str]]:
    # DDG întoarce „No results” la operatori (ghilimele/OR/paranteze) → doar cuvinte-cheie.
    q = {
        "ddg_news": [f"{name} lawsuit investigation", f"{name} competition AI disruption",
                     f"{name} regulation subsidies tariffs"],
        "ddg_text": [f"{name} short seller report"],
        "reddit": [f"{name} alternative OR cancel OR leaving OR switched"],  # API-ul Reddit acceptă OR
        "reddit_ddg": [f"site:reddit.com {name}"],  # largă: etichetele de risc fac clasificarea
        "hn": [name],
        "github": [],
    }
    if product:
        q["hn"].append(f"{product} open source")
        q["github"].append(product)
        q["ddg_text"].append(f"open source alternative to {name}")
    return q


def is_relevant(hit: dict, name: str, product: str = "") -> bool:
    """Rezultatul menționează compania sau (pentru căutările pe produs) cuvintele-cheie ale produsului."""
    text = f"{hit['title']} {hit['snippet']} {hit['url']}".lower()
    if hit["source"] == "github" or name.lower() in text:
        return True
    words = [w for w in re.findall(r"[a-z0-9]+", product.lower()) if len(w) >= 4]
    return bool(words) and all(w in text for w in words)


# ── Surse ────────────────────────────────────────────────────────────────────

def _headers(extra=None):
    return {"User-Agent": SETTINGS.user_agent} | (extra or {})


def _cached(source: str, query: str, fetch):
    key = make_key(source, query)
    hits = _cache.get(key)
    if hits is None:
        hits = fetch(query)
        _cache.set(key, hits)
    return hits


@with_retry("ddg")
def _ddg(kind: str, query: str, max_results: int = 5) -> list[dict]:
    rate_limiter.wait("ddg")
    try:
        with DDGS(timeout=int(SETTINGS.http_timeout)) as ddgs:
            if kind == "news":
                rows = ddgs.news(query, max_results=max_results, timelimit="y")
            else:
                rows = ddgs.text(query, max_results=max_results)
    except DDGSException as exc:
        if "no results" in str(exc).lower():  # nu e o eroare, doar tăcere
            return []
        raise  # RatelimitException/TimeoutException → retry (is_transient)
    source = "reddit(ddg)" if "site:reddit.com" in query else f"ddg_{kind}"
    return [_hit(source, r.get("title"), r.get("url") or r.get("href"), r.get("date"),
                 snippet=r.get("body")) for r in rows or []]


@with_retry("hn")
def _hn(query: str) -> list[dict]:
    rate_limiter.wait("hn")
    since = int(time.time()) - 2 * 365 * 86400
    r = requests.get("https://hn.algolia.com/api/v1/search", timeout=SETTINGS.http_timeout, headers=_headers(),
                     params={"query": query, "tags": "story", "hitsPerPage": 6,
                             "numericFilters": f"created_at_i>{since}"})
    r.raise_for_status()
    return [_hit("hackernews", h.get("title"), h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                 h.get("created_at"), h.get("points"), snippet=f"{h.get('num_comments', 0)} comentarii")
            for h in r.json().get("hits", [])]


class _GitHubQuotaExhausted(Exception):
    """Cota GitHub Search e epuizată. Nu e tranzitorie pentru `with_retry`: așteptarea o face `_github_search`."""


MAX_GITHUB_WAIT = 65  # cota Search (10 req/min fără token) se resetează la fiecare minut


@with_retry("github")
def _github_search(q: str) -> list[dict]:
    headers = _headers({"Accept": "application/vnd.github+json"})
    if SETTINGS.github_token:
        headers["Authorization"] = f"Bearer {SETTINGS.github_token}"
    for _ in range(2):
        rate_limiter.wait("github")
        r = requests.get("https://api.github.com/search/repositories", timeout=SETTINGS.http_timeout,
                         headers=headers, params={"q": q, "sort": "stars", "order": "desc", "per_page": 5})
        if r.status_code in (403, 429) and r.headers.get("X-RateLimit-Remaining") == "0":
            # backoff-ul scurt e inutil aici: așteptăm până la momentul de resetare anunțat de GitHub
            wait = int(r.headers.get("X-RateLimit-Reset", 0)) - time.time() + 1
            if wait > MAX_GITHUB_WAIT:
                raise _GitHubQuotaExhausted()
            ui.info(f"[github] cota de căutări e epuizată; aștept {max(wait, 0):.0f}s până la resetare")
            time.sleep(max(wait, 0))
            continue
        r.raise_for_status()
        return [_hit("github", i["full_name"], i["html_url"], i.get("created_at"), i.get("stargazers_count"),
                     snippet=i.get("description") or "", extra_tags=["open_source_clone"])
                for i in r.json().get("items", [])]
    raise _GitHubQuotaExhausted()


def _github(product: str) -> list[dict]:
    """Proiecte recente și populare pe categoria de produs; fără rezultate → orice vârstă, prag mai mare."""
    since = (dt.date.today() - dt.timedelta(days=3 * 365)).isoformat()
    hits = _github_search(f"{product} in:name,description,topics stars:>300 created:>{since}")
    return hits or _github_search(f"{product} in:name,description,topics stars:>1000")


def _reddit_json(query: str) -> list[dict]:
    """O singură încercare: un refuz al Reddit nu se reîncearcă și nu se ocolește."""
    global _reddit_blocked
    rate_limiter.wait("reddit")
    r = requests.get("https://www.reddit.com/search.json", timeout=SETTINGS.http_timeout, headers=_headers(),
                     params={"q": query, "sort": "relevance", "t": "year", "limit": 8})
    if r.status_code in (401, 403, 429):
        _reddit_blocked = True
        return []
    r.raise_for_status()
    posts = [c["data"] for c in r.json().get("data", {}).get("children", [])]
    return [_hit("reddit", p.get("title"), f"https://www.reddit.com{p.get('permalink', '')}",
                 dt.datetime.fromtimestamp(p.get("created_utc", 0), dt.UTC).isoformat(), p.get("score"),
                 snippet=f"r/{p.get('subreddit')}: {p.get('selftext', '')}") for p in posts]


# ── Tool-ul ──────────────────────────────────────────────────────────────────

def _run(label: str, source: str, query: str, fetch, status: dict, hits: list) -> None:
    ui.info(f"[{label}] 🔎 {query}")
    try:
        found = _cached(source, query, fetch)
    except _GitHubQuotaExhausted:
        status.setdefault(label, "rate_limited")
        return
    except Exception as exc:  # noqa: BLE001 — o sursă căzută nu oprește radarul
        if status.get(label) != "ok":
            status[label] = f"error: {type(exc).__name__}"
        ui.warn(f"[{label}] {type(exc).__name__}: {str(exc)[:120]}")
        return
    hits.extend(found)
    if found:
        status[label] = "ok"
    else:
        status.setdefault(label, "empty")


@safe_tool
def paranoid_osint_radar(company_name: str, ticker: str, industry: str, product: str = "",
                         extra_queries: list[str] | None = None) -> dict:
    """Caută activ motive pentru care compania ar putea muri sau deveni irelevantă în 3 ani."""
    if not company_name.strip():
        raise ToolError("`company_name` lipsește.", hint="Trimite numele companiei, ex. „Chegg Inc”.")
    name = short_name(company_name)
    queries = build_queries(name, product.strip())
    extra = [q.strip() for q in (extra_queries or []) if q and q.strip()][:MAX_EXTRA_QUERIES]
    status: dict[str, str] = {}
    hits: list[dict] = []

    for q in queries["ddg_news"]:
        _run("ddg_news", "ddg_news", q, lambda q: _ddg("news", q), status, hits)
    for q in queries["ddg_text"] + extra:
        _run("ddg_web", "ddg_text", q, lambda q: _ddg("text", q), status, hits)
    for q in queries["hn"] + extra:
        _run("hackernews", "hn", q, _hn, status, hits)
    for q in queries["github"]:
        _run("github", "github", q, _github, status, hits)
    if not queries["github"]:
        status["github"] = "skipped (fără `product`)"

    for q in queries["reddit"]:
        if not _reddit_blocked:
            _run("reddit", "reddit", q, _reddit_json, status, hits)
    if _reddit_blocked:
        status["reddit"] = "blocked → fallback ddg site:reddit.com"
        for q in queries["reddit_ddg"]:
            _run("reddit(ddg)", "ddg_text", q, lambda q: _ddg("text", q), status, hits)

    relevant = [h for h in hits if is_relevant(h, name, product)]
    if len(relevant) < len(hits):
        ui.info(f"Filtru de relevanță: am aruncat {len(hits) - len(relevant)} rezultate care nu menționează "
                f"{name}{' / ' + product if product else ''}")
    hits = relevant
    ranked = rank(hits)[:MAX_HITS]
    top = ranked[:TOP_THREATS]
    summary = threat_summary(ranked)
    if top:
        ui.show_table(f"🕵️  Radar OSINT — {name} ({ticker})", ["Sursă", "Semnal", "Riscuri", "Titlu"],
                      [[h["source"], h["signal"] or "", ", ".join(h["tags"]), h["title"][:60]] for h in top])
    ui.info(f"Surse: {status}")

    result = {"ticker": ticker.upper(), "company": name, "industry": industry,
              "sources_status": status, "threat_summary": summary, "top_threats": top}
    if not hits:
        result["coverage"] = "none"
        result["hint"] = ("Nicio sursă nu a întors rezultate; nu trage concluzii din tăcere — "
                          "menționează în raport că testul OSINT a fost incomplet.")
    return result


if __name__ == "__main__":
    print(paranoid_osint_radar("Chegg, Inc.", "CHGG", "Education & Training Services",
                               product="homework help"))
