"""ActionCanonicalizer 测试。

覆盖：
- No-op：clean action 透传
- META_AI 扫描（中英文）
- COT_LEAK 扫描（中英文）
- Sanitize 行为：SPEAK 替换 message；非 SPEAK 清空 message
- metadata 携带 canonicalized + canonicalize_triggered + canonicalize_original_message
- CanonicalizationError：SPEAK action 但 SPEAK 不在 allowed_actions
- ROLE_LEAK 当前是空集 —— 验证不误报
"""

from __future__ import annotations

import pytest

from contracts import (
    ActionType,
    AgentAction,
    Role,
)

from agent_runtime.action_canonicalizer import ActionCanonicalizer
from agent_runtime.exceptions import CanonicalizationError
from tests.fixtures.agent_contexts import (
    day_discussion_context,
    vote_context,
    werewolf_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _speak_action(message: str, ctx) -> AgentAction:
    """构造一条带指定 public_message 的 SPEAK action。"""
    return AgentAction(
        game_id=ctx.game_id,
        agent_id=ctx.agent_id,
        role=ctx.role,
        phase=ctx.phase,
        action_type=ActionType.SPEAK,
        public_message=message,
    )


def _vote_action(target: str, ctx) -> AgentAction:
    return AgentAction(
        game_id=ctx.game_id,
        agent_id=ctx.agent_id,
        role=ctx.role,
        phase=ctx.phase,
        action_type=ActionType.VOTE,
        target=target,
    )


# ---------------------------------------------------------------------------
# No-op：clean action 透传
# ---------------------------------------------------------------------------


class TestNoOp:
    def setup_method(self):
        self.canon = ActionCanonicalizer()

    def test_clean_speak_passes_through(self):
        ctx = day_discussion_context()
        action = _speak_action("I think P3 is suspicious.", ctx)
        result = self.canon.canonicalize(action, ctx)
        assert result.public_message == "I think P3 is suspicious."
        assert "canonicalized" not in result.metadata

    def test_action_with_no_message_passes_through(self):
        # NIGHT_KILL_NOMINATE 通常没 public_message
        ctx = werewolf_context()
        action = AgentAction(
            game_id=ctx.game_id,
            agent_id=ctx.agent_id,
            role=ctx.role,
            phase=ctx.phase,
            action_type=ActionType.NIGHT_KILL_NOMINATE,
            target="P2",
        )
        result = self.canon.canonicalize(action, ctx)
        assert result is action  # 透传同一个对象

    def test_empty_message_passes_through(self):
        ctx = day_discussion_context()
        action = _speak_action("", ctx)
        result = self.canon.canonicalize(action, ctx)
        assert "canonicalized" not in result.metadata

    def test_neutral_phrase_passes_through(self):
        """常见合法发言不能误报。"""
        ctx = day_discussion_context()
        for msg in [
            "I am the seer, I checked P4 and they are a werewolf.",
            "I vote against P3 because they were too quiet.",
            "Let me know what you think about P5.",  # "let me know" 不是 "let me think"
            "我觉得 P3 比较可疑。",
            "我是预言家，昨晚查验 P4 是狼人。",
        ]:
            action = _speak_action(msg, ctx)
            result = self.canon.canonicalize(action, ctx)
            assert "canonicalized" not in result.metadata, f"误报: {msg!r}"


# ---------------------------------------------------------------------------
# META_AI 扫描
# ---------------------------------------------------------------------------


class TestMetaAIScan:
    def setup_method(self):
        self.canon = ActionCanonicalizer()
        self.ctx = day_discussion_context()

    @pytest.mark.parametrize(
        "message",
        [
            "As an AI, I think P3 is suspicious.",
            "As a language model, my analysis suggests P3.",
            "As a large language model, I cannot have opinions.",
            "I'm an AI and I think P3.",
            "I am an AI assistant.",
            "I'm a language model.",
            "As GPT, I'd recommend voting P3.",
            "ChatGPT says P3 is the wolf.",
            "Claude tells me P4 might be the seer.",
            "As a model, I think...",
        ],
    )
    def test_english_meta_ai_triggers(self, message):
        action = _speak_action(message, self.ctx)
        result = self.canon.canonicalize(action, self.ctx)
        assert result.metadata["canonicalized"] is True
        assert "meta_ai" in result.metadata["canonicalize_triggered"]

    @pytest.mark.parametrize(
        "message",
        [
            "作为一个AI，我认为 P3 可疑。",
            "作为一个人工智能，我不能有偏见。",
            "作为一个语言模型，我建议投票 P3。",
            "作为AI，我的分析...",
            "作为大语言模型，我会...",
            "我是一个AI，我觉得 P3 是狼。",
            "我是一个人工智能助手。",
            "我是AI，无法做决策。",
        ],
    )
    def test_chinese_meta_ai_triggers(self, message):
        action = _speak_action(message, self.ctx)
        result = self.canon.canonicalize(action, self.ctx)
        assert result.metadata["canonicalized"] is True
        assert "meta_ai" in result.metadata["canonicalize_triggered"]


# ---------------------------------------------------------------------------
# COT_LEAK 扫描
# ---------------------------------------------------------------------------


class TestCOTLeakScan:
    def setup_method(self):
        self.canon = ActionCanonicalizer()
        self.ctx = day_discussion_context()

    @pytest.mark.parametrize(
        "message",
        [
            "Let me think about who might be the wolf...",
            "Let me reason through this carefully.",
            "Let me analyze the votes from yesterday.",
            "Let's think step by step about P3.",
            "Thinking step by step about the situation.",
            "Step-by-step, I'll consider each player.",
            "step by step, my conclusion is P3.",
            "My reasoning is that P3 voted for P4.",
            "My thought process here is...",
            "My chain of thought tells me P3.",
            "Chain-of-thought: P3 is suspicious because...",
        ],
    )
    def test_english_cot_leak_triggers(self, message):
        action = _speak_action(message, self.ctx)
        result = self.canon.canonicalize(action, self.ctx)
        assert result.metadata["canonicalized"] is True
        assert "cot_leak" in result.metadata["canonicalize_triggered"]

    @pytest.mark.parametrize(
        "message",
        [
            "我的思考过程是...",
            "我的思维链显示 P3 是狼。",
        ],
    )
    def test_chinese_cot_leak_triggers(self, message):
        action = _speak_action(message, self.ctx)
        result = self.canon.canonicalize(action, self.ctx)
        assert result.metadata["canonicalized"] is True
        assert "cot_leak" in result.metadata["canonicalize_triggered"]

    @pytest.mark.parametrize(
        "message",
        [
            # 这些是正常真人发言，措辞虽含"分析/思路/推理"但不是元信息泄漏，不应被拦。
            "让我想想 P3 是不是狼。",
            "让我分析一下投票模式。",
            "我的思路是先排除 P1。",
            "我的推理是 P3 太安静了。",
            "思考一下，P3 似乎更可疑。",
            "逐步分析昨晚的发言。",
            "一步一步来，先看 P3。",
        ],
    )
    def test_normal_speech_not_flagged_as_cot_leak(self, message):
        action = _speak_action(message, self.ctx)
        result = self.canon.canonicalize(action, self.ctx)
        assert "canonicalized" not in result.metadata
        assert result.public_message == message


# ---------------------------------------------------------------------------
# Sanitize 行为
# ---------------------------------------------------------------------------


class TestSanitizeBehavior:
    def setup_method(self):
        self.canon = ActionCanonicalizer()

    def test_speak_message_replaced_with_placeholder(self):
        ctx = day_discussion_context()
        action = _speak_action("As an AI, I vote P3.", ctx)
        result = self.canon.canonicalize(action, ctx)
        assert result.public_message == ActionCanonicalizer.SANITIZED_MESSAGE
        assert result.action_type == ActionType.SPEAK  # 类型不变

    def test_non_speak_action_with_leaky_message_clears_message(self):
        # VOTE action 不应该有 public_message 但万一有 + 违规，清掉
        ctx = vote_context()
        action = AgentAction(
            game_id=ctx.game_id,
            agent_id=ctx.agent_id,
            role=ctx.role,
            phase=ctx.phase,
            action_type=ActionType.VOTE,
            target="P3",
            public_message="As an AI, I vote P3.",
        )
        result = self.canon.canonicalize(action, ctx)
        assert result.public_message is None
        assert result.target == "P3"  # 其他字段不变
        assert result.action_type == ActionType.VOTE

    def test_multiple_violations_both_recorded(self):
        ctx = day_discussion_context()
        msg = "As an AI, let me think step by step about P3."
        action = _speak_action(msg, ctx)
        result = self.canon.canonicalize(action, ctx)
        triggered = result.metadata["canonicalize_triggered"]
        assert "meta_ai" in triggered
        assert "cot_leak" in triggered

    def test_original_message_preserved_in_metadata(self):
        ctx = day_discussion_context()
        msg = "As an AI, I think P3 is suspicious."
        action = _speak_action(msg, ctx)
        result = self.canon.canonicalize(action, ctx)
        assert result.metadata["canonicalize_original_message"] == msg


# ---------------------------------------------------------------------------
# CanonicalizationError 路径
# ---------------------------------------------------------------------------


class TestCanonicalizationError:
    def setup_method(self):
        self.canon = ActionCanonicalizer()

    def test_speak_with_leak_but_speak_not_allowed_raises(self):
        # 构造一个 SPEAK action，但 context.allowed_actions 不含 SPEAK
        # 这本是 RuleValidator 该拒的状态；canonicalizer 兜底显式抛
        ctx = vote_context()  # allowed_actions = [VOTE]
        action = _speak_action("As an AI, I think P3.", ctx)
        with pytest.raises(CanonicalizationError) as exc_info:
            self.canon.canonicalize(action, ctx)
        assert "meta_ai" in exc_info.value.triggered
        assert exc_info.value.original_message == "As an AI, I think P3."


# ---------------------------------------------------------------------------
# ROLE_LEAK 当前是空集 —— 验证不误报
# ---------------------------------------------------------------------------


class TestRoleLeakNotImplementedYet:
    """ROLE_LEAK 词表会议待定，第一版必须不误报合法的自报角色。"""

    def setup_method(self):
        self.canon = ActionCanonicalizer()

    @pytest.mark.parametrize(
        "self_claim",
        [
            "I am the seer.",
            "I am the witch, I saved P3 last night.",
            "I claim seer.",
            "我是预言家。",
            "我是女巫，昨晚救了 P3。",
            "我跳预言家。",
        ],
    )
    def test_self_claim_role_not_flagged(self, self_claim):
        ctx = day_discussion_context()
        action = _speak_action(self_claim, ctx)
        result = self.canon.canonicalize(action, ctx)
        # ROLE_LEAK 暂不扫描，自报角色合法
        assert "canonicalized" not in result.metadata, (
            f"误报合法自报: {self_claim!r}"
        )


# ---------------------------------------------------------------------------
# 与 Parser 输出的端到端兼容
# ---------------------------------------------------------------------------


class TestEndToEndWithParser:
    """模拟 ActionParser → ActionCanonicalizer 串联，验证管道连通。"""

    def test_clean_parser_output_passes_canonicalizer(self):
        import json

        from agent_runtime.action_parser import ActionParser

        parser = ActionParser()
        canon = ActionCanonicalizer()
        ctx = day_discussion_context()

        raw = json.dumps(
            {
                "action_type": "speak",
                "public_message": "I think P3 voted suspiciously yesterday.",
            }
        )
        parsed = parser.parse(raw, ctx)
        final = canon.canonicalize(parsed, ctx)
        assert final.public_message == (
            "I think P3 voted suspiciously yesterday."
        )

    def test_leaky_parser_output_sanitized_by_canonicalizer(self):
        import json

        from agent_runtime.action_parser import ActionParser

        parser = ActionParser()
        canon = ActionCanonicalizer()
        ctx = day_discussion_context()

        raw = json.dumps(
            {
                "action_type": "speak",
                "public_message": "As an AI, let me think step by step about P3.",
            }
        )
        parsed = parser.parse(raw, ctx)
        final = canon.canonicalize(parsed, ctx)
        assert final.public_message == ActionCanonicalizer.SANITIZED_MESSAGE
        assert final.metadata["canonicalized"] is True
