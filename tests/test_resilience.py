import json
import time

import pytest
import requests
from tenacity import wait_none

from utils import resilience as r


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=resp)


# ── is_transient ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("exc, expected", [
    (r.TransientError("x"), True),
    (requests.Timeout(), True),
    (requests.ConnectionError(), True),
    (_http_error(429), True),
    (_http_error(503), True),
    (_http_error(404), False),
    (ValueError("x"), False),
    (type("YFRateLimitError", (Exception,), {})(), True),
    (type("RatelimitException", (Exception,), {})(), True),
])
def test_is_transient(exc, expected):
    assert r.is_transient(exc) is expected


# ── with_retry ───────────────────────────────────────────────────────────────

def test_retry_recovers_after_transient_failures():
    calls = {"n": 0}

    @r.with_retry("test", attempts=4, wait=wait_none())
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise r.TransientError("rate limited")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_does_not_retry_permanent_errors():
    calls = {"n": 0}

    @r.with_retry("test", attempts=4, wait=wait_none())
    def broken():
        calls["n"] += 1
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        broken()
    assert calls["n"] == 1


def test_retry_gives_up_and_reraises():
    calls = {"n": 0}

    @r.with_retry("test", attempts=3, wait=wait_none())
    def always_429():
        calls["n"] += 1
        raise _http_error(429)

    with pytest.raises(requests.HTTPError):
        always_429()
    assert calls["n"] == 3


# ── RateLimiter ──────────────────────────────────────────────────────────────

def test_rate_limiter_enforces_min_interval():
    limiter = r.RateLimiter({"src": 0.2})
    start = time.monotonic()
    limiter.wait("src")  # primul apel nu așteaptă
    limiter.wait("src")
    assert time.monotonic() - start >= 0.19


def test_rate_limiter_unknown_source_does_not_wait():
    limiter = r.RateLimiter({})
    start = time.monotonic()
    limiter.wait("x")
    limiter.wait("x")
    assert time.monotonic() - start < 0.05


# ── DiskCache ────────────────────────────────────────────────────────────────

def test_cache_roundtrip_and_expiry(tmp_path, monkeypatch):
    cache = r.DiskCache("ns", ttl=60, root=tmp_path)
    key = r.make_key("finviz", {"b": 2, "a": 1})
    assert cache.get(key) is None
    cache.set(key, {"rows": [1, 2], "v": float("nan")})
    assert cache.get(key) == {"rows": [1, 2], "v": None}

    real_time = time.time
    monkeypatch.setattr(r.time, "time", lambda: real_time() + 61)
    assert cache.get(key) is None


def test_make_key_is_order_independent():
    assert r.make_key({"a": 1, "b": 2}) == r.make_key({"b": 2, "a": 1})


def test_cache_ignores_corrupt_file(tmp_path):
    cache = r.DiskCache("ns", ttl=60, root=tmp_path)
    cache.dir.mkdir(parents=True)
    (cache.dir / "bad.json").write_text("{not json", encoding="utf-8")
    assert cache.get("bad") is None


def test_cache_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(r.DiskCache, "enabled", False)
    cache = r.DiskCache("ns", ttl=60, root=tmp_path)
    cache.set("k", 1)
    assert cache.get("k") is None
    assert not cache.dir.exists()


# ── truncate_json / clean ────────────────────────────────────────────────────

def test_truncate_json_shrinks_longest_list():
    obj = {"ok": True, "count": 500, "rows": [{"ticker": f"T{i}", "pe": i} for i in range(500)]}
    text = r.truncate_json(obj, max_chars=1000)
    data = json.loads(text)
    assert len(text) <= 1000
    assert data["truncated"] is True
    assert data["count"] == 500
    assert 1 <= len(data["rows"]) < 500


def test_truncate_json_untouched_when_small():
    data = json.loads(r.truncate_json({"ok": True, "rows": [1, 2]}, max_chars=1000))
    assert data == {"ok": True, "rows": [1, 2]}


def test_truncate_json_hard_cut_for_huge_string():
    text = r.truncate_json({"ok": True, "blob": "x" * 5000}, max_chars=500)
    data = json.loads(text)
    assert len(text) <= 500
    assert data["truncated"] is True


def test_clean_handles_nan_numpy_and_dates():
    import numpy as np
    import pandas as pd
    out = r.clean({"a": float("inf"), "b": np.float64(1.5), "c": np.int64(3),
                   "d": pd.Timestamp("2025-12-31"), "e": (1, 2)})
    assert out == {"a": None, "b": 1.5, "c": 3, "d": "2025-12-31T00:00:00", "e": [1, 2]}


# ── safe_tool ────────────────────────────────────────────────────────────────

def test_safe_tool_success_adds_ok():
    @r.safe_tool
    def tool(x: int) -> dict:
        return {"value": x}

    assert json.loads(tool(5)) == {"value": 5, "ok": True}


def test_safe_tool_tool_error_keeps_hint():
    @r.safe_tool
    def tool() -> dict:
        raise r.ToolError("filtru invalid", hint="valori valide: Under 20")

    data = json.loads(tool())
    assert data == {"ok": False, "error": "filtru invalid", "hint": "valori valide: Under 20"}


def test_safe_tool_unexpected_exception_never_raises():
    @r.safe_tool
    def tool() -> dict:
        raise _http_error(429)

    data = json.loads(tool())
    assert data["ok"] is False
    assert "HTTPError" in data["error"]
    assert data["hint"] == r.TRANSIENT_HINT


def test_safe_tool_preserves_name():
    @r.safe_tool
    def finviz_dynamic_screener() -> dict:
        return {}

    assert finviz_dynamic_screener.__name__ == "finviz_dynamic_screener"
