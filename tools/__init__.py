"""Registry-ul uneltelor: definiții pentru Claude (scheme JSON), validare și dispatch.

Ordinea și textul definițiilor sunt fixe (deterministe) → prefixul tools+system rămâne în cache.
"""

import json
from collections.abc import Callable

from jsonschema import Draft202012Validator

from tools.finviz_tool import filter_catalog, finviz_dynamic_screener
from tools.fundamentals_tool import deep_fundamental_inspector
from tools.osint_tool import paranoid_osint_radar
from tools.report_tool import generate_client_report

FINVIZ_DESCRIPTION = f"""Rulează screener-ul FinViz (acțiuni listate în SUA) cu o combinație de filtre aleasă de tine și întoarce numărul TOTAL de companii care trec + top companii după capitalizare (P/E, Forward P/E, PEG, P/FCF, ROE, ROI, marje, LT Debt/Equity, Current Ratio, creștere EPS 5 ani, creștere vânzări 5 ani, tranzacții insideri, deținere instituțională).

Cum se folosește:
- `filters` = {{nume_filtru: valoare}}. Valorile sunt etichetele FinViz exacte (ex. "Under 20", "Over +15%", "+Mid (over $2bln)"). Se acceptă și prescurtări: "P/FCF", "ROIC"/"ROI" (→ Return on Investment), "ROE", "<0.5", ">15%".
- `angle` = numele ipotezei testate (ex. "GARP", "Cash-Flow Monsters") — apare în loguri și te ajută să compari unghiurile.
- Câmpul `guidance` din rezultat aplică regula de iterare: 0-2 companii → relaxează UN singur filtru; peste 30 → strânge șurubul; ținta este 5-12. Peste 30 de rezultate primești doar un eșantion de rânduri.
- O valoare invalidă întoarce eroare cu lista valorilor valide — corectează și reîncearcă.

Filtre uzuale și valorile lor valide:
{filter_catalog()}"""

TOOLS: list[dict] = [
    {
        "name": "finviz_dynamic_screener",
        "description": FINVIZ_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "filters": {"type": "object", "additionalProperties": {"type": "string"},
                            "description": "Filtrele FinViz: {nume: valoare}."},
                "angle": {"type": "string", "description": "Ipoteza/unghiul testat, ex. 'GARP'."},
            },
            "required": ["filters"],
            "additionalProperties": False,
        },
    },
    {
        "name": "deep_fundamental_inspector",
        "description": (
            "Autopsia contabilă a unei companii din situațiile financiare anuale (Yahoo Finance, ultimii 3-4 ani): "
            "venituri și creștere, marjă operațională, profit net, Free Cash Flow și marjă FCF, capex, datorie "
            "totală vs. cash, elemente neobișnuite. Aplică automat teste de value trap (FCF negativ, profit "
            "nesusținut de cash, profit umflat de one-off-uri, P/E mic cu FCF în scădere, venituri în contracție, "
            "marje erodate, îndatorare, diluare) și întoarce red_flags / green_flags și un verdict PASS/WARN/FAIL. "
            "Verdictul e un semnal, nu o sentință: decizia e a ta. Pentru bănci/asigurători testele de FCF și "
            "datorie nu se aplică (vezi `notes`). Sumele sunt în milioane, în moneda raportării (`currency`)."),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Tickerul, ex. 'MSFT' sau 'BRK.B'."}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "name": "paranoid_osint_radar",
        "description": (
            "Red-teaming OSINT împotriva unei companii: caută activ motive pentru care ar putea da faliment sau "
            "deveni irelevantă în 3 ani. Surse: știri DuckDuckGo (procese, investigații, subvenții/tarife, "
            "competiție, disrupție AI), web (rapoarte short-seller, alternative open-source), HackerNews, GitHub "
            "(proiecte open-source populare care clonează produsul) și Reddit (plângeri, clienți care pleacă). "
            "Fiecare rezultat are tag-uri de risc; `threat_summary` le numără. `sources_status` arată ce surse au "
            "răspuns — o sursă căzută nu înseamnă „nicio amenințare”. Durează ~20-30 s."),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string", "description": "Numele companiei, ex. 'Chegg, Inc.'."},
                "ticker": {"type": "string"},
                "industry": {"type": "string", "description": "Industria (ex. din FinViz)."},
                "product": {"type": "string",
                            "description": "Produsul/serviciul principal în 2-4 cuvinte în ENGLEZĂ, ex. "
                                           "'homework help', 'CRM software', 'GPU'. Necesar pentru căutarea "
                                           "de clone pe GitHub."},
                "extra_queries": {"type": "array", "items": {"type": "string"}, "maxItems": 3,
                                  "description": "Până la 3 căutări suplimentare (engleză, cuvinte-cheie, fără "
                                                 "operatori) pentru o pistă suspectă."},
            },
            "required": ["company_name", "ticker", "industry"],
            "additionalProperties": False,
        },
    },
    {
        "name": "generate_client_report",
        "description": (
            "Salvează raportul final „Vulpea de pe Wall Street” în raport_sateliti.md și îl afișează în terminal. "
            "Trimite raportul COMPLET în Markdown, în română, într-un singur apel: 3-5 companii, fiecare cu "
            "subsecțiunile „Povestea pe șleau”, „De ce cifrele arată beton” și „Testul meu de Paranoia (Ce am "
            "săpat pe net)”, plus secțiunea „Cimitirul Iluziilor (Firme pe care le-am aruncat la gunoi azi)” cu "
            "2-3 companii respinse și motivul. Un raport incomplet e respins cu lista a ce lipsește."),
        "input_schema": {
            "type": "object",
            "properties": {"markdown_content": {"type": "string", "description": "Raportul complet în Markdown."}},
            "required": ["markdown_content"],
            "additionalProperties": False,
        },
    },
]

# Input-urile mari (raportul) sosesc în flux; validăm noi schema înainte de execuție.
for _tool in TOOLS:
    _tool["eager_input_streaming"] = True

FUNCTIONS: dict[str, Callable[..., str]] = {
    "finviz_dynamic_screener": finviz_dynamic_screener,
    "deep_fundamental_inspector": deep_fundamental_inspector,
    "paranoid_osint_radar": paranoid_osint_radar,
    "generate_client_report": generate_client_report,
}

PHASE_OF = {
    "finviz_dynamic_screener": 1,
    "deep_fundamental_inspector": 2,
    "paranoid_osint_radar": 3,
    "generate_client_report": 4,
}

_VALIDATORS = {t["name"]: Draft202012Validator(t["input_schema"]) for t in TOOLS}


def _error(message: str, hint: str) -> str:
    return json.dumps({"ok": False, "error": message, "hint": hint}, ensure_ascii=False)


def dispatch(name: str, tool_input) -> tuple[str, bool]:
    """Execută tool-ul `name` → (conținut JSON pentru tool_result, is_error)."""
    if name not in FUNCTIONS:
        return _error(f"Unealtă necunoscută: {name!r}.", f"Unelte disponibile: {list(FUNCTIONS)}"), True
    if not isinstance(tool_input, dict):
        return _error("Input-ul trebuie să fie un obiect JSON.", "Retrimite apelul cu parametrii corecți."), True
    errors = sorted(_VALIDATORS[name].iter_errors(tool_input), key=lambda e: list(e.path))
    if errors:
        details = "; ".join(f"{'/'.join(map(str, e.path)) or '(rădăcină)'}: {e.message}" for e in errors[:5])
        return _error(f"Parametri invalizi pentru {name}: {details}",
                      "Corectează parametrii conform schemei și reîncearcă (input posibil trunchiat)."), True
    content = FUNCTIONS[name](**tool_input)
    try:
        ok = json.loads(content).get("ok", True)
    except (ValueError, AttributeError):
        ok = True
    return content, not ok
