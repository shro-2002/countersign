"""Judge demo mode rules that need no network: session header, agent allowlist, seeds only."""

import dataclasses

import pytest
from fastapi.testclient import TestClient

from countersign import api, demo
from countersign.config import Settings

DEMO = Settings("id", "secret", "https://api-m.sandbox.paypal.com", "postgresql://unused", None, "tok", demo_mode=True)


@pytest.fixture
def demo_client(monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: DEMO)
    return TestClient(api.app, headers={"Authorization": "Bearer tok"})


@pytest.mark.parametrize("session", [None, "short", "Z" * 32])
def test_demo_mode_requires_a_well_formed_session(demo_client, session):
    headers = {"X-Countersign-Session": session} if session else {}
    assert demo_client.post("/api/cases", json={"seed": "F1"}, headers=headers).status_code == 401
    assert demo_client.get("/api/cases/00000000-0000-0000-0000-000000000000", headers=headers).status_code == 401


def test_operator_token_still_required_in_demo_mode(monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: DEMO)
    client = TestClient(api.app)
    assert client.post("/api/cases", json={"seed": "F1"}, headers={"X-Countersign-Session": "a" * 32}).status_code == 401


def test_unknown_seed_and_free_text_are_refused():
    with pytest.raises(demo.DemoLimit) as exc:
        demo.seed("F99")
    assert exc.value.status == 403


def test_agent_allowlist_and_forced_scripted_seed():
    assert demo.agent_for("F7", "gemini:gemini-2.5-flash") == "scripted-dup:0.25"  # F7 is always SCRIPTED
    assert demo.agent_for("F1", "gemini:gemini-flash-lite-latest") == "gemini:gemini-flash-lite-latest"
    for blocked in ("ollama:llama3.1", "scripted:0.01", "gemini:gemini-2.5-pro"):
        with pytest.raises(demo.DemoLimit):
            demo.agent_for("F1", blocked)


def test_seeds_come_from_the_frozen_cases_unchanged():
    ids = [s["id"] for s in demo.seeds()]
    assert ids == ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8"]
    assert dataclasses.replace(DEMO).demo_mode


def usage(session_cases=0, day_cases=0, day_llm_runs=0) -> dict:
    return {"session_cases": session_cases, "day_cases": day_cases, "day_llm_runs": day_llm_runs}


@pytest.mark.parametrize("over", [usage(session_cases=demo.MAX_CASES_PER_SESSION), usage(day_cases=demo.MAX_CASES_PER_DAY)])
def test_case_caps_refuse_with_429(over):
    demo.check_case_caps(usage())
    with pytest.raises(demo.DemoLimit) as exc:
        demo.check_case_caps(over)
    assert exc.value.status == 429


def test_llm_cap_blocks_live_models_but_never_the_scripted_agent():
    spent = usage(day_llm_runs=demo.MAX_LLM_RUNS_PER_DAY)
    demo.check_llm_cap("scripted:1.00", spent)
    with pytest.raises(demo.DemoLimit):
        demo.check_llm_cap("gemini:gemini-flash-lite-latest", spent)
