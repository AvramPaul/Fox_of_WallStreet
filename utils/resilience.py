"""Reziliență: retry cu backoff, rate-limiting per sursă, cache pe disc, wrapper sigur pentru tool-uri."""

import functools
import hashlib
import json
import math
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests
from tenacity import (retry, retry_if_exception, stop_after_attempt,
                      wait_exponential_jitter)

import config
from utils import console as ui

SETTINGS = config.load()


# ── Erori ────────────────────────────────────────────────────────────────────

class TransientError(Exception):
    """Eroare temporară (rate-limit, 5xx, timeout) — merită reîncercată."""


class ToolError(Exception):
    """Eroare intenționată a unui tool, cu indiciu pentru agent."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (TransientError, requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    # Excepții de rate-limit din librării terțe (ex. yfinance YFRateLimitError, ddgs RatelimitException)
    return "ratelimit" in type(exc).__name__.lower().replace("_", "")


# ── Retry ────────────────────────────────────────────────────────────────────

def with_retry(source: str, attempts: int | None = None, wait=None):
    """Decorator: reîncearcă doar erorile temporare, cu backoff exponențial + jitter."""

    def log_retry(state):
        exc = state.outcome.exception()
        ui.warn(f"[{source}] {type(exc).__name__}: {exc} — reîncerc "
                f"({state.attempt_number}/{attempts or SETTINGS.retry_attempts}) "
                f"peste {state.next_action.sleep:.1f}s")

    return retry(
        retry=retry_if_exception(is_transient),
        stop=stop_after_attempt(attempts or SETTINGS.retry_attempts),
        wait=wait or wait_exponential_jitter(initial=1, max=20),
        before_sleep=log_retry,
        reraise=True,
    )


# ── Rate limiting ────────────────────────────────────────────────────────────

class RateLimiter:
    """Impune un interval minim între cereri consecutive către aceeași sursă."""

    def __init__(self, intervals: dict[str, float]):
        self.intervals = intervals
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, source: str) -> None:
        interval = self.intervals.get(source, 0.0)
        with self._lock:
            now = time.monotonic()
            delay = self._last.get(source, -math.inf) + interval - now
            if delay > 0:
                time.sleep(delay)
            self._last[source] = time.monotonic()


rate_limiter = RateLimiter(SETTINGS.min_interval)


# ── Cache pe disc ────────────────────────────────────────────────────────────

def make_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class DiskCache:
    """Cache JSON pe disc cu TTL: .cache/<namespace>/<sha1>.json"""

    enabled = True  # dezactivat global de `agent.py --no-cache`

    def __init__(self, namespace: str, ttl: float, root: Path = config.CACHE_DIR):
        self.dir = root / namespace
        self.ttl = ttl

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> Any | None:
        if not DiskCache.enabled:
            return None
        try:
            entry = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if time.time() - entry.get("ts", 0) > self.ttl:
            return None
        return entry.get("value")

    def set(self, key: str, value: Any) -> None:
        if not DiskCache.enabled:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"ts": time.time(), "value": clean(value)}, ensure_ascii=False)
            self._path(key).write_text(payload, encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            ui.warn(f"cache: nu am putut scrie {key[:8]}: {exc}")


# ── Serializare compactă ─────────────────────────────────────────────────────

def clean(obj: Any) -> Any:
    """Convertește recursiv la tipuri JSON: NaN/inf → None, numpy/pandas → Python."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [clean(v) for v in obj]
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    if hasattr(obj, "item"):  # numpy scalar
        try:
            return clean(obj.item())
        except (ValueError, TypeError):
            pass
    if hasattr(obj, "isoformat"):  # datetime / Timestamp
        return obj.isoformat()
    return str(obj)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _longest_list(obj: Any) -> list | None:
    """Cea mai „grea” listă (după dimensiunea serializată) din structură."""
    best, best_size = None, 0
    stack = [obj]
    while stack:
        node = stack.pop()
        children = node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()
        if isinstance(node, list) and len(node) > 1:
            size = len(_dumps(node))
            if size > best_size:
                best, best_size = node, size
        stack.extend(c for c in children if isinstance(c, (dict, list)))
    return best


def truncate_json(obj: Any, max_chars: int | None = None) -> str:
    """JSON compact sub `max_chars`: scurtează listele cele mai mari, marchează `truncated`."""
    max_chars = max_chars or SETTINGS.max_tool_output_chars
    obj = clean(obj)
    text = _dumps(obj)
    if len(text) <= max_chars:
        return text
    if isinstance(obj, dict):
        obj["truncated"] = True
    while len(text) > max_chars:
        target = _longest_list(obj)
        if target is None:
            break
        del target[max(1, len(target) * 3 // 4):]
        text = _dumps(obj)
    if len(text) > max_chars:
        text = _dumps({"ok": obj.get("ok", True) if isinstance(obj, dict) else True,
                       "truncated": True, "partial_json": text[: max_chars - 80]})
    return text


# ── Wrapper pentru tool-uri ──────────────────────────────────────────────────

TRANSIENT_HINT = ("Sursa e indisponibilă temporar (rate-limit/rețea) după mai multe reîncercări. "
                  "Continuă cu datele pe care le ai sau încearcă altă companie/abordare.")


def safe_tool(fn: Callable[..., dict]) -> Callable[..., str]:
    """Tool-ul întoarce mereu JSON (str) și nu aruncă niciodată spre agent."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> str:
        try:
            result = fn(*args, **kwargs)
            if isinstance(result, dict):
                result.setdefault("ok", True)
            return truncate_json(result)
        except ToolError as exc:
            ui.show_tool_error(fn.__name__, str(exc), exc.hint)
            return truncate_json({"ok": False, "error": str(exc), "hint": exc.hint})
        except Exception as exc:  # noqa: BLE001 — granița dintre tool și agent
            transient = is_transient(exc)
            err = f"{type(exc).__name__}: {exc}"
            hint = TRANSIENT_HINT if transient else "Verifică parametrii sau încearcă altă abordare."
            ui.show_tool_error(fn.__name__, err, hint)
            return truncate_json({"ok": False, "error": err[:500], "hint": hint})

    return wrapper
