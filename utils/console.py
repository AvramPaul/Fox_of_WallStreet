"""Consolă rich partajată + helperi de afișare pentru fiecare pas al agentului."""

import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

# Pe Windows stdout poate fi cp1252 → diacriticele/emoji ar arunca UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and (_stream.encoding or "").lower() != "utf-8":
        _stream.reconfigure(encoding="utf-8", errors="replace")

console = Console(highlight=False)

PHASES = {
    1: ("Faza 1 — Jocul cu filtrele FinViz", "cyan"),
    2: ("Faza 2 — Autopsia Contabilă", "magenta"),
    3: ("Faza 3 — Testul de Paranoia (OSINT)", "yellow"),
    4: ("Faza 4 — Raportul „Vulpea de pe Wall Street”", "green"),
}


# ── Mesaje generale ──────────────────────────────────────────────────────────

def info(msg: str) -> None:
    console.print(f"[dim]•[/dim] {escape(msg)}")


def success(msg: str) -> None:
    console.print(f"[bold green]✔[/bold green] {escape(msg)}")


def warn(msg: str) -> None:
    console.print(f"[bold yellow]⚠[/bold yellow] [yellow]{escape(msg)}[/yellow]")


def error(msg: str) -> None:
    console.print(f"[bold red]✖[/bold red] [red]{escape(msg)}[/red]")


# ── Bucla agentului ──────────────────────────────────────────────────────────

def phase_banner(phase: int) -> None:
    title, color = PHASES.get(phase, (f"Faza {phase}", "white"))
    console.print()
    console.print(Rule(f"[bold {color}]{title}[/bold {color}]", style=color))


def iteration_header(n: int, max_n: int) -> None:
    console.print(f"\n[dim]── iterația {n}/{max_n} ──[/dim]")


def show_thought(text: str) -> None:
    """Rezumatul gândirii modelului (thinking display='summarized')."""
    if text and text.strip():
        console.print(Panel(Text(text.strip(), style="italic"), title="💭 Gândul Vulpii",
                            title_align="left", border_style="bright_black"))


def show_agent_text(text: str) -> None:
    """Text vizibil scris de agent (ex. fraza dinaintea unui apel de tool)."""
    if text and text.strip():
        console.print(Panel(Markdown(text.strip()), title="🦊 Vulpea", title_align="left",
                            border_style="orange3"))


def show_tool_call(name: str, args: Mapping[str, Any]) -> None:
    shown = ", ".join(f"{k}={_short(v)}" for k, v in args.items() if k != "filters")
    console.print(f"[bold blue]🔧 {name}[/bold blue]([dim]{shown}[/dim])")


def show_tool_error(name: str, err: str, hint: str | None = None) -> None:
    msg = f"{name}: {err}"
    if hint:
        msg += f"  → [italic]{hint}[/italic]"
    warn(msg)


def show_usage(usage: Any) -> None:
    """Tokeni pe iterație; acceptă obiectul Usage din SDK sau un dict."""
    get = usage.get if isinstance(usage, Mapping) else lambda k, d=0: getattr(usage, k, d)
    inp = get("input_tokens", 0) or 0
    out = get("output_tokens", 0) or 0
    c_read = get("cache_read_input_tokens", 0) or 0
    c_write = get("cache_creation_input_tokens", 0) or 0
    total_in = inp + c_read + c_write
    hit = f"{100 * c_read / total_in:.0f}%" if total_in else "–"
    console.print(f"[dim]tokeni: in {inp:,} · cache citit {c_read:,} · cache scris {c_write:,} "
                  f"· out {out:,} · cache hit {hit}[/dim]")


# ── FinViz ───────────────────────────────────────────────────────────────────

def show_filters_diff(prev: Mapping[str, str] | None, curr: Mapping[str, str], angle: str = "") -> None:
    """Tabel cu filtrele curente față de apelul anterior (adăugat / modificat / scos)."""
    prev = prev or {}
    table = Table(title=f"🎛  Filtre FinViz{f' — {angle}' if angle else ''}", title_justify="left",
                  show_lines=False, header_style="bold")
    table.add_column("Filtru")
    table.add_column("Înainte", style="dim")
    table.add_column("Acum")
    table.add_column("", justify="center")
    for key in list(curr) + [k for k in prev if k not in curr]:
        before, now = prev.get(key), curr.get(key)
        if before is None:
            mark, style = "➕", "green"
        elif now is None:
            mark, style = "➖", "red"
        elif before != now:
            mark, style = "✏️", "yellow"
        else:
            mark, style = "", ""
        table.add_row(key, before or "", Text(now or "—", style=style), mark)
    console.print(table)


def show_screener_count(count: int, guidance: str) -> None:
    if 5 <= count <= 12:
        style = "bold green"
    elif count <= 2 or count > 30:
        style = "bold red"
    else:
        style = "bold yellow"
    console.print(f"[{style}]→ {count} companii[/{style}]  [italic]{guidance}[/italic]")


# ── Generic ──────────────────────────────────────────────────────────────────

def show_table(title: str, columns: Sequence[str], rows: Iterable[Sequence[Any]],
               styles: Mapping[str, str] | None = None) -> None:
    styles = styles or {}
    table = Table(title=title, title_justify="left", header_style="bold")
    for col in columns:
        table.add_column(col, style=styles.get(col, ""))
    for row in rows:
        table.add_row(*("" if v is None else str(v) for v in row))
    console.print(table)


def show_markdown(md: str, title: str = "") -> None:
    console.print(Panel(Markdown(md), title=title or None, border_style="green", padding=(1, 2)))


def _short(value: Any, limit: int = 60) -> str:
    s = repr(value) if not isinstance(value, str) else f'"{value}"'
    return s if len(s) <= limit else s[: limit - 1] + "…"


if __name__ == "__main__":
    # Demo: `python -m utils.console`
    phase_banner(1)
    iteration_header(1, 40)
    show_thought("Încep cu GARP: P/E sub 25, PEG sub 1.5, datorie mică. Vreau creștere, nu povești.")
    show_agent_text("Primul unghi: **Growth at a Reasonable Price**. Vedem ce prinde plasa.")
    show_tool_call("finviz_dynamic_screener", {"angle": "GARP", "filters": {"P/E": "Under 25"}})
    show_filters_diff(None, {"P/E": "Under 25", "PEG": "Under 1.5", "LT Debt/Equity": "Under 0.5"}, "GARP")
    show_screener_count(47, "PREA MULTE: strânge șurubul.")
    show_filters_diff({"P/E": "Under 25", "PEG": "Under 1.5", "LT Debt/Equity": "Under 0.5"},
                      {"P/E": "Under 20", "PEG": "Under 1.5", "Price/Free Cash Flow": "Under 20"}, "GARP v2")
    show_screener_count(9, "ȚINTĂ ATINSĂ: listă scurtă de elită.")
    show_usage({"input_tokens": 1200, "output_tokens": 450, "cache_read_input_tokens": 8800,
                "cache_creation_input_tokens": 0})
    phase_banner(2)
    show_table("📊 MSFT — ultimii ani (mil. USD)", ["An", "FCF", "Venituri", "Marjă op."],
               [[2022, "65,149", "198,270", "42.1%"], [2023, "59,475", "211,915", "41.8%"]])
    show_tool_error("deep_fundamental_inspector", "rând lipsă: Free Cash Flow", "date parțiale")
    phase_banner(3)
    info("GitHub: căutare „open source alternative to Chegg”")
    phase_banner(4)
    show_markdown("# Raport\n\n**Povestea pe șleau:** o mașinărie de făcut bani.", "raport_sateliti.md")
    success("Demo complet.")
    error("Exemplu de eroare.")
