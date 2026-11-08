"""generate_client_report — validează raportul final, îl salvează ca pagină HTML (`raport_sateliti.html`) și îl afișează."""

import datetime as dt
import re
import unicodedata
from pathlib import Path

import config
from utils import console as ui
from utils import html_report, journal
from utils.resilience import ToolError, safe_tool

SETTINGS = config.load()
MIN_COMPANIES, MAX_COMPANIES = 3, 5
SECTIONS = {  # secțiunile cerute pentru fiecare companie (comparate fără diacritice)
    "povestea pe sleau": "Povestea pe șleau",
    "de ce cifrele arata beton": "De ce cifrele arată beton",
    "testul meu de paranoia": "Testul meu de Paranoia",
}
GRAVEYARD = ("cimitirul iluziilor", "Cimitirul Iluziilor (Firme pe care le-am aruncat la gunoi azi)")
DISCLAIMER = ("Material educațional generat automat de un agent AI pe baza unor surse publice gratuite "
              "(FinViz, Yahoo Finance, DuckDuckGo, HackerNews, GitHub, Reddit). Nu este o recomandare de "
              "investiții. Verifică datele și decide pe propria răspundere.")

_run_dir: Path | None = None
_model: str = SETTINGS.model
_report_md: str | None = None
_started: dt.datetime = dt.datetime.now()


def configure(run_dir: Path | None = None, model: str | None = None) -> None:
    """Apelat de agent.py la începutul rulării: unde se salvează copia raportului și ce model apare în antet."""
    global _run_dir, _model, _report_md, _started
    _run_dir = run_dir
    _model = model or _model
    _report_md = None
    _started = dt.datetime.now()


def write_html(summary: list | None = None, stop_reason: str = "") -> list[str]:
    """Scrie raportul HTML (pagina 1: raportul, pagina 2: jurnalul de până acum).

    Cu raport valid: `raport_sateliti.html` în proiect + copie în `runs/<data-ora>/`.
    Fără raport (rulare oprită): doar în `runs/<data-ora>/`, ca să nu suprascrie raportul bun anterior.
    """
    meta = {"model": _model, "when": f"{_started:%Y-%m-%d %H:%M}", "run": _run_dir.name if _run_dir else "",
            "summary": summary or [], "stop_reason": stop_reason, "disclaimer": DISCLAIMER}
    page = html_report.render(_report_md, journal.EVENTS, journal.TOOL_RESULTS, meta)
    targets = [config.REPORT_PATH] if _report_md else []
    if _run_dir:
        targets.append(_run_dir / config.REPORT_PATH.name)
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page, encoding="utf-8")
    return [str(p) for p in targets]


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def check_report(markdown: str) -> dict:
    """Numără secțiunile obligatorii; ridică ToolError cu ce lipsește."""
    if not markdown or len(markdown.strip()) < 500:
        raise ToolError("Raportul e gol sau prea scurt.",
                        hint="Trimite raportul complet în Markdown, într-un singur apel.")
    plain = _plain(markdown)
    counts = {label: len(re.findall(re.escape(key), plain)) for key, label in SECTIONS.items()}
    problems = []
    companies = min(counts.values())
    if companies < MIN_COMPANIES:
        problems.append(f"am găsit doar {companies} companii complete (minimum {MIN_COMPANIES}); "
                        f"fiecare companie trebuie să aibă subsecțiunile {list(SECTIONS.values())} "
                        f"(numărate: {counts})")
    if max(counts.values()) > MAX_COMPANIES:
        problems.append(f"prea multe companii ({max(counts.values())}); portofoliul Satelit are "
                        f"{MIN_COMPANIES}-{MAX_COMPANIES}")
    if GRAVEYARD[0] not in plain:
        problems.append(f"lipsește secțiunea „{GRAVEYARD[1]}” cu 2-3 companii respinse și motivul")
    if problems:
        raise ToolError("Raport incomplet: " + "; ".join(problems) + ".",
                        hint="Corectează și retrimite raportul COMPLET (nu doar partea lipsă).")
    return {"companies": companies}


@safe_tool
def generate_client_report(markdown_content: str) -> dict:
    """Salvează raportul final ca pagină HTML (raport_sateliti.html) și îl afișează formatat în terminal."""
    global _report_md
    stats = check_report(markdown_content)
    _report_md = markdown_content.strip()
    paths = write_html()

    ui.show_markdown(f"{_report_md}\n\n---\n\n> ⚠️ *{DISCLAIMER}*", title=f"📄 {config.REPORT_PATH.name}")
    ui.success(f"Raport salvat: {paths[0]}")
    return {"path": paths[0], "copies": paths[1:], "companies": stats["companies"]}
