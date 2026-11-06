import os
import re

import pytest

import config
import prompts


def _annex_a() -> str:
    plan = (config.ROOT / "PLAN.md").read_text(encoding="utf-8")
    section = plan.split("## Anexa A", 1)[1].split("## Anexa B", 1)[0]
    return re.search(r"```\n(.*?)\n```", section, re.S).group(1)


def test_persona_is_exactly_the_client_directive():
    assert prompts.PERSONA == _annex_a()


def test_system_prompt_is_static_and_contains_both_parts():
    assert prompts.SYSTEM_PROMPT.startswith(prompts.PERSONA)
    assert "Faza 1" in prompts.SYSTEM_PROMPT and "generate_client_report" in prompts.SYSTEM_PROMPT
    assert not re.search(r"20\d\d-\d\d-\d\d", prompts.SYSTEM_PROMPT)  # nicio dată → cache stabil


def test_protocol_mentions_report_sections_checked_by_report_tool():
    from tools.report_tool import GRAVEYARD, SECTIONS
    for label in SECTIONS.values():
        assert label in prompts.OPERATING_PROTOCOL
    assert GRAVEYARD[1] in prompts.OPERATING_PROTOCOL


def test_protocol_budgets_match_config():
    s = config.load()
    assert f"maximum {s.max_screener_calls} apeluri de screener" in prompts.OPERATING_PROTOCOL
    assert f"{s.max_inspector_calls} inspecții" in prompts.OPERATING_PROTOCOL
    assert f"{s.max_osint_calls} radare OSINT" in prompts.OPERATING_PROTOCOL


def test_kickoff_has_date_and_default_mandate():
    msg = prompts.build_kickoff("2026-10-28")
    assert "2026-10-28" in msg and prompts.DEFAULT_MANDATE in msg
    assert "doar tech" in prompts.build_kickoff("2026-10-28", "Vreau doar tech")


def test_nudges():
    assert "10 apeluri" in prompts.nudge_budget("finviz_dynamic_screener", 10)
    assert "3 iterații" in prompts.nudge_last_iterations(3)


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="fără ANTHROPIC_API_KEY")
def test_live_prefix_token_count():
    import anthropic

    import tools
    s = config.load()
    count = anthropic.Anthropic().messages.count_tokens(
        model=s.model, system=prompts.SYSTEM_PROMPT, tools=tools.TOOLS,
        messages=[{"role": "user", "content": prompts.build_kickoff("2026-10-28")}])
    assert 4096 <= count.input_tokens < 20000  # peste pragul minim de caching, sub orice exces
