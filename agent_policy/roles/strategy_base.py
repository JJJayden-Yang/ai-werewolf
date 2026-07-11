"""角色策略基类。

这里只放角色策略统一接口、phase 分发和最保守的合法动作兜底。
具体角色的 mock 策略应放在各自的 `agent_policy.roles.*` 文件里。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from contracts import ActionType, AgentAction, AgentContext, Phase

from agent_policy.actions import build_hunter_shoot_action, build_skip_action, build_speak_action, build_vote_action
from agent_policy.target_selectors import select_alive_non_self


class RoleStrategy(ABC):
    """角色策略统一接口。"""

    @abstractmethod
    def decide(self, context: AgentContext) -> AgentAction:
        """根据 AgentContext 生成一个标准 AgentAction。"""


class BaseRuleBasedStrategy(RoleStrategy):
    """不调用 LLM 的规则策略基类。

    本类负责把 `AgentContext.phase` 分发到对应 hook。
    各角色文件覆写 hook 来承载真正的 mock 策略。
    """

    def decide(self, context: AgentContext) -> AgentAction:
        if context.phase == Phase.NIGHT_WEREWOLF:
            return self.decide_werewolf_night(context)
        if context.phase == Phase.NIGHT_SEER:
            return self.decide_seer_night(context)
        if context.phase == Phase.NIGHT_WITCH:
            return self.decide_witch_night(context)
        if context.phase in {Phase.DAY_DISCUSSION, Phase.DAY_TIE_DISCUSSION}:
            return self.decide_speech(context)
        if context.phase == Phase.DAY_VOTE:
            return self.decide_vote(context)
        if context.phase == Phase.DAY_TIE_REVOTE:
            return self.decide_tie_revote(context)
        if context.phase == Phase.HUNTER_SHOOT:
            return self.decide_hunter_shoot(context)
        if context.phase == Phase.EXILE_LAST_WORDS:
            return self.decide_last_words(context)
        return self.decide_fallback(context)

    def decide_werewolf_night(self, context: AgentContext) -> AgentAction:
        return self.decide_fallback(context)

    def decide_seer_night(self, context: AgentContext) -> AgentAction:
        return self.decide_fallback(context)

    def decide_witch_night(self, context: AgentContext) -> AgentAction:
        return self.decide_fallback(context)

    def decide_speech(self, context: AgentContext) -> AgentAction:
        return self.decide_fallback(context)

    def decide_vote(self, context: AgentContext) -> AgentAction:
        return self.decide_fallback(context)

    def decide_tie_revote(self, context: AgentContext) -> AgentAction:
        return self.decide_fallback(context)

    def decide_hunter_shoot(self, context: AgentContext) -> AgentAction:
        return build_hunter_shoot_action(
            context,
            None,
            reason_summary="规则策略骨架默认猎人不开枪。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "hunter_pass"},
        )

    def decide_last_words(self, context: AgentContext) -> AgentAction:
        return self.decide_fallback(context)

    def decide_fallback(self, context: AgentContext) -> AgentAction:
        if ActionType.SKIP in context.allowed_actions:
            return build_skip_action(
                context,
                metadata={"strategy": self.__class__.__name__, "selected_by": "fallback_skip"},
            )
        if ActionType.VOTE in context.allowed_actions:
            target = select_alive_non_self(context)
            if target:
                return build_vote_action(
                    context,
                    target,
                    reason_summary="兜底选择一个存活且非自己的投票目标。",
                    metadata={"strategy": self.__class__.__name__, "selected_by": "fallback_vote"},
                )
        if ActionType.HUNTER_SHOOT in context.allowed_actions:
            return build_hunter_shoot_action(
                context,
                None,
                reason_summary="兜底选择猎人不开枪。",
                metadata={"strategy": self.__class__.__name__, "selected_by": "fallback_hunter_pass"},
            )
        return build_speak_action(
            context,
            "我暂时没有更多信息。",
            metadata={"strategy": self.__class__.__name__, "selected_by": "fallback_speech"},
        )
