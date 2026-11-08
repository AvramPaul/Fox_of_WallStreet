"""Paranoid Wolf — Satellite Hunter: agent autonom (ReAct / tool calling) peste Claude API.

Rulare:  python agent.py [--max-iterations N] [--effort high] [--model ...] [--no-cache] [--mandate "..."]
"""

import argparse
import datetime as dt
import json
import sys
import webbrowser
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

import config
import prompts
import tools
from tools import report_tool
from utils import console as ui
from utils import journal
from utils.resilience import DiskCache

REPORT_TOOL = "generate_client_report"
BETAS = ["server-side-fallback-2026-07-01"]
LAST_ITERATIONS_WARNING = 3
MAX_NO_REPORT_NUDGES = 2
MAX_REFUSALS = 1

# $/milion tokeni: (input, output, cache read, cache write 5 min)
PRICING = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


@dataclass
class RunState:
    iteration: int = 0
    phase: int | None = None
    calls: Counter = field(default_factory=Counter)
    report_done: bool = False
    report_path: str | None = None
    no_report_nudges: int = 0
    refusals: int = 0
    warned_last_iterations: bool = False
    usage: Counter = field(default_factory=Counter)
    stop_reason: str = ""


# ── Ajutoare ─────────────────────────────────────────────────────────────────

def _budgets(settings: config.Settings) -> dict[str, int]:
    return {"finviz_dynamic_screener": settings.max_screener_calls,
            "deep_fundamental_inspector": settings.max_inspector_calls,
            "paranoid_osint_radar": settings.max_osint_calls}


def _block_to_dict(block) -> dict:
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    if isinstance(block, dict):
        return block
    return {k: v for k, v in vars(block).items() if not k.startswith("_")}


def save_transcript(path: Path, messages: list, state: RunState) -> None:
    serial = [{"role": m["role"],
               "content": m["content"] if isinstance(m["content"], str)
               else [_block_to_dict(b) for b in m["content"]]} for m in messages]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state": {"iterations": state.iteration, "calls": dict(state.calls),
                                          "report_done": state.report_done, "usage": dict(state.usage),
                                          "stop_reason": state.stop_reason},
                                "messages": serial}, ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")


def _accumulate_usage(state: RunState, usage) -> None:
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        state.usage[key] += getattr(usage, key, 0) or 0


def estimate_cost(model: str, usage: Counter) -> float | None:
    if model not in PRICING:
        return None
    p_in, p_out, p_read, p_write = PRICING[model]
    return (usage["input_tokens"] * p_in + usage["output_tokens"] * p_out
            + usage["cache_read_input_tokens"] * p_read + usage["cache_creation_input_tokens"] * p_write) / 1e6


def _show_response(response) -> None:
    for block in response.content:
        kind = getattr(block, "type", None)
        if kind == "thinking":
            ui.show_thought(getattr(block, "thinking", "") or "")
        elif kind == "text":
            ui.show_agent_text(block.text)


def _request(client, settings: config.Settings, messages: list):
    params = dict(
        model=settings.model,
        max_tokens=settings.max_tokens,
        system=prompts.SYSTEM_PROMPT,
        tools=tools.TOOLS,
        messages=messages,
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": settings.effort},
        cache_control={"type": "ephemeral"},
        betas=BETAS,
        fallbacks="default",
    )
    with ui.console.status("[orange3]🦊 Vulpea gândește…[/orange3]", spinner="dots"):
        with client.beta.messages.stream(**params) as stream:
            return stream.get_final_message()


def _run_tools(response, state: RunState, settings: config.Settings, truncated: bool) -> list[dict]:
    """Execută toate tool_use-urile din răspuns → un singur mesaj user cu toate tool_result-urile."""
    budgets = _budgets(settings)
    results = []
    for block in response.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        name, tool_input = block.name, block.input
        phase = tools.PHASE_OF.get(name)
        if phase and phase != state.phase:
            state.phase = phase
            ui.phase_banner(phase)

        if truncated:
            content = json.dumps({"ok": False, "error": "Apelul a fost tăiat de limita de lungime; input incomplet.",
                                  "hint": "Retrimite apelul complet, mai concis."}, ensure_ascii=False)
            is_error = True
        elif name in budgets and state.calls[name] >= budgets[name]:
            content = json.dumps({"ok": False, "error": "Buget epuizat.",
                                  "hint": prompts.nudge_budget(name, budgets[name])}, ensure_ascii=False)
            is_error = True
            ui.show_tool_error(name, "buget epuizat", f"limită {budgets[name]} apeluri")
        else:
            ui.show_tool_call(name, tool_input if isinstance(tool_input, dict) else {"input": tool_input})
            content, is_error = tools.dispatch(name, tool_input)
            state.calls[name] += 1
            if name == REPORT_TOOL and not is_error:
                state.report_done = True
                state.report_path = json.loads(content).get("path")
        results.append({"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error})
    return results


# ── Bucla ReAct ──────────────────────────────────────────────────────────────

def run(settings: config.Settings, client, mandate: str | None = None, run_dir: Path | None = None,
        today: str | None = None, open_browser: bool = False) -> RunState:
    run_dir = run_dir or config.RUNS_DIR / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    journal.reset()
    report_tool.configure(run_dir=run_dir, model=settings.model)
    state = RunState()
    messages: list[dict] = [{"role": "user",
                             "content": prompts.build_kickoff(today or dt.date.today().isoformat(), mandate)}]
    ui.console.rule("[bold orange3]🐺 Paranoid Wolf — Satellite Hunter[/bold orange3]")
    ui.info(f"model {settings.model} · effort {settings.effort} · max {settings.max_iterations} iterații · "
            f"rulare {run_dir.name}")

    try:
        while state.iteration < settings.max_iterations:
            state.iteration += 1
            ui.iteration_header(state.iteration, settings.max_iterations)
            try:
                response = _request(client, settings, messages)
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
                ui.error(f"Autentificare eșuată ({type(exc).__name__}). Pune ANTHROPIC_API_KEY în .env "
                         "(vezi .env.example).")
                state.stop_reason = "auth_error"
                break
            except TypeError as exc:
                # SDK-ul aruncă TypeError când nu găsește nicio credențială (cheie, token, profil).
                if "authentication" not in str(exc).lower():
                    raise
                ui.error("Nu am găsit credențiale Anthropic. Copiază .env.example în .env și completează "
                         "ANTHROPIC_API_KEY.")
                state.stop_reason = "auth_error"
                break
            except anthropic.BadRequestError as exc:
                ui.error(f"Cerere respinsă de API: {exc}")
                state.stop_reason = "bad_request"
                break
            except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError,
                    anthropic.APIStatusError) as exc:
                # SDK-ul a reîncercat deja (max_retries); ne oprim curat, cu transcriptul salvat.
                ui.error(f"API indisponibil după reîncercări: {type(exc).__name__}: {exc}")
                state.stop_reason = "api_error"
                break

            _accumulate_usage(state, response.usage)
            ui.show_usage(response.usage)
            _show_response(response)
            messages.append({"role": "assistant", "content": response.content})  # append-only, inclusiv thinking
            stop = response.stop_reason
            state.stop_reason = stop or ""

            if stop == "refusal":
                state.refusals += 1
                category = getattr(getattr(response, "stop_details", None), "category", None)
                ui.warn(f"Modelul a refuzat cererea (categorie: {category}).")
                if state.refusals > MAX_REFUSALS:
                    break
                messages.append({"role": "user", "content": "Continuă analiza financiară publică a companiilor, "
                                                            "strict pe baza datelor din unelte."})
                continue

            truncated = stop == "max_tokens"
            user_content: list[dict] = _run_tools(response, state, settings, truncated)
            if state.report_done:
                break
            if truncated:
                user_content.append({"type": "text", "text": prompts.NUDGE_MAX_TOKENS})
            elif not user_content:  # end_turn (sau pause_turn) fără raport
                if state.no_report_nudges >= MAX_NO_REPORT_NUDGES:
                    ui.warn("Agentul s-a oprit de mai multe ori fără raport.")
                    break
                state.no_report_nudges += 1
                user_content.append({"type": "text", "text": prompts.NUDGE_NO_REPORT})

            left = settings.max_iterations - state.iteration
            if left <= LAST_ITERATIONS_WARNING and not state.warned_last_iterations:
                state.warned_last_iterations = True
                user_content.append({"type": "text", "text": prompts.nudge_last_iterations(left)})
            messages.append({"role": "user", "content": user_content})
        else:
            state.stop_reason = "max_iterations"
    except KeyboardInterrupt:
        ui.warn("Întrerupt de utilizator (Ctrl+C).")
        state.stop_reason = "interrupted"
    finally:
        save_transcript(run_dir / "transcript.json", messages, state)

    rows = _summary_rows(settings, state, run_dir)
    _show_summary(state, rows)
    _write_html(state, rows, open_browser)
    return state


def _write_html(state: RunState, rows: list, open_browser: bool) -> None:
    """Rescrie raportul HTML la final, cu tot jurnalul și rezumatul rulării."""
    try:
        paths = report_tool.write_html(summary=[[str(k), str(v)] for k, v in rows], stop_reason=state.stop_reason)
    except Exception as exc:  # pagina HTML nu trebuie să strice o rulare reușită
        ui.warn(f"Nu am putut scrie raportul HTML: {type(exc).__name__}: {exc}")
        return
    if not paths:
        return
    page = Path(paths[0])
    if not state.report_done:
        ui.info(f"Jurnalul rulării (fără raport): {page}")
    if open_browser:
        try:
            webbrowser.open(page.resolve().as_uri())
        except Exception:
            pass


def _summary_rows(settings: config.Settings, state: RunState, run_dir: Path) -> list:
    u = state.usage
    total_in = u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"]
    hit = f"{100 * u['cache_read_input_tokens'] / total_in:.0f}%" if total_in else "–"
    cost = estimate_cost(settings.model, u)
    rows = [["Iterații", state.iteration], ["Oprire", state.stop_reason or "–"],
            *[[f"Apeluri {name}", n] for name, n in state.calls.items()],
            ["Tokeni input (necache-uiți)", f"{u['input_tokens']:,}"],
            ["Tokeni cache citit / scris", f"{u['cache_read_input_tokens']:,} / {u['cache_creation_input_tokens']:,}"],
            ["Tokeni output", f"{u['output_tokens']:,}"], ["Cache hit", hit],
            ["Cost estimat", f"${cost:.2f}" if cost is not None else "–"],
            ["Transcript", str(run_dir / "transcript.json")]]
    return rows


def _show_summary(state: RunState, rows: list) -> None:
    ui.console.print()
    ui.show_table("📋 Rezumatul rulării", ["", ""], rows)
    if state.report_done:
        ui.success(f"Raport final: {state.report_path}")
    else:
        ui.error("Rularea s-a încheiat fără raport final.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paranoid Wolf — Satellite Hunter")
    parser.add_argument("--max-iterations", type=int, help="limita buclei ReAct (implicit din .env / 40)")
    parser.add_argument("--effort", choices=config.VALID_EFFORTS, help="efortul de gândire (implicit high)")
    parser.add_argument("--model", help="modelul Claude (implicit claude-opus-5-5)")
    parser.add_argument("--no-cache", action="store_true", help="ignoră cache-ul de date de pe disc")
    parser.add_argument("--mandate", help="mandatul clientului (text liber), în locul celui implicit")
    parser.add_argument("--no-open", action="store_true", help="nu deschide raportul HTML în browser la final")
    args = parser.parse_args(argv)

    settings = config.load(model=args.model, effort=args.effort, max_iterations=args.max_iterations)
    DiskCache.enabled = not args.no_cache
    try:
        client = anthropic.Anthropic(max_retries=4)
    except anthropic.AnthropicError as exc:
        ui.error(f"Nu pot crea clientul Anthropic: {exc}. Pune ANTHROPIC_API_KEY în .env (vezi .env.example).")
        return 2
    state = run(settings, client, mandate=args.mandate, open_browser=not args.no_open)
    return 0 if state.report_done else 1


if __name__ == "__main__":
    sys.exit(main())
