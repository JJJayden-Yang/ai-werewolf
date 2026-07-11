"""ActionParser —— Task C2。

把 LLM 的原文输出解析为合法 ``AgentAction``。解析失败时抛 ``ParseError``，
由上层 RetryPolicy 决定重试，重试耗尽走 FallbackPolicy 兜底 —— 绝不允许
脏数据穿透到 RuleValidator / Engine。

LLM 输出预期 schema（C 单方面定义，B 写 prompt 时按此规范）::

    {
      "action_type": "vote",          # 标准 8 个 ActionType 之一，或下表 alias
      "target": "P3",                  # 可选
      "public_message": "...",         # 可选
      "role_claim": "seer",            # 可选；speak 时声明角色
      "claim_result": {                # 可选；预言家自报查验结果
        "target": "P4",
        "claimed_alignment": "werewolf"
      },
      "reason_summary": "..."          # 可选；决策摘要，进 trace 不进 prompt
    }

Identity 字段（``game_id / agent_id / role / phase``）由 ActionParser 从
``AgentContext`` 自动补齐 —— **LLM 不应决定这些**。即使 LLM 输出了，
也会被 context 的值覆盖（防 prompt injection 把动作路由到别的 game / agent）。

Alias hard mapping（见 Interface_v2_1 §5.6）::

    多语言/口语 raw action_type    → 标准 ActionType.value
    "kill" / "wolf_kill"            → "night_kill_nominate"
    "inspect" / "verify" / "see"    → "check"
    "shoot" / "fire" / "revenge"    → "hunter_shoot"
    "pass_shoot"                    → "hunter_shoot" + target=None + metadata.pass=True
    "jump_claim" / "claim_seer" ... → "speak"
    "defend" / "accuse" / "argue"   → "speak"
    "talk" / "say" / "discuss"      → "speak"

未匹配 alias 且非标准 ActionType 的输入 → 由 pydantic enum 验证抛错 →
转 ParseError 由上层走 FallbackPolicy。

设计原则：本类**不做语义校验**（"vote 在当前 phase 合法吗"是 RuleValidator 的事），
只负责"LLM 字符串 → 合法 AgentAction 对象"的语法层转换。
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from contracts import AgentAction

from agent_runtime.exceptions import ParseError

if TYPE_CHECKING:
    from contracts import AgentContext


# Alias 映射表 —— key 全小写，值为标准 ActionType.value。
# 修改时同步 docstring 上面的对照表。
_ACTION_ALIAS: dict[str, str] = {
    # NIGHT_WEREWOLF
    "kill": "night_kill_nominate",
    "wolf_kill": "night_kill_nominate",
    "night_kill": "night_kill_nominate",
    "nominate": "night_kill_nominate",
    # NIGHT_SEER
    "inspect": "check",
    "verify": "check",
    "see": "check",
    "investigate": "check",
    # HUNTER_SHOOT
    "shoot": "hunter_shoot",
    "fire": "hunter_shoot",
    "revenge": "hunter_shoot",
    "pass_shoot": "hunter_shoot",  # 特殊：target 强制 None + metadata.pass=True
    # SPEAK 同义词（任意阶段 SPEAK allowed 时可走）
    "jump_claim": "speak",
    "claim_seer": "speak",
    "claim_witch": "speak",
    "claim_hunter": "speak",
    "claim_villager": "speak",
    "defend": "speak",
    "accuse": "speak",
    "argue": "speak",
    "quarrel": "speak",
    "talk": "speak",
    "say": "speak",
    "discuss": "speak",
}


# 匹配 ```json ... ``` 或 ```... ``` 代码块；非贪婪匹配第一个完整块。
_MARKDOWN_FENCE_RE = re.compile(
    r"```(?:json)?\s*(.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)


class ActionParser:
    """从 LLM 原文构造合法 ``AgentAction``。

    无状态：可以共享一个实例给所有 Agent。线程安全（无属性修改）。
    """

    def parse(self, raw_llm_output: str, context: AgentContext) -> AgentAction:
        """解析 LLM 原文 → AgentAction。

        Args:
            raw_llm_output: LLM 的原文输出（可能裹 markdown 代码块）。
            context: 当前 AgentContext，用于补齐 identity 字段。

        Returns:
            通过 pydantic 验证的 AgentAction。

        Raises:
            ParseError: 任何环节失败都抛此异常，``reason`` 字段标明失败类型：
                - ``empty_input`` —— 输入空字符串
                - ``json_decode`` —— JSON 解析失败
                - ``not_object`` —— JSON 解析出来不是对象
                - ``missing_action_type`` —— payload 缺 action_type 字段
                - ``pydantic_validation`` —— 拼装后 pydantic 验证失败
                  （含 unknown action_type / 缺必填字段 / 类型错误等）
        """
        payload = self._extract_json(raw_llm_output)
        payload = self._normalize_action_type(payload)
        payload = self._apply_identity_from_context(payload, context)

        try:
            return AgentAction.model_validate(payload)
        except Exception as e:  # pydantic.ValidationError 等
            raise ParseError(
                f"AgentAction validation failed: {e}",
                raw=raw_llm_output,
                reason="pydantic_validation",
            ) from e

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        """剥离 markdown 后 ``json.loads``，必须得到 dict。"""
        if not raw or not raw.strip():
            raise ParseError(
                "LLM raw output is empty",
                raw=raw,
                reason="empty_input",
            )

        text = raw.strip()
        # 1. 尝试 markdown 代码块剥离
        match = _MARKDOWN_FENCE_RE.search(text)
        if match:
            text = match.group(1).strip()

        # 2. json.loads
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            raise ParseError(
                f"JSON decode failed: {e.msg}",
                raw=raw,
                reason="json_decode",
            ) from e

        if not isinstance(payload, dict):
            raise ParseError(
                f"expected JSON object, got {type(payload).__name__}",
                raw=raw,
                reason="not_object",
            )

        return payload

    @staticmethod
    def _normalize_action_type(payload: dict[str, Any]) -> dict[str, Any]:
        """alias → 标准 ActionType.value；触发 pass_shoot 的特殊处理。"""
        action_type = payload.get("action_type")
        if not isinstance(action_type, str):
            raise ParseError(
                f"missing or invalid action_type: {action_type!r}",
                reason="missing_action_type",
            )

        normalized = action_type.strip().lower()
        is_pass_shoot = normalized == "pass_shoot"

        mapped = _ACTION_ALIAS.get(normalized, normalized)
        payload["action_type"] = mapped

        if is_pass_shoot:
            payload["target"] = None
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["pass"] = True
            payload["metadata"] = metadata

        return payload

    @staticmethod
    def _apply_identity_from_context(
        payload: dict[str, Any], context: AgentContext
    ) -> dict[str, Any]:
        """强制用 context 的 identity 字段覆盖 LLM 输出。

        防 prompt injection 让 LLM 把动作路由到别的 game / 假冒别的 agent。
        """
        payload["game_id"] = context.game_id
        payload["agent_id"] = context.agent_id
        payload["role"] = context.role.value
        payload["phase"] = context.phase.value
        return payload
