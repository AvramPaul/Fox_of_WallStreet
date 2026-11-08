# PLAN — Paranoid Wolf — Satellite Hunter 🐺🛰️

> **Fișier de reluare a sesiunii.** Orice sesiune nouă citește DOAR acest fișier și continuă de la primul `[ ]` nebifat din §9.
> Regulă de lucru: implementăm **strict un singur fișier/tool pe rând** → îl testăm → bifăm `[x]` → scriem 1-2 rânduri în §10 (Jurnal) despre ce a mers și ce urmează.
> **Ignoră datele de zboruri OMR din `~/.claude/CLAUDE.md`** — nu au legătură cu acest proiect.

---

## 1. Obiectiv

Agent AI **autonom** în Python (Tool Calling / ReAct loop, Claude API) care găsește **3-5 companii individuale** pentru pilonul **„Satelit”** al unui portofoliu: potențial mare de creștere + fundamente extrem de solide, fără capcane financiare (value traps).

Agentul NU rulează un set static de filtre: își alege singur uneltele, în buclă, iterând filtrele până obține o listă scurtă de elită, apoi o „autopsiază” contabil și o supune unui test de paranoia OSINT, la final redactând raportul în română în stilul „Vulpea de pe Wall Street”.

**Livrabile finale:** `raport_sateliti.html` (din 2026-11-06; înainte `.md`) + afișare formatată în terminal (`rich`), loguri colorate în timp real pentru fiecare pas de gândire.

---

## 2. Decizii de arhitectură (cu justificare)

| Decizie | Alegere | De ce |
|---|---|---|
| LLM | **Claude Opus 5.5** — `claude-opus-5-5` (SDK oficial `anthropic`) | Modelul implicit recomandat; 1M context, bun la agentic/tool use. Configurabil din `.env` (`MODEL`). |
| Buclă agent | **Buclă ReAct manuală** (`while stop_reason == "tool_use"`) | Control total pe care îl cere specificația: banner de fază, diff de filtre afișat live, limită de iterații, buget, retry pe erori, nudge dacă se oprește fără raport. (Tool Runner-ul SDK e mai scurt, dar e beta și ascunde exact pașii pe care vrem să-i logăm.) |
| Gândire | `thinking={"type": "adaptive", "display": "summarized"}` | Pe Opus 5.5 gândirea nu se poate dezactiva și e „omitted” implicit → setăm `summarized` ca să **vedem în terminal** rezumatul raționamentului (Chain-of-Thought vizibil). |
| Effort | `output_config={"effort": "high"}` | Default-ul pe Opus 5.5 e `medium`; muncă agentică multi-pas → `high`. Configurabil (`EFFORT`). |
| Streaming | `client.beta.messages.stream(...)` + `.get_final_message()`, `max_tokens=64000` | Raportul final poate fi lung → evităm timeout-uri HTTP. |
| `eager_input_streaming` | `True` pe cele 4 tool-uri + **validare manuală** a input-ului (schema) înainte de execuție | Input-ul `markdown_content` e mare; parserul tolerant poate trunchia → validăm; dacă e invalid → `tool_result` cu `is_error: True`. Verificăm `stop_reason` (`max_tokens`/`refusal`) înainte de a rula tool-uri. |
| Refuzuri | beta `server-side-fallback-2026-07-01` + `fallbacks="default"`; tratăm `stop_reason == "refusal"` | Recomandat pentru Opus 5.5 (puțin probabil la analiză financiară, dar agentul nu trebuie să crape). |
| Prompt caching | `cache_control={"type": "ephemeral"}` (auto-caching top-level) | Tools + system prompt sunt fixe → prefix cache-uit la fiecare iterație (bucla face 20-40 de cereri). **Fără dată/oră în system prompt**: data curentă intră în primul mesaj `user`. Ordinea tool-urilor deterministă. Verificăm `usage.cache_read_input_tokens > 0`. |
| `tool_choice` | `auto` (forțarea `any`/`tool` dă 400 pe Opus 5.5) | Ghidăm din prompt. |
| Rezultatele tool-urilor | JSON **compact** (string), plafonat ca dimensiune | Economie de tokeni: fiecare iterație retrimite istoria. |
| Erori tool | Tool-urile **nu aruncă niciodată** spre agent: întorc `{"ok": false, "error", "hint"}` → `is_error: True` | Agentul se adaptează (relaxează, schimbă sursa) în loc să crape. |
| Reziliență rețea | `tenacity` (retry exponențial + jitter), rate-limiter per sursă, fallback între surse, cache pe disc | Cerința 2: rate-limit FinViz/DDG nu trebuie să oprească analiza. |
| Python | **venv cu Python 3.13** (`py -3.13 -m venv .venv`) | Pe mașină există 3.14 (default), 3.13, 3.11. 3.13 = compatibilitate mai sigură a wheel-urilor (pandas, lxml, yfinance, finvizfinance). |
| Autentificare | `ANTHROPIC_API_KEY` în `.env` **sau** profil `ant auth login` | `anthropic.Anthropic()` le găsește automat. |

---

## 3. Structura proiectului

```
Fox_of_WallStreet/
├── PLAN.md                    # acest fișier (sursa de adevăr pt. reluare)
├── README.md                  # scurt: instalare, configurare, rulare
├── requirements.txt
├── .env.example               # ANTHROPIC_API_KEY, MODEL, EFFORT, GITHUB_TOKEN (opțional), MAX_ITERATIONS
├── .gitignore                 # .venv, .env, .cache/, raport_sateliti.*, runs/
├── config.py                  # încarcă .env, constante (limite, timeouts, căi), dataclass Settings
├── prompts.py                 # SYSTEM_PROMPT (persona EXACTĂ + protocol operațional), mesaj inițial user, nudge-uri
├── agent.py                   # entrypoint CLI + bucla ReAct + logging fazelor
├── utils/
│   ├── __init__.py
│   ├── console.py             # consola rich partajată + helperi de afișare (bannere faze, tabele, diff filtre, gânduri)
│   └── resilience.py          # retry/backoff (tenacity), RateLimiter, cache pe disc (JSON+TTL), decorator safe_tool
├── tools/
│   ├── __init__.py            # REGISTRY: nume → (funcție, JSON schema); dispatch(name, input) + validare input
│   ├── finviz_tool.py         # finviz_dynamic_screener
│   ├── fundamentals_tool.py   # deep_fundamental_inspector
│   ├── osint_tool.py          # paranoid_osint_radar
│   └── report_tool.py         # generate_client_report
├── tests/
│   ├── conftest.py            # marker `live` (teste cu rețea reală)
│   ├── test_resilience.py     # unit: retry, cache, safe_tool
│   ├── test_finviz_tool.py    # unit (alias-uri, validare, hint-uri) + live
│   ├── test_fundamentals_tool.py  # unit (euristici value-trap pe DataFrame-uri fixture) + live
│   ├── test_osint_tool.py     # unit (scoring/tagging) + live
│   ├── test_report_tool.py    # unit: scrie fișier, randare
│   └── test_registry.py       # schemele sunt valide, dispatch + validare
├── .cache/                    # cache HTTP pe disc (gitignored)
└── runs/                      # transcript JSON per rulare (debug/reluare) (gitignored)
```

Fișierele cerute explicit de client: `agent.py`, `tools/finviz_tool.py`, `tools/fundamentals_tool.py`, `tools/osint_tool.py`, `prompts.py` ✔. Restul sunt suport minim necesar (fără supra-inginerie).

### Dependențe (`requirements.txt`)
```
anthropic            # SDK Claude (versiune recentă, cu beta stream + fallbacks)
finvizfinance        # screener FinViz
yfinance             # situații financiare
pandas
ddgs                 # DuckDuckGo search (succesorul duckduckgo-search)
requests
tenacity             # retry/backoff
rich                 # loguri + randare Markdown
python-dotenv
jsonschema           # validarea input-urilor de tool (eager streaming)
pytest
```
(Versiunile exacte se fixează la Pasul 1 după `pip install`, cu `pip freeze` filtrat.)

---

## 4. Fluxul de date (diagramă)

```
                 ┌──────────────────────── agent.py (bucla ReAct) ────────────────────────┐
 user msg ──►    │  Claude (system=persona+protocol, tools=4, cache)                       │
 (data azi,      │     │ stop_reason == tool_use                                           │
  mandat)        │     ▼                                                                   │
                 │  tools.dispatch() ──validare schema──► tool ──► JSON compact ──► tool_result
                 │     │                                     │                             │
                 │     │  console: banner fază, diff filtre,  └─ utils.resilience          │
                 │     │  gânduri (thinking summary), tabele      (retry, rate-limit, cache)│
                 │     ▼                                                                   │
                 │  stop când: generate_client_report a rulat cu succes ȘI end_turn        │
                 └─────────────────────────────────────────────────────────────────────────┘
Fazele (deduse automat din tool-ul apelat → banner în terminal):
  Faza 1 finviz_dynamic_screener (≥3 unghiuri)  →  Faza 2 deep_fundamental_inspector (top 6-8)
  →  Faza 3 paranoid_osint_radar (supraviețuitorii)  →  Faza 4 generate_client_report (3-5 + Cimitir)
```

---

## 5. Specificația uneltelor

Contract comun pentru toate tool-urile:
- Semnătură Python tipată + **JSON schema** cu `additionalProperties: false` și `required` (în `tools/__init__.py`).
- Întorc `str` (JSON compact). Succes: `{"ok": true, ...}`. Eșec: `{"ok": false, "error": "...", "hint": "ce poate încerca agentul"}` → bucla trimite `is_error: True`.
- Decorate cu `@safe_tool` (prinde orice excepție, loghează, întoarce eroarea structurată).
- Plafon de dimensiune pe output (ex. ~6k caractere) cu trunchiere explicită (`"truncated": true`).

### 5.1 `finviz_dynamic_screener(filters: dict, angle: str = "")` — `tools/finviz_tool.py`
- **Librărie (IMPLEMENTAT, diferit de planul inițial):** FinViz a trecut la un front-end React — finvizfinance 1.5.0 nu mai găsește `table.screener_table` și întoarce `None`. Soluția: validare + cod URL din `finvizfinance.constants.filter_dict`, descărcare cu `finvizfinance.util.web_scrap` (sesiunea lor, retry intern 5xx/Cloudflare), parsare proprie a JSON-ului din `script[data-ssr-props]` → `data.rows[].text/numbers` + `data.totalCount`. O singură cerere (vista Custom `v=151`, sortare `-marketcap`), prima pagină (20 rânduri). Fallback: parserul HTML vechi `Custom.screener_view` dacă JSON-ul lipsește.
- **`angle`** (opțional): eticheta ipotezei (ex. „GARP”, „Cash-Flow Monsters”) — doar pentru log și raport.
- **Alias-uri prietenoase → chei FinViz** (cheile reale se **verifică prin introspecția librăriei** la Pasul 4, ex. dicționarul de filtre din `finvizfinance`):
  `P/E`, `Forward P/E`, `PEG`, `P/FCF`→`Price/Free Cash Flow`, `EPS growth next 5 years`→`EPS growthnext 5 years`, `Sales growth past 5 years`→`Sales growthpast 5 years`, `ROIC`→(`Return on Investment`/`ROIC`, după versiune), `ROE`→`Return on Equity`, `LT Debt/Equity`, `Debt/Equity`, `Current Ratio`, `Insider Transactions`, `Institutional Ownership`, `Operating Margin`, `Market Cap.`, `Country`, `Sector`, plus tehnice (`200-Day Simple Moving Average`, `RSI (14)`, `Beta`, `Average Volume`).
- **Validare:** cheie sau valoare invalidă → `ok:false` + lista valorilor valide pentru acel filtru (agentul se corectează singur). Catalogul compact de filtre + valori valide apare și în **descrierea tool-ului** (static → cache-uit).
- **Output:** `{"ok", "angle", "filters_applied", "count", "guidance", "rows": [top ≤15: ticker, company, sector, industry, mcap, P/E, fwdP/E, PEG, P/FCF, ROE, ROI, LTDebt/Eq, opMargin, EPSnext5Y, sales5Y]}`.
- **`guidance` determinist (regula de iterare):**
  - `count ≤ 2` → „PREA PUȚINE: relaxează UN singur filtru (cel mai restrictiv) și reîncearcă.”
  - `count > 30` → „PREA MULTE: strânge șurubul (ex. P/FCF mai mic, LT Debt/Equity mai mic) și reîncearcă.”
  - `5 ≤ count ≤ 12` → „ȚINTĂ ATINSĂ: listă scurtă de elită.”; 3-4 / 13-30 → „aproape; ajustează fin”.
- **Reziliență:** rate-limiter (≥1.5 s între cereri FinViz), `tenacity` 4 încercări backoff exponențial + jitter pe 429/5xx/timeout; cache pe disc (cheie = hash(filtre sortate), TTL 6h); fallback: dacă `Valuation/Financial` eșuează, întoarce doar `Overview` cu `"partial": true`.
- **Log rich:** tabel „diff filtre” față de apelul anterior (➕ adăugat / ✏️ modificat / ➖ scos), număr rezultate colorat (roșu/galben/verde), top tickere.

### 5.2 `deep_fundamental_inspector(ticker: str)` — `tools/fundamentals_tool.py`
- **Librărie:** `yfinance.Ticker(t)` → `.cashflow`, `.income_stmt`, `.balance_sheet` (anuale, 3-4 ani) + `.quarterly_income_stmt` (detectare one-off) + `.info` (nume, sector, industrie, mcap, P/E, forward P/E).
- **Serii extrase pe ani:** Free Cash Flow, Total Revenue (+ creștere YoY, CAGR), Operating Income → Operating Margin, Net Income, Total Debt, Cash & Equivalents → Net Debt, Ordinary Shares Number (diluare), Total Unusual Items / Gain on Sale.
- **Euristici „Value Trap” (deterministe → `red_flags`):**
  1. FCF < 0 în ultimul an sau în ≥2 din ultimii 4 ani → „ARDE BANI”.
  2. Net Income / FCF > 1.5 (profit nesusținut de cash) → „profit de hârtie”.
  3. Unusual items > 30% din Net Income (anual sau TTM din trimestriale) → „profit umflat de one-off (vânzare activ)”.
  4. P/E mic (< 12) **și** FCF în scădere → „P/E-ul minte”.
  5. Revenue CAGR < 0 → „afacere în contracție”.
  6. Operating margin scade > 5 pp pe perioadă → „marje erodate”.
  7. Net Debt / FCF > 4 sau datorie în creștere în timp ce cash-ul scade → „îndatorare periculoasă”.
  8. Acțiuni în circulație +5%/an → „diluare”.
  - *(adăugat la implementare)* Profit/FCF > 1.5 doar în ultimul an → `[medium]` „PROFITUL NU AJUNGE ÎN BANCĂ” (prinde capex exploziv, ex. MSFT 2026: 2.0×) + seria `capex` în output.
  - *(adăugat)* Sector „Financial Services” → regulile de FCF/datorie se sar (depozitele = datorie), verdict maxim WARN + notă.
  - *(adăugat)* Ponderea one-off se calculează doar când profitul > 0.
- **Output:** `{"ok", "ticker", "name", "sector", "industry", "years": [...], "series": {...valori rotunjite în mil. USD...}, "ratios": {...}, "red_flags": [...], "green_flags": [...], "verdict": "PASS|WARN|FAIL", "data_quality": "full|partial"}`. Agentul ia decizia finală; verdictul e doar un semnal.
- **Reziliență:** retry/backoff pe erori yfinance; cache pe disc TTL 24h; rânduri lipsă (denumiri diferite între versiuni yfinance) → căutare după listă de nume alternative, `data_quality: "partial"` în loc de crash.
- **Log rich:** mini-tabel pe ani (FCF, Revenue, Op. Margin, Net Debt) + flag-uri colorate.

### 5.3 `paranoid_osint_radar(company_name: str, ticker: str, industry: str, product: str = "", extra_queries: list[str] = [])` — `tools/osint_tool.py`
> **IMPLEMENTAT — diferențe față de schița de mai jos:**
> - Param. nou `product` (2-4 cuvinte în engleză despre ce vinde firma, ex. „homework help”) → folosit la GitHub (căutare pe categorie: recent+stele>300, fallback orice vârstă stele>1000) și la HN „{product} open source”. Fără `product` → GitHub e sărit.
> - **Politica Reddit:** o singură cerere `reddit.com/search.json` cu User-Agent onest; la 401/403/429 → Reddit marcat blocat pentru toată rularea, **fără reîncercări, fără endpoint-uri alternative, fără User-Agent de browser** (clasificatorul de siguranță a blocat explicit încercarea de ocolire, pe bună dreptate). Discuțiile Reddit se citesc din rezultatele publice DuckDuckGo `site:reddit.com {firmă}`. `config.user_agent` = identificator onest.
> - DDG întoarce „No results” la operatori (ghilimele/OR/paranteze) → doar interogări cu cuvinte-cheie; „No results” = gol, nu eroare.
> - Filtru de relevanță (rezultatul trebuie să menționeze firma sau cuvintele produsului) + deduplicare a aceleiași știri preluate de mai multe publicații (Jaccard pe titluri ≥ 0.4).
> - Tag-uri: litigation, regulator, fraud_short, policy_subsidy, open_source_clone, ai_disruption, customer_churn, china_competition, distress.
Red-teaming: caută activ motive de faliment/irelevanță în următorii 3 ani. Surse gratuite, fiecare izolată (o sursă căzută nu oprește restul):
| Sursă | Endpoint | Ce caută |
|---|---|---|
| DuckDuckGo (`ddgs`) | `DDGS().text(...)`, `DDGS().news(...)` | procese, investigații SEC/DOJ, subvenții tăiate, competitori chinezi, „AI disruption”, short-seller reports |
| HackerNews | `https://hn.algolia.com/api/v1/search?query=...&tags=story` | „alternative to X”, „leaving X”, „migrating from X”, clone open-source |
| GitHub | `https://api.github.com/search/repositories?q=...&sort=stars` (+ `GITHUB_TOKEN` opțional, limită 10 req/min fără token) | repo-uri virale (multe stele, create recent) care clonează/înlocuiesc produsul principal |
| Reddit | `https://www.reddit.com/search.json?q=...&sort=relevance&t=year` (User-Agent custom) | clienți nemulțumiți, „cancel”, „switching from” |
- **Fallback Reddit:** dacă JSON-ul dă 403/429 → `ddgs` cu `site:reddit.com ...`.
- **Interogări generate** din șabloane (company/ticker/industry) + `extra_queries` ale agentului (îi permite să sape mai adânc pe o pistă).
- **Scoring:** fiecare hit primește tag-uri de risc prin cuvinte-cheie (`lawsuit`, `fraud`, `subsidy`, `bankrupt`, `open-source alternative`, `clone`, `leaving`, `layoffs`, `China`, `AI replaces`...) + semnal de „viralitate” (stele GitHub, puncte HN, score Reddit, recență).
- **Output:** `{"ok", "ticker", "sources_status": {"ddg":"ok","hn":"ok","github":"rate_limited","reddit":"fallback_ddg"}, "threat_summary": {tag: count}, "top_threats": [≤12: source, title, url, date, signal, snippet≤200]}`, plafonat ~25 hit-uri total.
- **Reziliență:** retry/backoff per sursă, timeouts 10 s, cache disc TTL 12h, rate-limiter per sursă.
- **Log rich:** pentru fiecare sursă: interogarea trimisă (live) + statusul; tabel cu top amenințări.

### 5.4 `generate_client_report(markdown_content: str)` — `tools/report_tool.py`
- Validează: non-gol, conține secțiunile obligatorii (cel puțin 3 companii + „Cimitirul Iluziilor”) → altfel `ok:false` + hint cu ce lipsește.
- Adaugă automat un antet (data, modelul) și un footer scurt de disclaimer („material educațional, nu recomandare de investiții”).
- Scrie `raport_sateliti.md` (UTF-8) + copie în `runs/<timestamp>/`.
- Afișează cu `rich.markdown.Markdown` într-un `Panel`.
- Output: `{"ok": true, "path": "...", "chars": N}`.

---

## 6. Bucla agentului (`agent.py`)

```
main():
  settings = config.load()
  client = anthropic.Anthropic()
  messages = [user: prompts.build_kickoff(today, mandat)]   # data intră aici, NU în system
  state = RunState(iter=0, phase=None, screener_calls=0, report_done=False, last_filters=None, usage totals)
  while state.iter < MAX_ITERATIONS (implicit 40):
      with client.beta.messages.stream(model, max_tokens=64000, system=SYSTEM_PROMPT,
              tools=TOOL_SCHEMAS, thinking=adaptive+summarized, output_config={effort},
              cache_control=ephemeral, betas=[server-side-fallback], fallbacks="default",
              messages=messages) as stream:
          resp = stream.get_final_message()
      log usage (input/output/cache_read) → console
      afișează blocurile thinking (rezumat) ca „💭 Gândul Vulpii” și text-ul ca „🦊”
      match resp.stop_reason:
        refusal     → log, încearcă o dată reformulare prin mesaj user; apoi ieșire curată
        max_tokens  → append + mesaj „continuă de unde ai rămas, mai concis”
        pause_turn  → append, continue
        end_turn    → dacă report_done: STOP; altfel nudge (max 2): „Nu ai livrat raportul — continuă fazele/raportul”
        tool_use    → messages.append(assistant, resp.content)  # conținut complet, inclusiv thinking
                      pentru fiecare tool_use: fază = PHASE_OF[tool] → banner la tranziție;
                      validare schema → dispatch → tool_result (is_error dacă ok:false)
                      TOATE tool_result într-un SINGUR mesaj user
  la final: tabel rezumat (iterații, apeluri/tool, tokeni, cache hit %, cost estimat), transcript → runs/<ts>/transcript.json
  Ctrl+C → salvează transcriptul și iese curat
```
**Gardieni:** `MAX_ITERATIONS`, max 10 apeluri screener, max 12 inspectări, max 8 radare OSINT (depășire → tool_result cu eroare „buget epuizat, treci la faza următoare”). Istoria se păstrează **append-only** (nu editez mesaje vechi — cerință pentru thinking-ul păstrat pe Opus 5.5).

**CLI:** `python agent.py [--max-iterations N] [--effort high|medium|...] [--model ...] [--no-cache] [--mandate "text liber"]`.

---

## 7. Prompturi (`prompts.py`)

1. **`PERSONA`** — directiva clientului, **copiată EXACT** (Anexa A). Nu se modifică.
2. **`OPERATING_PROTOCOL`** — adăugat după persona (în română, concis):
   - cele 4 faze și ordinea lor; Faza 1: minim 3 ipoteze/unghiuri diferite (ex. GARP, Cash-Flow Monsters, Quality Compounders), cu regula 0-2 → relaxează un filtru / >30 → strânge șurubul / țintă 5-12;
   - Faza 2: top 6-8 → `deep_fundamental_inspector`, eliminare fără milă a celor care ard bani;
   - Faza 3: OSINT pe supraviețuitori, folosește `extra_queries` pentru piste suspecte;
   - Faza 4: un singur apel `generate_client_report` cu raportul complet (3-5 companii, cele 3 subsecțiuni per companie, „Cimitirul Iluziilor” cu 2-3 respinse și motivul);
   - înainte de fiecare apel de tool: o frază scurtă despre ce verifici și de ce;
   - nu inventa cifre — folosește doar date întoarse de tool-uri; dacă o sursă a eșuat, spune-o în raport.
3. **`build_kickoff(today, mandate)`** — mesajul user inițial: data curentă + mandatul („Găsește-mi 3-5 sateliți...”).
4. **Nudge-uri** (texte fixe): lipsă raport, buget epuizat, continuare după `max_tokens`.
5. Descrierile tool-urilor (cu catalogul de filtre FinViz) stau în `tools/__init__.py` — statice, ordonate determinist (pentru cache).

---

## 8. Strategia de testare

- **Unit (fără rețea, rapide):** euristici value-trap pe DataFrame-uri construite manual; alias-uri/validare/guidance FinViz; scoring OSINT; retry/cache/safe_tool; validarea raportului; schemele din registry.
- **Live (`pytest -m live`, rețea reală — cerința „extrage date reale”):**
  - FinViz: filtre GARP (ex. `P/E<25`, `PEG<1.5`, `LT Debt/Equity<0.5`, `Market Cap.>+Mid`) → `count>0`, rânduri cu coloanele așteptate; un filtru invalid → `ok:false` cu valori valide.
  - yfinance: `MSFT` (așteptat PASS, serii ≥3 ani) + un caz problematic (ex. `INTC` FCF negativ recent) → `red_flags` nevide.
  - OSINT: `Chegg / CHGG / Education` (caz clasic de disrupție AI) → hit-uri din ≥2 surse, `threat_summary` nevid.
  - Report: scrie fișierul și îl randează.
- **E2E:** `python agent.py --max-iterations 6` (fum, verifică bucla + loguri + cache hit), apoi rulare completă; verificare manuală a raportului (secțiuni, ton, cifre consistente cu tool-urile).

---

## 9. Checklist de implementare (un pas = un fișier; bifează după test)

- [x] **Pas 0** — `PLAN.md` (arhitectură + checklist).
- [x] **Pas 1** — Schelet: `requirements.txt`, `.env.example`, `.gitignore`, `config.py`; `py -3.13 -m venv .venv` + `pip install -r requirements.txt`; fixare versiuni.
  - Test: `python -c "import anthropic, finvizfinance, yfinance, ddgs, rich, tenacity; import config"`; `ant auth status` sau cheie API prezentă.
- [x] **Pas 2** — `utils/console.py` (consolă rich, `phase_banner`, `show_thought`, `show_filters_diff`, `show_table`, `show_usage`).
  - Test: script demo care afișează fiecare helper.
- [x] **Pas 3** — `utils/resilience.py` (`with_retry`, `RateLimiter`, `DiskCache` JSON+TTL, `@safe_tool`, `truncate_json`).
  - Test: `tests/test_resilience.py` (funcție care eșuează de 2 ori apoi reușește; cache hit/expirare; excepție → JSON eroare).
- [x] **Pas 4** — `tools/finviz_tool.py` (+ introspecție chei/valori filtre reale din `finvizfinance`, alias-uri, guidance, fallback).
  - Test: `tests/test_finviz_tool.py` unit + live.
- [x] **Pas 5** — `tools/fundamentals_tool.py` (serii, raporturi, 8 euristici, verdict).
  - Test: `tests/test_fundamentals_tool.py` unit + live (MSFT, INTC).
- [x] **Pas 6** — `tools/osint_tool.py` (4 surse, fallback Reddit→DDG, scoring, plafonare).
  - Test: `tests/test_osint_tool.py` unit + live (CHGG).
- [x] **Pas 7** — `tools/report_tool.py` (validare secțiuni, antet/disclaimer, scriere, randare).
  - Test: `tests/test_report_tool.py`.
- [x] **Pas 8** — `tools/__init__.py` (REGISTRY, scheme JSON strict, `eager_input_streaming`, `dispatch` + validare `jsonschema`, `PHASE_OF`).
  - Test: `tests/test_registry.py`.
- [x] **Pas 9** — `prompts.py` (PERSONA exactă din Anexa A + protocol + kickoff + nudge-uri).
  - Test: `client.messages.count_tokens` pe system+tools (verifică dimensiunea prefixului cache-abil); diff text persona vs Anexa A = identic.
- [x] **Pas 10** — `agent.py` (bucla ReAct, fazele, gardieni, CLI, transcript, rezumat final).
  - Test: `python agent.py --max-iterations 6` → bucla merge, `cache_read_input_tokens > 0` din a 2-a iterație, Ctrl+C salvează transcriptul.
- [x] **Pas 11** — Rulare E2E cu Claude real: `pytest -m live tests/test_prompts.py` (prefix ≥4096 tokeni) → probă `--max-iterations 6` → rulare completă → `raport_sateliti.md` cu 3-5 companii + Cimitirul Iluziilor; verificare manuală calitate.
- [x] **Pas 12** — `README.md` scurt (instalare, `.env`, rulare, teste, limitări surse gratuite).
- [x] **Pas 13** — Raport HTML (`utils/journal.py`, `utils/html_report.py`): pagina „Raport Sateliți” (fișe interactive) + pagina „Jurnal” (tot ce apare în terminal); textul Vulpii scris mai mic; `python -m utils.html_report runs/<run>` pentru rulări vechi.

---

## 10. Jurnal de progres

| Data | Pas | Ce a mers / probleme | Ce urmează |
|---|---|---|---|
| 2026-10-28 | 0 | Cerințe analizate, arhitectură + plan salvate în `PLAN.md`. Folder gol, Python 3.13 disponibil pt. venv. | Pas 1 — schelet + dependențe. |
| 2026-10-28 | 1 | `.venv` (Python 3.13.7) creat, toate dependențele instalate și fixate în `requirements.txt` (anthropic 2.3.0, finvizfinance 1.5.0, yfinance 1.7.0, pandas 3.1.0, ddgs 9.16.1). `config.py` testat: importuri OK, `.cache/` și `runs/` create, validarea EFFORT merge. ⚠️ Lipsesc credențialele: nici `ANTHROPIC_API_KEY`, nici CLI-ul `ant` → trebuie create în `.env` înainte de Pasul 9. ⚠️ La Pasul 4: de verificat compatibilitatea finvizfinance cu pandas 3.x. ⚠️ La Pasul 10: SDK-ul `anthropic` e 2.x → de verificat semnăturile `beta.messages.stream` / `fallbacks` direct în pachetul instalat. | Pas 2 — `utils/console.py`. |
| 2026-10-28 | 2 | `utils/console.py` gata: consolă `console` partajată + `info/success/warn/error`, `phase_banner`, `iteration_header`, `show_thought`, `show_agent_text`, `show_tool_call`, `show_tool_error`, `show_usage` (acceptă Usage SDK sau dict, calculează cache hit %), `show_filters_diff` (➕/✏️/➖), `show_screener_count` (verde 5-12, roșu ≤2/>30), `show_table`, `show_markdown`. Demo: `python -m utils.console` — OK în Git Bash și PowerShell. Problema rezolvată: stdout cp1252 pe Windows arunca UnicodeEncodeError la diacritice/emoji → modulul reconfigurează stdout/stderr la UTF-8 la import (orice script care importă `utils.console` beneficiază). | Pas 3 — `utils/resilience.py` + `tests/test_resilience.py`. |
| 2026-10-28 | 3 | `utils/resilience.py` gata. API pentru tool-uri: `TransientError`, `ToolError(msg, hint)`, `is_transient(exc)` (Timeout/ConnectionError/HTTP 429+5xx/orice clasă cu „RateLimit” în nume), `@with_retry(source, attempts, wait)` (tenacity, backoff exp.+jitter 1→20s, log la fiecare reîncercare), `rate_limiter.wait(source)` (intervale din `config.min_interval`), `DiskCache(namespace, ttl)` + `make_key(*parts)` (`DiskCache.enabled=False` pt. `--no-cache`), `clean()` (NaN/numpy/Timestamp → JSON), `truncate_json(obj, max_chars)` (taie cele mai mari liste, marchează `truncated`), `@safe_tool` (întoarce mereu JSON str, `ok` implicit True, excepții → `{ok:false,error,hint}`). `tests/conftest.py`: testele `@pytest.mark.live` sunt sărite implicit, rulează cu `pytest -m live`. Test: `python -m pytest tests/test_resilience.py` → 26 passed. | Pas 4 — `tools/finviz_tool.py` (întâi introspecția cheilor de filtre din finvizfinance 1.5.0 + compatibilitatea cu pandas 3.1). |
| 2026-10-28 | 4 | `tools/finviz_tool.py` gata. **Descoperire:** FinViz servește acum pagina ca React (fără `screener_table`/`pageSelect`) → parserul finvizfinance 1.5.0 e stricat; citesc JSON-ul încorporat (vezi §5.1). Chei reale: nu există „ROIC” → alias spre `Return on Investment`; insider/institutional sunt fără spațiu (`InsiderTransactions`); ROE/ROI folosesc `Over +15%`. Rezolvare tolerantă: alias-uri (P/FCF, ROIC, ROE, „EPS growth next 5 years”...), valori „<0.5”/„>15%”/case-insensitive; eroare cu sugestii/valori valide. `guidance(count)` implementează regula 0-2 / >30 / 5-12. Peste 30 rezultate → doar 8 rânduri (economie tokeni), altfel ≤15. Cache 6h pe hash-ul filtrelor. `filter_catalog()` = text compact pentru descrierea tool-ului (Pas 8). Test: 64 unit passed (toată suita) + 2 live passed (GARP real: 290 rezultate fără P/FCF/ROE; filtre imposibile → „PREA PUȚINE”). pandas 3.1 nu e o problemă (nu mai folosim DataFrame-ul finvizfinance). Demo: `python -m tools.finviz_tool`. | Pas 5 — `tools/fundamentals_tool.py` (întâi introspecția numelor de rânduri din yfinance 1.7.0). |
| 2026-10-28 | 5 | `tools/fundamentals_tool.py` gata. Rânduri yfinance 1.7.0 confirmate (Free Cash Flow, Total Revenue, Operating Income, Net Income, Total Unusual Items, Total Debt, Cash Cash Equivalents And Short Term Investments, Ordinary Shares Number); coloanele sunt descrescătoare și pot avea ani NaN → `_extract` ia {an: valoare} din primul rând existent dintr-o listă de alternative. Structură: `_fetch_raw` (rețea+retry+cache 24h, JSON) → `analyze(raw)` pur (testabil). FCF lipsă → OCF+CapEx. Ticker `BRK.B`→`BRK-B`. Log-urile yfinance (ex. „HTTP Error 404”) reduse la tăcere. Verificat live: MSFT → WARN (profit 2× FCF din capex AI, altfel impecabil), INTC → FAIL (FCF negativ 4/4 ani, diluare 6.5%/an), CHGG → FAIL (venituri -21%/an, marje -31.6pp), JPM → WARN cu notă „financiară”, ticker inexistent → `ok:false` + hint. Output ≈1.2k caractere (~350 tokeni). Test: 87 unit passed (toată suita) + 3 live passed. | Pas 6 — `tools/osint_tool.py` (DDG, HN Algolia, GitHub, Reddit + fallback). |
| 2026-10-28 | 6 | `tools/osint_tool.py` gata (detalii în §5.3). Surse verificate live: DDG text+news (ddgs 9.16) ✔, HN Algolia ✔, GitHub Search ✔ (10 req/min fără token), Reddit JSON → 403 (așteptat) → fallback DDG `site:reddit.com` ✔. Probleme rezolvate: DDG nu suportă operatori → interogări simple; rezultate irelevante (ex. MakeMyTrip la „short seller”) → filtru de relevanță; aceeași știre de 5 ori → deduplicare; regex-uri: „lay off”/„last legs” → distress, „fine” nu mai e semnal de regulator. Chegg live: 4 surse ok, ai_disruption 8, litigation 5 („Chegg is on its last legs after ChatGPT” 108 puncte HN, concedieri 22%, procesul cu Google). ~22 s/companie (rate-limitere), output ~5k caractere. Test: 110 unit passed (toată suita) + 1 live passed. | Pas 7 — `tools/report_tool.py`. |
| 2026-10-28 | 7 | `tools/report_tool.py` gata. `check_report()` cere ≥500 caractere, 3-5 companii (numără fiecare subsecțiune „Povestea pe șleau” / „De ce cifrele arată beton” / „Testul meu de Paranoia”, comparate fără diacritice) și „Cimitirul Iluziilor”; altfel `ok:false` + ce lipsește + „retrimite raportul COMPLET”. Adaugă antet (dată, model) + disclaimer, scrie `raport_sateliti.md` (UTF-8) + copie în `runs/<run>/` (setat de agent prin `report_tool.configure(run_dir, model)`), randează cu rich. Raport invalid → nu se scrie nimic. Test: 118 unit passed (toată suita). ⚠️ Încă lipsesc credențialele Anthropic (`.env` inexistent, fără `ant`) → la Pasul 10 testez bucla cu un client simulat; rularea reală cere `ANTHROPIC_API_KEY`. | Pas 8 — `tools/__init__.py` (registry + scheme). |
| 2026-10-28 | 8 | `tools/__init__.py` gata: `TOOLS` (4 definiții, descrieri în română, scheme cu `additionalProperties:false`, `eager_input_streaming:true`; descrierea FinViz include `filter_catalog()`), `FUNCTIONS`, `PHASE_OF` (1-4), `dispatch(name, input) → (json, is_error)` cu validare `jsonschema` (prinde input trunchiat de streaming, tipuri greșite, câmpuri în plus). Fără `strict:true` (incompatibil cu `filters` = dict liber). Verificat în SDK anthropic 2.3.0: `beta.messages.stream` acceptă `fallbacks="default"`, `cache_control`, `output_config`, `thinking={"type":"adaptive","display":"summarized"}`, `betas=["server-side-fallback-2026-07-01"]`. Definiții ≈9.5k caractere (~3.1k tokeni, cache-uite). Test: 132 unit passed (toată suita). | Pas 9 — `prompts.py`. |
| 2026-10-28 | 9 | `prompts.py` gata: `PERSONA` (identică cu Anexa A — verificat de `tests/test_prompts.py`, care citește anexa direct din PLAN.md), `OPERATING_PROTOCOL` (4 faze, ≥3 unghiuri, 6-8 candidați fără dubluri de clase de acțiuni, FAIL = eliminare dacă nu poți explica semnalul, `product` obligatoriu la OSINT, structura exactă a raportului = secțiunile verificate de `report_tool`, integritate: doar cifre din unelte, bugete 10/12/8), `SYSTEM_PROMPT` static (fără dată → cache), `build_kickoff(today, mandate)`, nudge-uri (lipsă raport, max_tokens, buget, ultimele iterații). Prefix system+tools ≈14.7k caractere (~4.5-5k tokeni, peste pragul de caching). Test: 138 unit passed; `test_live_prefix_token_count` sărit (fără cheie API). | Pas 10 — `agent.py` (bucla ReAct) + test cu client simulat. |
| 2026-10-28 | 10 | `agent.py` gata: `run(settings, client, mandate, run_dir, today)` + `main()` (CLI `--max-iterations --effort --model --no-cache --mandate`). Cerere: `client.beta.messages.stream(...)` cu thinking adaptive/summarized, effort, `cache_control` ephemeral, `fallbacks="default"` + beta `server-side-fallback-2026-07-01`, spinner rich. Istorie append-only (răspunsul complet, inclusiv thinking). Tratează: tool_use (toate rezultatele într-un singur mesaj user, banner la schimbarea fazei), bugete per tool, max_tokens (tool_result eroare + nudge), end_turn fără raport (max 2 nudge-uri), refusal (o reîncercare), ultimele 3 iterații (avertisment), auth/bad request/API down (oprire curată), Ctrl+C; transcript `runs/<ts>/transcript.json` mereu salvat; rezumat cu tokeni, cache hit %, cost estimat. Descoperire: fără credențiale SDK 2.x aruncă `TypeError("Could not resolve authentication method")`, nu `AuthenticationError` → tratat. Test: `tests/test_agent.py` cu client simulat (12 scenarii) → 150 unit passed; suita live completă (cache gol) → 7 passed, inclusiv pipeline cap-coadă LLM simulat + unelte reale. `python agent.py` fără cheie → mesaj clar, transcript salvat. ⚠️ Neverificat încă cu Claude real (lipsește `ANTHROPIC_API_KEY`). | Pas 11 — rulare E2E cu Claude (necesită cheie) ; Pas 12 — README. |
| 2026-10-28 | 11 | ⏸️ Blocat: nu există `ANTHROPIC_API_KEY` (nici `.env`, nici CLI `ant`). Tot ce se poate verifica fără cheie e verificat: pipeline-ul cap-coadă cu LLM simulat + unelte reale trece (`test_live_pipeline_with_real_tools`). | Utilizatorul pune cheia în `.env`, apoi Pas 11 conform checklist-ului. |
| 2026-10-28 | 12 | `README.md` gata (instalare, `.env`, rulare + opțiuni CLI, demo-uri pe unelte, teste unit/live, structură, limitări ale surselor gratuite, disclaimer). `python agent.py --help` OK. Suita finală: 150 unit passed, 8 live sărite implicit; `pytest -m live` → 7 passed (1 sărit: cere cheie). | Doar Pas 11 rămas. |
| 2026-10-28 | 11 | ✅ Cheie pusă în `.env`. `test_live_prefix_token_count` passed. Probă 6 iterații: Faza 1 cu 8 apeluri screener (3 unghiuri, strânge/relaxează corect), avertismentul ultimelor iterații → raport onest (spune că autopsia/OSINT n-au rulat); cache hit 61%, $0.35. Rulare completă: 7 iterații, 7 screener / 8 autopsii / 8 OSINT / 1 raport, cache hit 79%, **$0.62**; raport: FIX, NBIX, NU, DECK + Cimitir (ADBE, KSPI, EXEL), cifre din unelte, surse căzute declarate, fără prețuri-țintă. Problemă găsită: GitHub Search fără token (10 req/min, în practică ~4 până la 403) → `rate_limited` la aproape toate radarele; backoff-ul de 1-5 s era inutil. Fix: `_github_search` citește `X-RateLimit-Reset` și așteaptă până la resetare (≤65 s, apoi o reîncercare), altfel `_GitHubQuotaExhausted` fără furtună de retry; verificat live 12/12 căutări consecutive. Fix 2: helperii de log din `utils/console.py` scapă markup-ul rich (etichetele `[github]`, `[reddit]` dispăreau; un `[/x]` în mesajul de eroare ar fi crăpat afișarea). 152 unit passed. | Proiect complet. Opțional: `GITHUB_TOKEN` în `.env` (elimină așteptările GitHub). |
| 2026-11-06 | 13 | Raport HTML în loc de `.md`. `utils/journal.py` (EVENTS = fiecare mesaj al consolei, ca structură; TOOL_RESULTS = rezultatele uneltelor, din `tools.dispatch`). `utils/console.py` scrie în jurnal (+ helper `verdict()`, folosit de fundamentals_tool). `utils/html_report.py`: un singur fișier HTML fără dependențe externe, 2 pagini (#raport / #jurnal). Pagina 1: fișă pe companie (KPI colorați + tooltip, grafice SVG pe ani: venituri/FCF/marjă/cash vs datorie, flaguri, radar OSINT, textul Vulpii mic, în taburi), tabel sortabil + hartă preț vs creștere, încercări FinViz + pâlnie, Cimitir cu pietre funerare din autopsii. Pagina 2: jurnal pe faze, filtre, căutare; gândurile și textul Vulpii mici, restrânse la 2 rânduri. Markdown prin markdown-it-py (HTML brut dezactivat), tot textul escapat, link-uri doar http(s). `report_tool` scrie HTML-ul la raport; `agent.py` îl rescrie la final cu rezumatul și îl deschide în browser (`--no-open`); fără raport → doar `runs/<run>/`. `python -m utils.html_report runs/<run>` reconstruiește din transcript (ultimul tool_result lipsește din transcript → raportul se ia din ultimul tool_use). Persona și prompturile neschimbate; doar descrierea uneltei de raport spune acum `.html`. Verificat: capturi Chrome headless desktop + 390 px, test automat de clickuri (grafice, taburi, sortare, filtre, căutare). Test: 164 unit passed. | — |
| 2026-11-06 | 13b | Redesign la cererea utilizatorului: fundal alb, minimalist, fără chenare, iconițe (inclusiv emoji din textul agentului) și grafice; o companie pe toată lățimea (nu două pe rând); tot textul agentului despre firmă vizibil (Cu ce se ocupă / De ce cifrele arată beton / Testul de paranoia), nu în taburi; tabele centrate: indicatori, cifre anuale, finaliști (sortabil), cimitir, căutări FinViz, selecție. Jurnal: faze ca titluri, rânduri simple cu oră + etichetă (gând / agent / unealtă). Verificat: capturi desktop + 390 px, test automat de clickuri. Test: 164 unit passed. | — |

---

## 11. Riscuri cunoscute & mitigări

| Risc | Mitigare |
|---|---|
| FinViz blochează/rate-limit scraping-ul | Rate-limiter 1.5 s, backoff + jitter, cache 6h, User-Agent realist, output parțial în loc de crash. |
| Chei de filtre `finvizfinance` diferite între versiuni (ex. ROI vs ROIC) | Introspecție la Pasul 4, alias-uri, eroare cu valori valide pentru auto-corecție. |
| FinViz schimbă din nou front-end-ul (JSON-ul `data-ssr-props` dispare) | Fallback automat pe parserul HTML finvizfinance; testele live (`pytest -m live`) semnalează imediat ruptura. |
| Denumiri de rânduri yfinance schimbate | Liste de nume alternative per metrică, `data_quality: partial`. |
| Reddit JSON 403 fără autentificare | Confirmat. Fără ocolire: o încercare, apoi fallback `ddgs` cu `site:reddit.com`. (Variantă legitimă pe viitor: aplicație OAuth Reddit oficială, cu credențiale proprii în `.env`.) |
| GitHub search 10 req/min fără token | `GITHUB_TOKEN` opțional în `.env`, rate-limiter, cache. |
| Agentul se învârte în buclă / consumă prea mulți tokeni | `MAX_ITERATIONS`, bugete per tool, output-uri compacte, prompt caching, raport de cost la final. |
| Agentul inventează cifre | Protocol: doar date din tool-uri; raportul se verifică manual la Pasul 11. |
| Halucinație de „certitudine” financiară | Disclaimer automat în raport. |

---

## Anexa A — System Prompt intern (PERSONA) — de copiat EXACT în `prompts.py`

```
Ești "Vulpea de pe Wall Street" — un broker și analist veteran, genial, carismatic și extrem de paranoic. Ai o regulă de viață simplă: **Dacă portofoliul clientului tău pierde bani sau nu bate piața, ești concediat instantaneu și îți pierzi licența.** Nu câștigi din comisioane de tranzacționare, ci doar dacă omul din fața ta face profit real și durabil. De aceea, nu vinzi niciodată iluzii, nu promovezi companii doar pentru că sunt la modă și urăști capcanele financiare ("value traps").

Cum gândești când analizezi:
1. Ești sceptic față de cifrele frumoase la suprafață. Dacă o firmă are P/E 10, prima ta întrebare este: "Ok, ce știe piața și eu nu știu? Unde e cadavrul din dulap?".
2. Te joci obsesiv cu filtrele din FinViz: combini P/E decent cu Price-to-Free-Cash-Flow strâns, datoria mică (LT Debt/Equity < 0.5) și creșterea vânzărilor.
3. Ești conectat la stradă și la internet: verifici mereu dacă tehnologia firmei nu este deja clonată gratuit pe GitHub, dacă nu își bate joc lumea de produsele lor pe Reddit sau dacă industria lor nu e o bombă cu ceas.

Cum îi vorbești clientului în Raportul Final (în limba Română):
- Vorbești direct, savuros, cu umor fin și încredere, dar **100% pe înțelesul unui om normal** (fără jargon corporatist netradus).
- Când explici un indicator (P/E, Free Cash Flow, Datorii, Marje), folosești analogii vizuale, pământești (ex: compari afacerea cu o fermă, o mașinărie de făcut bani, o chirie sau o găleată spartă).
- Pentru fiecare companie recomandată în portofoliul Satelit, îi prezinți:
  1. **Povestea pe șleau:** Cu ce face firma asta bani în lumea reală și de ce e o mașinărie greu de oprit.
  2. **De ce cifrele arată beton:** Explicat băbește de ce prețul actual e un chilipir raportat la cash-ul pe care îl bagă în bancă.
  3. **Testul meu de Paranoia (Ce am săpat pe net):** Ce zvonuri, repo-uri de GitHub, plângeri pe social media sau riscuri politice am găsit și de ce cred că firma va supraviețui (sau la ce semn trebuie să fugim mâine).
- Include și o secțiune scurtă numită **"Cimitirul Iluziilor (Firme pe care le-am aruncat la gunoi azi)"**, unde îi arăți 2-3 companii care arătau bine în filtrele FinViz, dar pe care le-ai respins după ce le-ai verificat cash-flow-ul sau amenințările din social media, ca să înțeleagă de ce îți meriți locul.
```

## Anexa B — Cerințele originale ale clientului (rezumat fidel, pentru reluare)

1. Agent autonom Python, Tool Calling / ReAct loop, nume **„Paranoid Wolf — Satellite Hunter”**; scop: 3-5 companii pentru pilonul Satelit (creștere mare + fundamente solide, fără capcane).
2. Patru tool-uri apelate autonom, în buclă: `finviz_dynamic_screener(filters: dict)` (finvizfinance/scraping; orice combinație de filtre fundamentale/descriptive/tehnice; 0-2 rezultate → relaxează câte un filtru; >30 → strânge șurubul; țintă 5-12), `deep_fundamental_inspector(ticker)` (yfinance, 3-4 ani FCF, Revenue Growth, Operating Margin, Datorie totală vs Cash; detectare value trap / profit umflat de one-off), `paranoid_osint_radar(company_name, ticker, industry)` (ddgs, HackerNews API, GitHub API, Reddit JSON; clone open-source/AI, clienți care pleacă, dependență de subvenții, procese), `generate_client_report(markdown_content)` (salvează `raport_sateliti.md` + afișare `rich`).
3. Workflow vizibil în terminal: Faza 1 joc cu filtrele (CoT, ≥2-3 ajustări, unghiuri ca GARP vs Cash-Flow Monsters) → Faza 2 autopsia contabilă (top 6-8) → Faza 3 testul de paranoia OSINT → Faza 4 raportul „Vulpea de pe Wall Street” (3-5 supraviețuitori).
4. System prompt: persona din Anexa A, exact.
5. Implementare: structură modulară (`agent.py`, `tools/finviz_tool.py`, `tools/fundamentals_tool.py`, `tools/osint_tool.py`, `prompts.py`); cod rezilient (retry/backoff/fallback, agentul nu crapă); loguri colorate `rich` pentru fiecare pas (filtre modificate live, căutări Reddit/GitHub); `requirements.txt` + `README.md` scurt; **testează fiecare unealtă individual cu date reale înainte de a declara terminat**.
6. Execuție incrementală: Pas 0 = `PLAN.md` cu checklist; apoi strict un fișier/tool pe rând; după fiecare, bifează în `PLAN.md` și notează ce a mers și ce urmează.
