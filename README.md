# 🐺 Paranoid Wolf — Satellite Hunter

Agent AI autonom (Claude + tool calling, buclă ReAct) care caută **3-5 companii pentru pilonul „Satelit”** al unui portofoliu: creștere puternică, fundamente solide, fără capcane financiare. Raportul final e scris în română de „Vulpea de pe Wall Street”.

Agentul lucrează singur, în 4 faze vizibile în terminal:

1. **Jocul cu filtrele FinViz.** Testează mai multe unghiuri (GARP, Cash-Flow Monsters…) și strânge sau relaxează filtrele până ajunge la 5-12 companii.
2. **Autopsia contabilă.** Verifică 3-4 ani de FCF, venituri, marje și datorii (Yahoo Finance) și elimină value trap-urile.
3. **Testul de Paranoia (OSINT).** Caută știri, discuții HackerNews, clone open-source pe GitHub și plângeri pe Reddit.
4. **Raportul.** Scrie `raport_sateliti.md` cu 3-5 companii și „Cimitirul Iluziilor”.

## Instalare

### Windows: un singur pas

Dublu-click pe **`run.bat`** sau, din terminal, `run.bat`. La prima rulare scriptul:

1. găsește Python 3.11+ (de preferat 3.13) și creează `.venv`;
2. instalează pachetele din `requirements.txt`;
3. îți cere cheia Anthropic (nu se afișează pe ecran) și o salvează în `.env`.

La rulările următoare pornește direct agentul. Pachetele se reinstalează doar dacă s-a schimbat `requirements.txt`. Argumentele merg mai departe la agent: `run.bat --max-iterations 6`. Ca să schimbi cheia, editezi `.env`.

### Manual (orice sistem)

```bash
py -3.13 -m venv .venv
.venv\Scripts\activate          # Windows (Git Bash: source .venv/Scripts/activate)
pip install -r requirements.txt
copy .env.example .env          # apoi completează ANTHROPIC_API_KEY
```

| Variabilă `.env` | Implicit | Rol |
|---|---|---|
| `ANTHROPIC_API_KEY` | — (obligatorie) | cheia Claude API |
| `MODEL` | `claude-opus-5-5` | modelul |
| `EFFORT` | `high` | cât de mult gândește modelul (`low` … `max`) |
| `MAX_ITERATIONS` | `40` | limita buclei |
| `GITHUB_TOKEN` | — (opțional) | ridică limita GitHub Search de la 10 la 30 req/min |

## Rulare

```bash
python agent.py                                  # rulare completă
python agent.py --max-iterations 6               # rulare scurtă, de probă
python agent.py --mandate "Doar companii europene, fără energie"
python agent.py --effort medium --no-cache
```

Ies la final:
- **`raport_sateliti.md`**, cu o copie în `runs/<data-ora>/`;
- **`runs/<data-ora>/transcript.json`**: conversația completă, pentru depanare;
- **un rezumat în terminal**: iterații, apeluri pe unealtă, tokeni, procentul de cache, costul estimat.

Fiecare unealtă se poate rula și singură, pentru demo:

```bash
python -m tools.finviz_tool
python -m tools.fundamentals_tool MSFT INTC
python -m tools.osint_tool
```

## Teste

```bash
python -m pytest              # teste unitare (fără rețea, ~4 s)
python -m pytest -m live      # date reale: FinViz, Yahoo, DDG, HN, GitHub (~1 min)
```

## Structură

```
agent.py              bucla ReAct, faze, bugete, transcript, rezumat
prompts.py            persona „Vulpea” (text exact) + protocolul de lucru
config.py             setări din .env
tools/                cele 4 unelte + registry (scheme, validare, dispatch)
utils/console.py      loguri colorate (rich)
utils/resilience.py   retry/backoff, rate-limit, cache pe disc, @safe_tool
PLAN.md               arhitectura și jurnalul de progres
```

## Limitări (surse gratuite)

- **FinViz** a trecut la un front-end React, iar parserul din `finvizfinance` 1.5.0 nu mai funcționează. Unealta citește JSON-ul încorporat în pagină. Dacă FinViz schimbă din nou pagina, `pytest -m live` semnalează imediat.
- **Reddit** refuză cererile neautentificate (403). Agentul nu ocolește blocajul: citește discuțiile prin rezultatele publice DuckDuckGo `site:reddit.com`.
- **DuckDuckGo și GitHub** limitează numărul de cereri. Uneltele așteaptă între cereri, reîncearcă și pun rezultatele în cache (`.cache/`). O sursă căzută apare în raport.
- **Datele yfinance** pot lipsi pentru unele companii; analiza le marchează ca `data_quality: partial`.

> ⚠️ Material educațional. Nu este o recomandare de investiții.
