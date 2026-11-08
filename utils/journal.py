"""Jurnalul rulării: tot ce afișează consola + rezultatele uneltelor.

Consola (`utils.console`) scrie aici fiecare mesaj, ca structură (nu ca text colorat), iar `tools.dispatch`
adaugă rezultatele uneltelor. Din ele se construiește raportul HTML: pagina „Jurnal” și fișele companiilor.
"""

import datetime as dt
import json
from typing import Any

EVENTS: list[dict] = []
TOOL_RESULTS: list[dict] = []


def reset() -> None:
    """Golește jurnalul (apelat la începutul fiecărei rulări)."""
    EVENTS.clear()
    TOOL_RESULTS.clear()


def event(kind: str, **data: Any) -> None:
    EVENTS.append({"kind": kind, "t": dt.datetime.now().strftime("%H:%M:%S"), **data})


def tool_result(name: str, args: Any, content: str) -> None:
    """Păstrează rezultatul (JSON) al unei unelte; ignoră ce nu se poate citi."""
    try:
        result = json.loads(content)
    except (TypeError, ValueError):
        return
    if isinstance(result, dict):
        TOOL_RESULTS.append({"name": name, "args": args if isinstance(args, dict) else {}, "result": result})
