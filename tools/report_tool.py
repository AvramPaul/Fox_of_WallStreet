"""generate_client_report — validează, salvează (`raport_sateliti.md`) și afișează raportul final."""

import datetime as dt
import re
import unicodedata
from pathlib import Path

import config
from utils import console as ui
from utils.resilience import ToolError, safe_tool

SETTINGS = config.load()
MIN_COMPANIES, MAX_COMPANIES = 3, 5
SECTIONS = {  # secțiunile cerute pentru fiecare companie (comparate fără diacritice)
    "povestea pe sleau": "Povestea pe șleau",
    "de ce cifrele arata beton": "De ce cifrele arată beton",
    "testul meu de paranoia": "Testul meu de Paranoia",
}
GRAVEYARD = ("cimitirul iluziilor", "Cimitirul Iluziilor (Firme pe care le-am aruncat la gunoi azi)")
DISCLAIMER = ("> ⚠️ *Material educațional generat automat de un agent AI pe baza unor surse publice gratuite "
              "(FinViz, Yahoo Finance, DuckDuckGo, HackerNews, GitHub, Reddit). Nu este o recomandare de "
              "investiții. Verifică datele și decide pe propria răspundere.*")

_run_dir: Path | None = None
_model: str = SETTINGS.model


def configure(run_dir: Path | None = None, model: str | None = None) -> None:
    """Apelat de agent.py: unde se salvează copia raportului și ce model apare în antet."""
    global _run_dir, _model
    _run_dir = run_dir
    _model = model or _model


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
    """Salvează raportul final în raport_sateliti.md și îl afișează formatat în terminal."""
    stats = check_report(markdown_content)
    now = dt.datetime.now()
    header = f"*🐺 Paranoid Wolf — Satellite Hunter · {now:%Y-%m-%d %H:%M} · model `{_model}`*\n\n"
    document = header + markdown_content.strip() + "\n\n---\n\n" + DISCLAIMER + "\n"

    config.REPORT_PATH.write_text(document, encoding="utf-8")
    paths = [str(config.REPORT_PATH)]
    if _run_dir:
        _run_dir.mkdir(parents=True, exist_ok=True)
        copy = _run_dir / config.REPORT_PATH.name
        copy.write_text(document, encoding="utf-8")
        paths.append(str(copy))

    ui.show_markdown(document, title=f"📄 {config.REPORT_PATH.name}")
    ui.success(f"Raport salvat: {paths[0]}")
    return {"path": paths[0], "copies": paths[1:], "chars": len(document), "companies": stats["companies"]}
