"""ActionParser 测试。

覆盖：
- JSON 提取（裸 JSON / markdown 代码块 / 大小写不敏感 / 前后说明文字）
- Alias 映射（kill / inspect / shoot / pass_shoot / jump_claim / 大小写）
- Identity 字段覆盖（game_id / agent_id / role / phase 由 context 强制写入）
- 错误路径（empty / json_decode / not_object / missing_action_type / pydantic_validation）
- ParseError 携带 raw + reason
"""

from __future__ import annotations

import json

import pytest

from contracts import ActionType, ClaimedAlignment, Role

from agent_runtime.action_parser import ActionParser
from agent_runtime.exceptions import ParseError
from tests.fixtures.agent_contexts import (
    day_discussion_context,
    hunter_shoot_context,
    seer_context,
    vote_context,
    werewolf_context,
)


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


class TestJSONExtraction:
    def setup_method(self):
        self.parser = ActionParser()

    def test_plain_json_object(self):
        raw = '{"action_type": "vote", "target": "P3"}'
        action = self.parser.parse(raw, vote_context())
        assert action.action_type == ActionType.VOTE
        assert action.target == "P3"

    def test_json_markdown_fence(self):
        raw = '```json\n{"action_type": "vote", "target": "P3"}\n```'
        action = self.parser.parse(raw, vote_context())
        assert action.action_type == ActionType.VOTE

    def test_plain_markdown_fence_no_json_tag(self):
        raw = '```\n{"action_type": "vote", "target": "P3"}\n```'
        action = self.parser.parse(raw, vote_context())
        assert action.action_type == ActionType.VOTE

    def test_markdown_fence_uppercase(self):
        raw = '```JSON\n{"action_type": "vote", "target": "P3"}\n```'
        action = self.parser.parse(raw, vote_context())
        assert action.action_type == ActionType.VOTE

    def test_leading_and_trailing_whitespace(self):
        raw = '   \n  {"action_type": "vote", "target": "P3"}  \n  '
        action = self.parser.parse(raw, vote_context())
        assert action.action_type == ActionType.VOTE

    def test_llm_explains_then_outputs_json_block(self):
        # LLM 常见输出：先说话再 JSON
        raw = (
            "Based on the suspicion around P3, I'll vote them.\n"
            "```json\n"
            '{"action_type": "vote", "target": "P3"}\n'
            "```\n"
            "End of decision."
        )
        action = self.parser.parse(raw, vote_context())
        assert action.target == "P3"

    def test_multiline_json(self):
        raw = (
            "{\n"
            '  "action_type": "vote",\n'
            '  "target": "P3",\n'
            '  "public_message": "I vote for P3."\n'
            "}"
        )
        action = self.parser.parse(raw, vote_context())
        assert action.public_message == "I vote for P3."


# ---------------------------------------------------------------------------
# Alias mapping
# ---------------------------------------------------------------------------


class TestActionTypeAliasMapping:
    def setup_method(self):
        self.parser = ActionParser()

    def test_standard_action_type_passes_through(self):
        raw = json.dumps({"action_type": "vote", "target": "P3"})
        action = self.parser.parse(raw, vote_context())
        assert action.action_type == ActionType.VOTE

    @pytest.mark.parametrize(
        "alias",
        ["kill", "wolf_kill", "night_kill", "nominate"],
    )
    def test_night_kill_aliases(self, alias):
        raw = json.dumps({"action_type": alias, "target": "P2"})
        action = self.parser.parse(raw, werewolf_context())
        assert action.action_type == ActionType.NIGHT_KILL_NOMINATE
        assert action.target == "P2"

    @pytest.mark.parametrize("alias", ["inspect", "verify", "see", "investigate"])
    def test_check_aliases(self, alias):
        raw = json.dumps({"action_type": alias, "target": "P1"})
        action = self.parser.parse(raw, seer_context())
        assert action.action_type == ActionType.CHECK

    @pytest.mark.parametrize("alias", ["shoot", "fire", "revenge"])
    def test_hunter_shoot_aliases(self, alias):
        raw = json.dumps({"action_type": alias, "target": "P2"})
        action = self.parser.parse(raw, hunter_shoot_context())
        assert action.action_type == ActionType.HUNTER_SHOOT
        assert action.target == "P2"

    def test_pass_shoot_forces_target_none_and_pass_metadata(self):
        raw = json.dumps({"action_type": "pass_shoot", "target": "P2"})
        action = self.parser.parse(raw, hunter_shoot_context())
        assert action.action_type == ActionType.HUNTER_SHOOT
        assert action.target is None  # 即使 LLM 给了 target，pass_shoot 也强制 None
        assert action.metadata["pass"] is True

    def test_pass_shoot_preserves_other_metadata_keys(self):
        raw = json.dumps(
            {
                "action_type": "pass_shoot",
                "metadata": {"confidence": 0.8},
            }
        )
        action = self.parser.parse(raw, hunter_shoot_context())
        assert action.metadata["pass"] is True
        assert action.metadata["confidence"] == 0.8

    @pytest.mark.parametrize(
        "alias",
        [
            "jump_claim",
            "claim_seer",
            "claim_witch",
            "claim_hunter",
            "claim_villager",
            "defend",
            "accuse",
            "argue",
            "quarrel",
            "talk",
            "say",
            "discuss",
        ],
    )
    def test_speak_aliases(self, alias):
        raw = json.dumps(
            {
                "action_type": alias,
                "public_message": "I think P3 is suspicious.",
            }
        )
        action = self.parser.parse(raw, day_discussion_context())
        assert action.action_type == ActionType.SPEAK
        assert action.public_message == "I think P3 is suspicious."

    def test_alias_case_insensitive(self):
        raw = json.dumps({"action_type": "KILL", "target": "P2"})
        action = self.parser.parse(raw, werewolf_context())
        assert action.action_type == ActionType.NIGHT_KILL_NOMINATE

    def test_alias_whitespace_trimmed(self):
        raw = json.dumps({"action_type": "  kill  ", "target": "P2"})
        action = self.parser.parse(raw, werewolf_context())
        assert action.action_type == ActionType.NIGHT_KILL_NOMINATE


# ---------------------------------------------------------------------------
# Identity 字段覆盖（防 prompt injection）
# ---------------------------------------------------------------------------


class TestIdentityOverride:
    def setup_method(self):
        self.parser = ActionParser()

    def test_context_identity_fills_in(self):
        # 简单情况：LLM 只给 action_type / target，identity 由 context 补
        raw = json.dumps({"action_type": "vote", "target": "P3"})
        ctx = vote_context()
        action = self.parser.parse(raw, ctx)
        assert action.game_id == ctx.game_id
        assert action.agent_id == ctx.agent_id
        assert action.role == ctx.role
        assert action.phase == ctx.phase

    def test_llm_game_id_is_overridden(self):
        # LLM 试图伪造 game_id —— 必须被 context 覆盖
        raw = json.dumps(
            {
                "action_type": "vote",
                "target": "P3",
                "game_id": "evil_game",
                "agent_id": "evil_agent",
            }
        )
        ctx = vote_context()
        action = self.parser.parse(raw, ctx)
        assert action.game_id == ctx.game_id
        assert action.agent_id == ctx.agent_id

    def test_llm_role_phase_overridden(self):
        # LLM 试图伪造 role/phase
        raw = json.dumps(
            {
                "action_type": "vote",
                "target": "P3",
                "role": "werewolf",
                "phase": "NIGHT_WEREWOLF",
            }
        )
        ctx = vote_context()  # role=VILLAGER, phase=DAY_VOTE
        action = self.parser.parse(raw, ctx)
        assert action.role == Role.VILLAGER
        assert action.phase == ctx.phase


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------


class TestParseErrorPaths:
    def setup_method(self):
        self.parser = ActionParser()

    def test_empty_string_raises(self):
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse("", vote_context())
        assert exc_info.value.reason == "empty_input"

    def test_whitespace_only_raises(self):
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse("   \n\t  ", vote_context())
        assert exc_info.value.reason == "empty_input"

    def test_invalid_json_raises_decode_error(self):
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse("not a json {{{", vote_context())
        assert exc_info.value.reason == "json_decode"

    def test_json_array_not_object_raises(self):
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse('["vote", "P3"]', vote_context())
        assert exc_info.value.reason == "not_object"

    def test_json_string_not_object_raises(self):
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse('"just a string"', vote_context())
        assert exc_info.value.reason == "not_object"

    def test_missing_action_type_raises(self):
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse('{"target": "P3"}', vote_context())
        assert exc_info.value.reason == "missing_action_type"

    def test_action_type_non_string_raises(self):
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse('{"action_type": 42}', vote_context())
        assert exc_info.value.reason == "missing_action_type"

    def test_unknown_action_type_raises_pydantic_error(self):
        # 既不是 alias 也不是标准 enum → pydantic 验证拒掉
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse(
                '{"action_type": "rave_party"}', vote_context()
            )
        assert exc_info.value.reason == "pydantic_validation"

    def test_parse_error_preserves_raw(self):
        raw = "totally broken {{{"
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse(raw, vote_context())
        assert exc_info.value.raw == raw

    def test_parse_error_chains_original_exception(self):
        with pytest.raises(ParseError) as exc_info:
            self.parser.parse("not json", vote_context())
        assert exc_info.value.__cause__ is not None


# ---------------------------------------------------------------------------
# Optional 字段处理
# ---------------------------------------------------------------------------


class TestOptionalFields:
    def setup_method(self):
        self.parser = ActionParser()

    def test_minimal_payload_works(self):
        raw = json.dumps({"action_type": "skip"})
        # NIGHT_WITCH 允许 skip
        from tests.fixtures.agent_contexts import witch_context
        action = self.parser.parse(raw, witch_context())
        assert action.action_type == ActionType.SKIP
        assert action.target is None
        assert action.public_message is None

    def test_role_claim_passes_through(self):
        raw = json.dumps(
            {
                "action_type": "speak",
                "public_message": "I am the seer.",
                "role_claim": "seer",
            }
        )
        action = self.parser.parse(raw, day_discussion_context())
        assert action.role_claim == Role.SEER

    def test_claim_result_passes_through(self):
        raw = json.dumps(
            {
                "action_type": "speak",
                "public_message": "I checked P4 and they are a werewolf.",
                "role_claim": "seer",
                "claim_result": {
                    "target": "P4",
                    "claimed_alignment": "werewolf",
                },
            }
        )
        action = self.parser.parse(raw, day_discussion_context())
        assert action.claim_result is not None
        assert action.claim_result.target == "P4"
        assert (
            action.claim_result.claimed_alignment == ClaimedAlignment.WEREWOLF
        )


# ---------------------------------------------------------------------------
# Stateless / 复用
# ---------------------------------------------------------------------------


class TestStateless:
    def test_same_instance_handles_multiple_calls(self):
        parser = ActionParser()
        raw1 = json.dumps({"action_type": "vote", "target": "P3"})
        raw2 = json.dumps({"action_type": "check", "target": "P1"})
        a1 = parser.parse(raw1, vote_context())
        a2 = parser.parse(raw2, seer_context())
        assert a1.action_type == ActionType.VOTE
        assert a2.action_type == ActionType.CHECK

    def test_no_cross_call_contamination(self):
        """连续调用：第一次 payload 修改不能污染第二次。"""
        parser = ActionParser()
        raw1 = json.dumps({"action_type": "pass_shoot"})
        raw2 = json.dumps({"action_type": "shoot", "target": "P2"})
        a1 = parser.parse(raw1, hunter_shoot_context())
        a2 = parser.parse(raw2, hunter_shoot_context())
        # 第一次 pass_shoot 设置了 metadata.pass，第二次正常 shoot 不应该有 pass=True
        assert a1.metadata.get("pass") is True
        assert a2.metadata.get("pass") is not True
