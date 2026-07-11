"""ActionCanonicalizer —— Task C3。安全闸门。

Engine 永远只接收 8 个标准 ``ActionType``。Alias 映射在 ``ActionParser`` 完成，
Canonicalizer 输入已是合法 ``AgentAction``；本类**只负责 message 内容层的清洗**：

1. **META_AI 扫描** —— 拒绝 "as an AI / 作为一个 AI / 大语言模型" 类元话术。
2. **COT_LEAK 扫描** —— 拒绝 "let me think step by step / 我的推理过程" 类思维链泄漏。
3. **ROLE_LEAK 扫描** —— 拒绝暴露系统视角身份的话（如"系统告诉我..."）。

命中任意一类 → 用占位 sanitize message 替换原 ``public_message``，
``metadata.canonicalized=True``、``metadata.canonicalize_triggered=[...]``，
**不抛错**（保证管道继续走，落盘 ``action_canonicalized`` / ``action_guard_triggered``
事件供 Trace 复盘）。

仅在 sanitize 自身失败（如 SPEAK 不在 ``allowed_actions`` 时无法清洗）才抛
``CanonicalizationError``，让上层走 ``FallbackPolicy``。

设计原则：

- **不做 alias 映射** —— 那是 Parser 的活。Parser 输入已通过 pydantic 验证，
  ``action_type`` 必然是 8 个标准之一。
- **不做语义合法性校验** —— "vote 在当前 phase 合法吗" 是 RuleValidator 的活。
- **不做角色合法性校验** —— "狼人能不能投票" 是 RuleValidator 的活。

词表说明（第一版，会议待对齐）：

- ``META_AI_PATTERNS`` / ``COT_LEAK_PATTERNS`` 给出最小可用占位集（中英文）。
- ``ROLE_LEAK_PATTERNS`` 先**空集** —— 玩家自报角色（"I am the seer"）是合法
  策略动作，跟"系统告诉我我是预言家"语义难区分。词表需要会议讨论后再填。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from contracts import ActionType, AgentAction

from agent_runtime.exceptions import CanonicalizationError

if TYPE_CHECKING:
    from contracts import AgentContext


# ---------------------------------------------------------------------------
# 扫描词表（第一版；TODO 会议讨论后扩充）
# ---------------------------------------------------------------------------

# 用正则的小写形式 + re.IGNORECASE 匹配。多语言短语放在一起便于维护。
META_AI_PATTERNS: tuple[str, ...] = (
    # 英文
    r"\bas an? (?:ai|llm|language model|large language model)\b",
    r"\bi['’]m an? (?:ai|llm|language model)\b",
    r"\bi am an? (?:ai|llm|language model)\b",
    r"\b(?:gpt|chatgpt|claude|gemini|llama)\b",
    r"\bas a model\b",
    # 中文
    r"作为一个?(?:ai|人工智能|语言模型|大语言模型|大模型|聊天机器人)",
    r"作为(?:ai|人工智能|语言模型|大语言模型|大模型)",
    r"我是一个?(?:ai|人工智能|语言模型|大语言模型|聊天机器人)",
    r"我是(?:ai|gpt|claude)",
)

COT_LEAK_PATTERNS: tuple[str, ...] = (
    # 英文
    r"\blet'?s think step by step\b",
    r"\bthinking step by step\b",
    r"\blet me (?:think|reason|analyze)\b",
    r"\bstep[- ]by[- ]step\b",
    r"\bmy (?:reasoning|thought process|chain of thought)\b",
    r"\bchain[- ]of[- ]thought\b",
    # 中文：只拦真·元信息泄漏（思维链 / 思考过程 等机器味措辞）。
    # 「让我分析一下」「我的思路是」「逐步分析」「思考一下」是正常真人发言，不拦，避免误伤。
    r"思维链",
    r"思考过程",
)

# TODO（会议讨论）：ROLE_LEAK 词表
# 难点：玩家"自报角色"是合法策略动作 vs "系统告诉我我是 X"是泄漏 —— 语义难区分。
# 第一版留空 + 接口，会议讨论后扩。
ROLE_LEAK_PATTERNS: tuple[str, ...] = ()


def _compile_patterns(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_META_AI_RE = _compile_patterns(META_AI_PATTERNS)
_COT_LEAK_RE = _compile_patterns(COT_LEAK_PATTERNS)
_ROLE_LEAK_RE = _compile_patterns(ROLE_LEAK_PATTERNS)


# ---------------------------------------------------------------------------
# Canonicalizer
# ---------------------------------------------------------------------------


class ActionCanonicalizer:
    """合法 ``AgentAction`` 的 message 层安全闸门。

    无状态：可以共享一个实例给所有 Agent。
    """

    SANITIZED_MESSAGE = "这点我先不展开，听听大家怎么看。"

    def canonicalize(
        self, action: AgentAction, context: AgentContext
    ) -> AgentAction:
        """扫描 ``action.public_message`` 三类违规并 sanitize。

        Args:
            action: 已通过 ``ActionParser`` + pydantic 验证的合法 AgentAction。
            context: 当前 AgentContext（用于 SPEAK 是否 allowed 判断）。

        Returns:
            合法 AgentAction。可能 message 被替换、metadata 加 flag。

        Raises:
            CanonicalizationError: 命中扫描但无法 sanitize（罕见，如 SPEAK 不允许
                但当前是 SPEAK action 且有违规 —— 这种情况理论上 RuleValidator 也会拒）。
        """
        triggered = self._scan(action.public_message)
        if not triggered:
            return action

        return self._sanitize(action, triggered, context)

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _scan(message: str | None) -> list[str]:
        """返回命中的违规类别列表（["meta_ai", "cot_leak", ...]）。"""
        if not message:
            return []

        triggered: list[str] = []
        if any(p.search(message) for p in _META_AI_RE):
            triggered.append("meta_ai")
        if any(p.search(message) for p in _COT_LEAK_RE):
            triggered.append("cot_leak")
        if _ROLE_LEAK_RE and any(p.search(message) for p in _ROLE_LEAK_RE):
            triggered.append("role_leak")
        return triggered

    def _sanitize(
        self,
        action: AgentAction,
        triggered: list[str],
        context: AgentContext,
    ) -> AgentAction:
        """生成 sanitize 后的 AgentAction。"""
        # SPEAK action：用占位替换 message。
        if action.action_type == ActionType.SPEAK:
            if ActionType.SPEAK not in context.allowed_actions:
                # 极罕见：SPEAK action 但当前 phase 不允许 SPEAK
                # —— RuleValidator 也会拒；这里显式抛让上层走 fallback。
                raise CanonicalizationError(
                    f"SPEAK action but SPEAK not in allowed_actions for phase "
                    f"{context.phase.value}",
                    triggered=",".join(triggered),
                    original_message=action.public_message,
                )
            new_message: str | None = self.SANITIZED_MESSAGE
        else:
            # 非 SPEAK action：清空 message（NIGHT_KILL_NOMINATE 等不应有 message）
            new_message = None

        new_metadata = dict(action.metadata)
        new_metadata["canonicalized"] = True
        new_metadata["canonicalize_triggered"] = list(triggered)
        new_metadata["canonicalize_original_message"] = action.public_message

        return action.model_copy(
            update={
                "public_message": new_message,
                "metadata": new_metadata,
            }
        )
