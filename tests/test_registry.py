import json

import pytest
from jsonschema import Draft202012Validator

import tools


def test_tool_names_match_functions_and_phases():
    names = [t["name"] for t in tools.TOOLS]
    assert names == list(tools.FUNCTIONS) == list(tools.PHASE_OF)
    assert [tools.PHASE_OF[n] for n in names] == [1, 2, 3, 4]


@pytest.mark.parametrize("tool", tools.TOOLS, ids=lambda t: t["name"])
def test_schemas_are_valid_and_closed(tool):
    Draft202012Validator.check_schema(tool["input_schema"])
    assert tool["input_schema"]["additionalProperties"] is False
    assert tool["input_schema"]["required"]
    assert tool["eager_input_streaming"] is True
    assert len(tool["description"]) > 100


def test_definitions_are_deterministic():
    # Prefixul cache-uit (tools+system) trebuie să fie identic la fiecare import/iterație.
    from tools.finviz_tool import filter_catalog
    assert filter_catalog() == filter_catalog()
    assert filter_catalog() in tools.TOOLS[0]["description"]
    assert "Price/Free Cash Flow" in tools.TOOLS[0]["description"]


def test_dispatch_unknown_tool():
    content, is_error = tools.dispatch("nope", {})
    assert is_error and "necunoscută" in json.loads(content)["error"]


@pytest.mark.parametrize("name, bad_input, fragment", [
    ("deep_fundamental_inspector", {}, "ticker"),
    ("deep_fundamental_inspector", {"ticker": "MSFT", "extra": 1}, "Additional properties"),
    ("finviz_dynamic_screener", {"filters": {"P/E": 20}}, "filters/P/E"),
    ("paranoid_osint_radar", {"company_name": "X", "ticker": "X", "industry": "Y",
                              "extra_queries": ["a", "b", "c", "d"]}, "extra_queries"),
    ("generate_client_report", {"markdown_content": None}, "markdown_content"),
])
def test_dispatch_validates_schema(name, bad_input, fragment):
    content, is_error = tools.dispatch(name, bad_input)
    data = json.loads(content)
    assert is_error and data["ok"] is False and fragment in data["error"]


def test_dispatch_not_a_dict():
    _, is_error = tools.dispatch("deep_fundamental_inspector", "MSFT")
    assert is_error


def test_dispatch_runs_tool_and_maps_ok(monkeypatch):
    monkeypatch.setitem(tools.FUNCTIONS, "deep_fundamental_inspector",
                        lambda ticker: json.dumps({"ok": True, "ticker": ticker}))
    content, is_error = tools.dispatch("deep_fundamental_inspector", {"ticker": "MSFT"})
    assert not is_error and json.loads(content)["ticker"] == "MSFT"

    monkeypatch.setitem(tools.FUNCTIONS, "deep_fundamental_inspector",
                        lambda ticker: json.dumps({"ok": False, "error": "x"}))
    _, is_error = tools.dispatch("deep_fundamental_inspector", {"ticker": "MSFT"})
    assert is_error
