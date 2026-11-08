"""Bucla agentului testată cu un client Claude simulat (răspunsuri scriptate)."""

import json
from dataclasses import replace
from types import SimpleNamespace as NS

import httpx2
import pytest

import agent
import config
import tools
from tests.test_report_tool import report as valid_report

SETTINGS = config.load()


# ── Client simulat ───────────────────────────────────────────────────────────

def thinking(text):
    return NS(type="thinking", thinking=text, signature="sig")


def text(t):
    return NS(type="text", text=t)


def tool_use(name, tool_input, id_):
    return NS(type="tool_use", id=id_, name=name, input=tool_input)


def response(blocks, stop="tool_use", cache_read=0):
    usage = NS(input_tokens=100, output_tokens=50, cache_read_input_tokens=cache_read,
               cache_creation_input_tokens=0)
    return NS(content=blocks, stop_reason=stop, usage=usage, stop_details=None)


class FakeStream:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **params):
        # copie superficială a listei de mesaje: vedem exact ce s-a trimis la fiecare iterație
        self.calls.append(params | {"messages": list(params["messages"])})
        return FakeStream(self.script.pop(0))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPORT_PATH", tmp_path / "raport_sateliti.html")
    fake = {
        "finviz_dynamic_screener": lambda filters, angle="": json.dumps({"ok": True, "count": 8, "rows": []}),
        "deep_fundamental_inspector": lambda ticker: json.dumps({"ok": True, "ticker": ticker, "verdict": "PASS"}),
        "paranoid_osint_radar": lambda **kw: json.dumps({"ok": True, "threat_summary": {}}),
    }
    for name, fn in fake.items():
        monkeypatch.setitem(tools.FUNCTIONS, name, fn)
    return tmp_path


def run(script, tmp_path, **overrides):
    client = FakeClient(script)
    state = agent.run(replace(SETTINGS, **overrides), client, run_dir=tmp_path / "run", today="2026-10-28")
    return state, client


def tool_results(client, call_index):
    """Conținutul ultimului mesaj user trimis la cererea `call_index`."""
    return client.calls[call_index]["messages"][-1]["content"]


# ── Scenarii ─────────────────────────────────────────────────────────────────

def test_happy_path_runs_all_phases(env):
    script = [
        response([thinking("Încep cu GARP"), text("Primul unghi: GARP."),
                  tool_use("finviz_dynamic_screener", {"filters": {"P/E": "<25"}, "angle": "GARP"}, "t1")]),
        response([tool_use("deep_fundamental_inspector", {"ticker": "MSFT"}, "t2"),
                  tool_use("deep_fundamental_inspector", {"ticker": "NVDA"}, "t3")], cache_read=5000),
        response([tool_use("paranoid_osint_radar", {"company_name": "Microsoft", "ticker": "MSFT",
                                                    "industry": "Software", "product": "office software"}, "t4")]),
        response([tool_use("generate_client_report", {"markdown_content": "prea scurt"}, "t5")]),
        response([tool_use("generate_client_report", {"markdown_content": valid_report(3)}, "t6")]),
    ]
    state, client = run(script, env)

    assert state.report_done and state.stop_reason == "tool_use"
    assert state.iteration == 5 and len(client.calls) == 5
    assert state.calls == {"finviz_dynamic_screener": 1, "deep_fundamental_inspector": 2,
                           "paranoid_osint_radar": 1, "generate_client_report": 2}
    assert state.phase == 4
    # cele două inspecții paralele → ambele tool_result într-un singur mesaj user
    results = tool_results(client, 2)
    assert [r["tool_use_id"] for r in results] == ["t2", "t3"]
    # raportul invalid → is_error, agentul primește motivul
    bad = tool_results(client, 4)[0]
    assert bad["is_error"] is True and "prea scurt" in json.loads(bad["content"])["error"]
    assert config.REPORT_PATH.exists()
    assert state.usage["cache_read_input_tokens"] == 5000


def test_request_params_are_cache_friendly(env):
    script = [response([tool_use("generate_client_report", {"markdown_content": valid_report(3)}, "t1")])]
    _, client = run(script, env)
    p = client.calls[0]
    assert p["model"] == SETTINGS.model and p["max_tokens"] == SETTINGS.max_tokens
    assert p["system"] == agent.prompts.SYSTEM_PROMPT and p["tools"] is tools.TOOLS
    assert p["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert p["output_config"] == {"effort": SETTINGS.effort}
    assert p["cache_control"] == {"type": "ephemeral"}
    assert p["fallbacks"] == "default" and p["betas"] == agent.BETAS
    assert "2026-10-28" in p["messages"][0]["content"]


def test_history_is_append_only(env):
    script = [
        response([tool_use("finviz_dynamic_screener", {"filters": {}}, "t1")]),
        response([tool_use("finviz_dynamic_screener", {"filters": {}}, "t2")]),
        response([tool_use("generate_client_report", {"markdown_content": valid_report(3)}, "t3")]),
    ]
    _, client = run(script, env)
    first, second, third = (c["messages"] for c in client.calls)
    assert second[:len(first)] == first and third[:len(second)] == second


def test_end_turn_without_report_gets_nudged_then_stops(env):
    script = [response([text("Gata, cred.")], stop="end_turn")] * 3
    state, client = run(script, env)
    assert not state.report_done and len(client.calls) == 3
    nudge = tool_results(client, 1)
    assert nudge[-1]["text"] == agent.prompts.NUDGE_NO_REPORT


def test_budget_exhausted_returns_error_result(env):
    script = [
        response([tool_use("finviz_dynamic_screener", {"filters": {}}, "t1"),
                  tool_use("finviz_dynamic_screener", {"filters": {}}, "t2")]),
        response([tool_use("generate_client_report", {"markdown_content": valid_report(3)}, "t3")]),
    ]
    state, client = run(script, env, max_screener_calls=1)
    r1, r2 = tool_results(client, 1)
    assert r1["is_error"] is False
    assert r2["is_error"] is True and "epuizat" in r2["content"]
    assert state.calls["finviz_dynamic_screener"] == 1


def test_max_tokens_with_tool_use_returns_error_and_nudge(env):
    script = [
        response([tool_use("generate_client_report", {"markdown_content": "trunc"}, "t1")], stop="max_tokens"),
        response([tool_use("generate_client_report", {"markdown_content": valid_report(3)}, "t2")]),
    ]
    state, client = run(script, env)
    content = tool_results(client, 1)
    assert content[0]["is_error"] is True and "tăiat" in content[0]["content"]
    assert content[-1]["text"] == agent.prompts.NUDGE_MAX_TOKENS
    assert state.report_done


def test_max_iterations_and_last_iterations_warning(env):
    script = [response([tool_use("finviz_dynamic_screener", {"filters": {}}, f"t{i}")]) for i in range(4)]
    state, client = run(script, env, max_iterations=4)
    assert state.stop_reason == "max_iterations" and not state.report_done
    warned = [c for c in tool_results(client, 1) if c.get("type") == "text"]
    assert warned and "3 iterații" in warned[0]["text"]


def test_refusal_retries_once_then_stops(env):
    script = [response([], stop="refusal")] * 2
    state, client = run(script, env)
    assert state.refusals == 2 and len(client.calls) == 2 and not state.report_done


def test_auth_error_stops_cleanly_and_saves_transcript(env):
    err = agent.anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx2.Response(401, request=httpx2.Request("POST", "https://api")), body=None)
    state, _ = run([err], env)
    assert state.stop_reason == "auth_error"
    saved = json.loads((env / "run" / "transcript.json").read_text(encoding="utf-8"))
    assert saved["messages"][0]["role"] == "user"


def test_missing_credentials_type_error_is_auth_error(env):
    state, _ = run([TypeError("Could not resolve authentication method. Expected one of api_key")], env)
    assert state.stop_reason == "auth_error"


def test_transcript_serializes_blocks(env):
    script = [response([thinking("hmm"), tool_use("generate_client_report",
                                                  {"markdown_content": valid_report(3)}, "t1")])]
    run(script, env)
    saved = json.loads((env / "run" / "transcript.json").read_text(encoding="utf-8"))
    assert saved["state"]["report_done"] is True
    assert saved["messages"][1]["content"][0] == {"type": "thinking", "thinking": "hmm", "signature": "sig"}


def test_estimate_cost():
    from collections import Counter
    usage = Counter(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=1_000_000,
                    cache_creation_input_tokens=0)
    assert agent.estimate_cost("claude-opus-5-5", usage) == pytest.approx(4.0 + 2.0 + 0.2)
    assert agent.estimate_cost("model-necunoscut", usage) is None


# ── Live: LLM simulat + unelte REALE (pipeline cap-coadă fără cheie API) ─────

@pytest.mark.live
def test_live_pipeline_with_real_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPORT_PATH", tmp_path / "raport_sateliti.html")
    script = [
        response([tool_use("finviz_dynamic_screener",
                           {"filters": {"Market Cap.": "+Mid (over $2bln)", "P/E": "<25", "P/FCF": "<20",
                                        "LT Debt/Equity": "<0.5", "ROE": ">15%"}, "angle": "GARP"}, "t1")]),
        response([tool_use("deep_fundamental_inspector", {"ticker": "MSFT"}, "t2")]),
        response([tool_use("paranoid_osint_radar", {"company_name": "Chegg, Inc.", "ticker": "CHGG",
                                                    "industry": "Education", "product": "homework help"}, "t3")]),
        response([tool_use("generate_client_report", {"markdown_content": valid_report(3)}, "t4")]),
    ]
    state, client = run(script, tmp_path)
    assert state.report_done
    for i, key in ((1, "count"), (2, "series"), (3, "threat_summary")):
        result = json.loads(tool_results(client, i)[0]["content"])
        assert result["ok"] and key in result, result
