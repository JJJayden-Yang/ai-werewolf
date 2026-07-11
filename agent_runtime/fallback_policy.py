"""FallbackPolicy —— Task C4。

任何阶段的 LLM/解析/规范化/校验失败，最终都必须落到一个**合法的** ``AgentAction``，
绝不抛错穿透到 Engine。本模块是 Agent Runtime 的"最终安全网"。

Fallback 表（见 Interface_v2_1 第 5.8 节）：

| Phase                  | 角色             | Fallback                                |
|------------------------|------------------|-----------------------------------------|
| NIGHT_WEREWOLF         | 狼人             | 提名第一个存活非自己玩家                |
| NIGHT_SEER             | 预言家           | 查第一个存活非自己玩家                  |
| NIGHT_WITCH            | 女巫             | skip                                    |
| HUNTER_SHOOT           | 猎人             | pass（target=None, metadata.pass=True） |
| DAY_DISCUSSION         | 所有存活玩家     | 中性发言                                |
| DAY_TIE_DISCUSSION     | 所有存活玩家     | 中性发言                                |
| DAY_VOTE               | 所有存活玩家     | 投第一个存活非自己玩家                  |
| DAY_TIE_REVOTE         | 所有存活玩家     | 只在 tie_candidates 中选第一个非自己    |
| EXILE_LAST_WORDS       | 被放逐玩家       | 默认遗言                                |

实现红线（与 b_c_responsibility_boundary §2.2 / Interface_v2_1 §5.8 一致）：

- **不读** ``TruthState``。
- 只读 ``AgentContext`` 暴露的 ``allowed_actions / visible_players / tie_candidates /
  private_events / rule_hints / agent_id / role / phase / game_id``。
- 输出必须可通过 ``RuleValidator``（自身不能产生非法 action）。
- ``apply`` 自身无法产生合法 action 时，抛 ``FallbackError`` 让 supervisor 显式失败，
  而不是静默返回一个仍会被 RuleValidator 拒掉的 action。

候选目标的选择（``_first_context_target``）：

1. 优先用 ``context.rule_hints["fallback_targets"]``（A 的 ContextAssembler 约定字段）。
2. 否则从 ``context.visible_players`` 取第一个 ``ALIVE`` 且 ``player_id != agent_id`` 的人。
3. 都没有 → 返回 ``None``，由上层 phase 分支决定是否能容忍。

注：默认是确定性"取第一个"，与 A 在 ``supervisor.py:_fallback_from_context`` 的临时版
行为兼容。需要随机时可在子类覆盖 ``_first_context_target``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from contracts import (
    ActionType,
    AgentAction,
    Phase,
    PlayerStatus,
)

from agent_runtime.exceptions import FallbackError

if TYPE_CHECKING:
    from contracts import AgentContext


# Phase 集合常量：分支判断集中在常量上，分支逻辑里只用 in / ==。
_PHASES_SPEAK = frozenset({Phase.DAY_DISCUSSION, Phase.DAY_TIE_DISCUSSION})

# 这些 phase 不需要 agent 决策（INIT / ROLE_ASSIGNMENT / DAY_ANNOUNCEMENT /
# EXILE_RESOLUTION / NO_EXILE_RESOLUTION / WIN_CHECK / GAME_OVER）。
# 走到 FallbackPolicy 即说明上游调用错了，必须显式失败而不是吞掉。
_PHASES_NO_AGENT_ACTION = frozenset(
    {
        Phase.INIT,
        Phase.ROLE_ASSIGNMENT,
        Phase.DAY_ANNOUNCEMENT,
        Phase.EXILE_RESOLUTION,
        Phase.NO_EXILE_RESOLUTION,
        Phase.WIN_CHECK,
        Phase.GAME_OVER,
    }
)


class FallbackPolicy(ABC):
    """Fallback 策略的抽象基类。

    任何实现都必须保证 ``apply`` 返回的 ``AgentAction`` 通过 ``RuleValidator`` 校验，
    或显式抛 ``FallbackError``。绝不允许返回一个会被下游拒掉的 action。
    """

    @abstractmethod
    def apply(
        self,
        context: AgentContext,
        error: Exception | None = None,
    ) -> AgentAction:
        """生成兜底动作。

        Args:
            context: 触发 fallback 的 AgentContext。``context.phase`` 决定走哪条分支。
            error:   触发 fallback 的原始异常（可选）。会被记到
                     ``action.metadata["fallback_reason"]``，便于事后审计。

        Raises:
            FallbackError: 当 phase 不需要 agent 决策、或 phase 需要 target 但 context
                           没暴露任何合法目标时抛出。
        """


class ContextAwareFallbackPolicy(FallbackPolicy):
    """从 ``AgentContext`` 派生合法 fallback action 的标准实现。

    每个需要 Agent 决策的 phase 都有一个分支；行为与 A 在
    ``supervisor.py:_fallback_from_context`` 的临时版兼容，并在三点上更完备：

    1. 不符合 phase（``INIT / GAME_OVER`` 等）显式 ``FallbackError``，
       而不是静默返回 SPEAK target=None。
    2. 把 ``error`` 类名 + 消息写进 ``metadata.fallback_reason / fallback_message``，
       trace 时能追到是哪一类失败触发。
    3. 完整覆盖 ``DAY_TIE_DISCUSSION`` 和 ``EXILE_LAST_WORDS``。
    """

    DEFAULT_DAY_DISCUSSION_MESSAGE = (
        "I will stay cautious and listen to more information."
    )
    DEFAULT_EXILE_LAST_WORDS = (
        "These are my last words. Please review the votes carefully."
    )

    # --- public API ----------------------------------------------------------

    def apply(
        self,
        context: AgentContext,
        error: Exception | None = None,
    ) -> AgentAction:
        phase = context.phase
        if phase in _PHASES_NO_AGENT_ACTION:
            raise FallbackError(
                f"FallbackPolicy 在不需要 agent 决策的 phase 被调用: {phase.value}",
                phase=phase.value,
                role=context.role.value,
            )

        action_type, target, public_message, extra_meta = self._dispatch(context)

        metadata: dict = {"fallback_used": True, **extra_meta}
        if error is not None:
            metadata["fallback_reason"] = type(error).__name__
            metadata["fallback_message"] = str(error)[:200]  # 截断防膨胀

        return AgentAction(
            game_id=context.game_id,
            agent_id=context.agent_id,
            role=context.role,
            phase=phase,
            action_type=action_type,
            target=target,
            public_message=public_message,
            metadata=metadata,
        )

    # --- phase dispatch ------------------------------------------------------

    def _dispatch(
        self, context: AgentContext
    ) -> tuple[ActionType, str | None, str | None, dict]:
        """根据 phase 返回 ``(action_type, target, public_message, extra_metadata)``。"""
        phase = context.phase

        if phase == Phase.NIGHT_WEREWOLF:
            target = self._require_target(context, "NIGHT_WEREWOLF needs target")
            return ActionType.NIGHT_KILL_NOMINATE, target, None, {}

        if phase == Phase.NIGHT_SEER:
            target = self._require_target(context, "NIGHT_SEER needs target")
            return ActionType.CHECK, target, None, {}

        if phase == Phase.NIGHT_WITCH:
            return ActionType.SKIP, None, None, {}

        if phase == Phase.HUNTER_SHOOT:
            # 兜底选 pass —— 比"乱射存活非自己"更安全：
            # 猎人误开枪可能直接干扰胜负条件，pass 则只是放弃技能。
            return ActionType.HUNTER_SHOOT, None, None, {"pass": True}

        if phase in _PHASES_SPEAK:
            return (
                ActionType.SPEAK,
                None,
                self.DEFAULT_DAY_DISCUSSION_MESSAGE,
                {},
            )

        if phase == Phase.DAY_VOTE:
            target = self._require_target(context, "DAY_VOTE needs target")
            return ActionType.VOTE, target, None, {}

        if phase == Phase.DAY_TIE_REVOTE:
            target = self._first_tie_candidate(context)
            if target is None:
                raise FallbackError(
                    "DAY_TIE_REVOTE 但 tie_candidates 空或全是自己",
                    phase=phase.value,
                    role=context.role.value,
                )
            return ActionType.VOTE, target, None, {}

        if phase == Phase.EXILE_LAST_WORDS:
            return ActionType.SPEAK, None, self.DEFAULT_EXILE_LAST_WORDS, {}

        # 理论上 _PHASES_NO_AGENT_ACTION 已经在 apply() 里拦截了，这里是兜底保险。
        raise FallbackError(
            f"FallbackPolicy 不识别的 phase: {phase.value}",
            phase=phase.value,
            role=context.role.value,
        )

    # --- target selection ----------------------------------------------------

    def _require_target(self, context: AgentContext, reason: str) -> str:
        """选第一个合法 target；选不到抛 ``FallbackError``。"""
        target = self._first_context_target(context)
        if target is None:
            raise FallbackError(
                f"{reason}; context 中没有可用的存活非自己玩家",
                phase=context.phase.value,
                role=context.role.value,
            )
        return target

    @staticmethod
    def _first_context_target(context: AgentContext) -> str | None:
        """与 A 的 ``supervisor._first_context_target`` 行为兼容。

        1. 优先 ``context.rule_hints["fallback_targets"]``（list[str]）。
        2. 否则取 ``visible_players`` 第一个 ALIVE 且非 self。
        """
        hinted = context.rule_hints.get("fallback_targets")
        if isinstance(hinted, list):
            for pid in hinted:
                if isinstance(pid, str) and pid != context.agent_id:
                    return pid
        for player in context.visible_players:
            if (
                player.player_id != context.agent_id
                and player.status == PlayerStatus.ALIVE
            ):
                return player.player_id
        return None

    @staticmethod
    def _first_tie_candidate(context: AgentContext) -> str | None:
        """从 ``context.tie_candidates`` 取第一个非自己的玩家。"""
        for pid in context.tie_candidates:
            if pid != context.agent_id:
                return pid
        return None
