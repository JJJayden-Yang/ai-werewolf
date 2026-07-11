from __future__ import annotations

import asyncio
import json

import pytest

from agent_policy.slow_think_reflector import LLMSlowThinkReflector
from agent_runtime.llm_provider import FakeLLMProvider
from contracts import (
    AgentContext,
    BeliefState,
    Phase,
    PlayerStatus,
    Role,
    RoleBelief,
    VisiblePlayer,
)


def _ctx(*, agent_id="P6", round_=2, phase=Phase.DAY_VOTE):
    return AgentContext(
        game_id="g",
        agent_id=agent_id,
        role=Role.VILLAGER,
        round=round_,
        phase=phase,
        visible_players=[
            VisiblePlayer(player_id=p, status=PlayerStatus.ALIVE)
            for p in ("P1", "P2", "P3", "P6")
        ],
        belief_top_suspects=[{"player_id": "P1", "werewolf_prob": 0.3}],
    )


def _uniform_belief(game_id="g", agent_id="P6"):
    return BeliefState(
        game_id=game_id,
        agent_id=agent_id,
        round=1,
        phase=Phase.DAY_DISCUSSION,
        beliefs={
            p: RoleBelief(werewolf=0.2, seer=0.2, witch=0.2, hunter=0.2, villager=0.2)
            for p in ("P1", "P2", "P3", "P6")
        },
    )


def _empty_belief(game_id="g", agent_id="P6"):
    return BeliefState(game_id=game_id, agent_id=agent_id, beliefs={})


def _run(coro):
    return asyncio.run(coro)


def test_default_max_tokens_is_1024():
    r = LLMSlowThinkReflector(FakeLLMProvider("{}"))
    assert r._model_config["max_tokens"] == 1024


def test_prompt_omits_per_player_note():
    r = LLMSlowThinkReflector(FakeLLMProvider("{}"))
    prompt = r._build_messages(_ctx(), _uniform_belief())[0]["content"]
    assert '"note"' not in prompt
    assert "note" not in prompt


def test_should_reflect_only_at_decision_points():
    r = LLMSlowThinkReflector(FakeLLMProvider("{}"))
    assert r.should_reflect("g", Phase.DAY_VOTE, 2) is True
    assert r.should_reflect("g", Phase.NIGHT_WITCH, 2) is False
    assert r.should_reflect("g", Phase.NIGHT_SEER, 2) is False
    assert r.should_reflect("g", Phase.NIGHT_WEREWOLF, 2) is False
    assert r.should_reflect("g", Phase.DAY_DISCUSSION, 2) is False
    assert r.should_reflect("g", Phase.DAY_ANNOUNCEMENT, 2) is False


def test_should_reflect_stops_after_max_reflections():
    r = LLMSlowThinkReflector(FakeLLMProvider("{}"), max_reflections=2)
    assert r.should_reflect("g", Phase.DAY_VOTE, 2) is True
    r.stats["reflections"] = 2
    assert r.should_reflect("g", Phase.DAY_VOTE, 2) is False


def test_custom_reflect_phases_still_supported():
    r = LLMSlowThinkReflector(
        FakeLLMProvider("{}"),
        reflect_phases=frozenset({Phase.NIGHT_SEER}),
    )
    assert r.should_reflect("g", Phase.NIGHT_SEER, 2) is True
    assert r.should_reflect("g", Phase.DAY_VOTE, 2) is False


def test_reflection_sharpens_belief_toward_llm_suspicion():
    canned = json.dumps(
        {
            "assessments": [
                {"player_id": "P1", "werewolf_suspicion": 0.92, "note": "flip-flopped"},
                {"player_id": "P2", "werewolf_suspicion": 0.05, "note": "clean"},
            ],
            "self_reasoning": "P1 contradicted earlier claim",
        }
    )
    r = LLMSlowThinkReflector(FakeLLMProvider(canned), gain=0.6)
    enriched = _run(r.reflect("g", "P6", _uniform_belief(), _ctx()))

    assert enriched.beliefs["P1"].werewolf > 0.2  # 被推高
    assert enriched.beliefs["P2"].werewolf < 0.2  # 被压低
    assert r.stats["applied"] == 1
    total = sum(
        getattr(enriched.beliefs["P1"], f)
        for f in ("werewolf", "seer", "witch", "hunter", "villager")
    )
    assert total == pytest.approx(1.0)  # 仍归一


def test_reflection_sharpens_seer_likelihood():
    canned = json.dumps(
        {
            "assessments": [
                {
                    "player_id": "P3",
                    "werewolf_suspicion": 0.2,
                    "seer_likelihood": 0.9,
                    "confidence": 1.0,
                }
            ],
            "self_reasoning": "P3 gave coherent checks",
        }
    )
    r = LLMSlowThinkReflector(FakeLLMProvider(canned), gain=0.6)
    enriched = _run(r.reflect("g", "P6", _uniform_belief(), _ctx()))

    assert enriched.beliefs["P3"].seer > 0.2


def test_reflection_parses_new_shape_without_note():
    canned = json.dumps(
        {
            "assessments": [
                {
                    "player_id": "P3",
                    "werewolf_suspicion": 0.1,
                    "seer_likelihood": 0.9,
                    "witch_likelihood": 0.05,
                    "confidence": 0.8,
                }
            ],
            "self_reasoning": "compact schema",
        }
    )
    r = LLMSlowThinkReflector(FakeLLMProvider(canned), gain=0.6)
    enriched = _run(r.reflect("g", "P6", _uniform_belief(), _ctx()))

    assert enriched.beliefs["P3"].werewolf < 0.2
    assert enriched.beliefs["P3"].seer > 0.2
    assert enriched.beliefs["P3"].witch < 0.2


def test_reflection_ignores_legacy_note_field():
    canned = json.dumps(
        {
            "assessments": [
                {
                    "player_id": "P3",
                    "werewolf_suspicion": 0.1,
                    "seer_likelihood": 0.9,
                    "witch_likelihood": 0.05,
                    "confidence": 0.8,
                    "note": "legacy extra text",
                }
            ],
            "self_reasoning": "legacy schema still accepted",
        }
    )
    r = LLMSlowThinkReflector(FakeLLMProvider(canned), gain=0.6)
    enriched = _run(r.reflect("g", "P6", _uniform_belief(), _ctx()))

    assert enriched.beliefs["P3"].werewolf < 0.2
    assert enriched.beliefs["P3"].seer > 0.2
    assert enriched.beliefs["P3"].witch < 0.2


def test_reflection_sharpens_witch_likelihood():
    canned = json.dumps(
        {
            "assessments": [
                {
                    "player_id": "P2",
                    "werewolf_suspicion": 0.2,
                    "witch_likelihood": 0.85,
                    "confidence": 1.0,
                }
            ],
            "self_reasoning": "P2 matched public witch-like behavior",
        }
    )
    r = LLMSlowThinkReflector(FakeLLMProvider(canned), gain=0.6)
    enriched = _run(r.reflect("g", "P6", _uniform_belief(), _ctx()))

    assert enriched.beliefs["P2"].witch > 0.2


def test_reflection_confidence_scales_nudge_strength():
    def _response(confidence):
        return json.dumps(
            {
                "assessments": [
                    {
                        "player_id": "P1",
                        "werewolf_suspicion": 0.95,
                        "confidence": confidence,
                    }
                ],
                "self_reasoning": "same claim, different confidence",
            }
        )

    low = LLMSlowThinkReflector(FakeLLMProvider(_response(0.1)), gain=0.6)
    high = LLMSlowThinkReflector(FakeLLMProvider(_response(0.9)), gain=0.6)

    low_out = _run(low.reflect("g", "P6", _uniform_belief(), _ctx()))
    high_out = _run(high.reflect("g", "P6", _uniform_belief(), _ctx()))

    low_delta = low_out.beliefs["P1"].werewolf - 0.2
    high_delta = high_out.beliefs["P1"].werewolf - 0.2
    assert 0 < low_delta < high_delta


def test_legacy_reflection_format_only_targets_werewolf_dimension():
    canned = json.dumps(
        {
            "assessments": [{"player_id": "P1", "werewolf_suspicion": 0.92}],
            "self_reasoning": "legacy shape",
        }
    )
    r = LLMSlowThinkReflector(FakeLLMProvider(canned), gain=0.6)
    enriched = _run(r.reflect("g", "P6", _uniform_belief(), _ctx()))
    p1 = enriched.beliefs["P1"]

    assert p1.werewolf > 0.2
    assert p1.seer == pytest.approx(p1.witch)
    assert p1.witch == pytest.approx(p1.hunter)
    assert p1.hunter == pytest.approx(p1.villager)


def test_reflection_prompt_contains_no_truth_or_role_map():
    captured = {}

    def _capture(messages, model_config):
        captured["text"] = json.dumps(messages, ensure_ascii=False)
        return json.dumps({"assessments": [], "self_reasoning": ""})

    r = LLMSlowThinkReflector(FakeLLMProvider(_capture))
    _run(r.reflect("g", "P6", _uniform_belief(), _ctx()))

    blob = captured["text"].lower()
    for forbidden in ("truth_state", "role_map", "hidden_role", "werewolf_team", "teammates"):
        assert forbidden not in blob


def test_parse_failure_returns_current_unchanged_and_no_crash():
    current = _uniform_belief()
    r = LLMSlowThinkReflector(FakeLLMProvider("not a json at all"))
    out = _run(r.reflect("g", "P6", current, _ctx()))

    assert out.beliefs["P1"].werewolf == pytest.approx(0.2)  # 不变
    assert r.stats["reflect_errors"] == 1
    assert r.stats["reflect_llm_errors"] == 0
    assert r.stats["reflect_parse_errors"] == 1
    assert r.stats["reflect_errors"] == (
        r.stats["reflect_llm_errors"] + r.stats["reflect_parse_errors"]
    )
    assert r.stats["applied"] == 0


def test_llm_failure_returns_current_unchanged_and_tracks_llm_error():
    current = _uniform_belief()

    def _raise(_messages, _model_config):
        raise TimeoutError("synthetic timeout")

    r = LLMSlowThinkReflector(FakeLLMProvider(_raise))
    out = _run(r.reflect("g", "P6", current, _ctx()))

    assert out is current
    assert r.stats["reflect_errors"] == 1
    assert r.stats["reflect_llm_errors"] == 1
    assert r.stats["reflect_parse_errors"] == 0
    assert r.stats["reflect_errors"] == (
        r.stats["reflect_llm_errors"] + r.stats["reflect_parse_errors"]
    )
    assert r.stats["applied"] == 0
    assert r.reflections[-1]["error"].startswith("llm:TimeoutError")


def test_locked_belief_not_touched_by_reflection():
    current = _uniform_belief()
    current.beliefs["P1"] = RoleBelief(werewolf=1.0, locked=True, lock_reason="seer_check")
    canned = json.dumps(
        {
            "assessments": [
                {
                    "player_id": "P1",
                    "werewolf_suspicion": 0.0,
                    "seer_likelihood": 0.9,
                    "witch_likelihood": 0.9,
                }
            ],
            "self_reasoning": "",
        }
    )
    r = LLMSlowThinkReflector(FakeLLMProvider(canned))
    out = _run(r.reflect("g", "P6", current, _ctx()))

    assert out.beliefs["P1"].locked is True
    assert out.beliefs["P1"].werewolf == pytest.approx(1.0)  # locked 不被慢思改
    assert out.beliefs["P1"].seer == pytest.approx(0.0)


def test_empty_belief_returns_unchanged_without_calling_llm():
    # 纯变换：传入空 belief（belief lane 无数据）→ 原样返回，不发 LLM。
    r = LLMSlowThinkReflector(FakeLLMProvider("{}"))
    passed = _empty_belief()
    out = _run(r.reflect("g", "P6", passed, _ctx()))
    assert out is passed
    assert r.stats["reflections"] == 0
