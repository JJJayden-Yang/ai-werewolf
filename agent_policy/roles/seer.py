"""预言家角色策略。

Owner：C。B 负责接口维护和合并协调。

策略要点：
- 夜晚优先查验未查验、存活、非自己的玩家。
- 白天若已查到存活狼人 → 6 人 D1 立刻跳；**9 人 D1 默认不跳**（避免秒死）、
  D2+ 必跳。**被悍跳时**（公开有人 claim Seer 且非自己）任何天都对跳查杀。
- 白天无狼但 D2+ 有金水 → 跳明并报金水。
- 投票优先投自己查到的存活狼人；其次投公开查杀目标。
- 遗言：跳身份 + 报已查狼人/金水 + 列漏验 + 推荐投向。
- 不假装查验过未查验目标；不引用未查验玩家身份。

9 人 / 6 人模式由 ``visible_players`` 数量自动切换 —— C 在 ContextAssembler
就不变；A/B 喂进来的 player_count 决定行为差异。9 人调整呼应 A 5/25 18:54
分配 "claim 时机 / 查杀披露 / 被悍跳应对，不要过早暴露导致秒死，也不要永远不跳"。
"""

from __future__ import annotations

from contracts import (
    AgentAction,
    AgentContext,
    ClaimedAlignment,
    ClaimResult,
    EventType,
    Role,
)

from agent_policy.actions import build_check_action, build_speak_action, build_vote_action
from agent_policy.roles.strategy_base import BaseRuleBasedStrategy
from agent_policy.target_selectors import (
    alive_players,
    select_alive_non_self,
    select_public_werewolf_claim_target,
    select_tie_candidate,
    select_unchecked_player,
)


# ---- 内部 helper：读取自己的查验历史 ----
# 这些 helper 是预言家专属逻辑，按 phase2_5_role_development.md §2 的协作约束，
# 公共 selector 需要先和 B 对齐。这里先放本文件内私有使用，未来若女巫等
# 角色也需要"读自己的私密事件提取已知信息"，再升级到 target_selectors。


def _known_check_results(context: AgentContext) -> list[tuple[str, str]]:
    """从自己的 private_events 提取查验历史 (target, alignment_value) 列表。

    alignment_value 是 ClaimedAlignment 的字符串值（"werewolf" / "villager" / "unknown"）。
    保留发现顺序，最早查的在前。
    """
    results: list[tuple[str, str]] = []
    for event in context.private_events:
        if (
            event.event_type == EventType.SEER_CHECK_RESULT
            and event.target
            and event.result
        ):
            results.append((event.target, event.result))
    return results


def _known_werewolves(context: AgentContext, *, alive_only: bool = True) -> list[str]:
    """从查验历史挑出狼人 target_id 列表（保留发现顺序，去重）。"""
    alive_ids = {p.player_id for p in alive_players(context)} if alive_only else None
    out: list[str] = []
    for target, result in _known_check_results(context):
        if result != ClaimedAlignment.WEREWOLF.value:
            continue
        if alive_ids is not None and target not in alive_ids:
            continue
        if target not in out:
            out.append(target)
    return out


def _known_villager_aligns(context: AgentContext, *, alive_only: bool = True) -> list[str]:
    """从查验历史挑出金水 target_id 列表（保留发现顺序，去重）。"""
    alive_ids = {p.player_id for p in alive_players(context)} if alive_only else None
    out: list[str] = []
    for target, result in _known_check_results(context):
        if result != ClaimedAlignment.VILLAGER.value:
            continue
        if alive_ids is not None and target not in alive_ids:
            continue
        if target not in out:
            out.append(target)
    return out


def _unchecked_alive_ids(context: AgentContext) -> list[str]:
    """所有未查验、存活、非自己的玩家 id（保留 visible_players 顺序）。"""
    checked = {target for target, _ in _known_check_results(context)}
    return [
        p.player_id
        for p in alive_players(context)
        if p.player_id != context.agent_id and p.player_id not in checked
    ]


def _is_9p_mode(context: AgentContext) -> bool:
    """9 人 / 6 人模式分支判定。

    ``visible_players`` 暴露的是 *全部* 玩家（含死亡），所以等同于开局玩家数。
    阈值 ≥9 启用 9 人收紧策略（D1 不主动跳、对悍跳必跳）；6 人保持原行为。
    """
    return len(context.visible_players) >= 9


def _has_public_seer_claim_by_others(context: AgentContext) -> bool:
    """公开事件流里是否有 *非自己* 的玩家 self_claim Seer（不论真假）。

    触发条件：检测到悍跳/对跳，本预言家任何天都应跳明应对，不能让狼牌染色。
    """
    for ev in context.public_events:
        if ev.event_type != EventType.SPEECH:
            continue
        if ev.role_claim != Role.SEER:
            continue
        if ev.actor and ev.actor != context.agent_id:
            return True
    return False


# ---- 策略主体 ----


class SeerStrategy(BaseRuleBasedStrategy):
    """预言家策略：查验、跳明、查杀、投票引导、遗言披露。"""

    def decide_seer_night(self, context: AgentContext) -> AgentAction:
        target = select_unchecked_player(context)
        if target:
            return build_check_action(
                context,
                target,
                reason_summary="预言家优先查验未查验的存活玩家。",
                metadata={
                    "strategy": self.__class__.__name__,
                    "selected_by": "unchecked_player",
                },
            )
        return self.decide_fallback(context)

    def decide_speech(self, context: AgentContext) -> AgentAction:
        werewolves = _known_werewolves(context, alive_only=True)
        villagers = _known_villager_aligns(context, alive_only=True)
        is_9p = _is_9p_mode(context)
        has_other_seer_claim = _has_public_seer_claim_by_others(context)

        # 1) 被悍跳/对跳：任何天都对跳查杀。优先报狼；只有金水也得跳防被染色
        if has_other_seer_claim and werewolves:
            target = werewolves[0]
            message = (
                f"我才是预言家，对面是悍跳。"
                f"我昨晚查验 {target} 是狼人，请大家投出 {target}。"
            )
            return self._speak_with_check_claim(
                context,
                message,
                target=target,
                alignment=ClaimedAlignment.WEREWOLF,
                reason_summary="预言家对悍跳：跳明并公开查杀。",
                selected_by="seer_counter_claim_with_werewolf_kill",
            )
        if has_other_seer_claim and villagers:
            target = villagers[0]
            message = (
                f"我才是预言家，对面是悍跳。"
                f"我查验过 {target} 是好人，请大家先信我。"
            )
            return self._speak_with_check_claim(
                context,
                message,
                target=target,
                alignment=ClaimedAlignment.VILLAGER,
                reason_summary="预言家对悍跳：跳明并报金水兜底。",
                selected_by="seer_counter_claim_with_villager_align",
            )

        # 2) 9 人 D1 默认不跳：避免秒死，保留信息给 D2 决断
        if is_9p and context.round == 1:
            return build_speak_action(
                context,
                "我先按公开发言判断局面，明天再视情况给出更多视角。",
                reason_summary="9 人 D1 预言家默认不主动跳明，避免被夜里秒杀。",
                metadata={
                    "strategy": self.__class__.__name__,
                    "selected_by": "seer_9p_d1_hold",
                },
            )

        # 3) 6 人 D1+ 或 9 人 D2+：查到狼立刻跳明查杀
        if werewolves:
            target = werewolves[0]
            message = (
                f"我是预言家，昨晚查验 {target} 是狼人，"
                f"请大家集中投票出 {target}。"
            )
            return self._speak_with_check_claim(
                context,
                message,
                target=target,
                alignment=ClaimedAlignment.WEREWOLF,
                reason_summary="预言家跳明并公开查杀。",
                selected_by="seer_claim_with_werewolf_kill",
            )

        # 4) 无狼但 D2+ 有金水 → 跳明报金水
        if villagers and context.round >= 2:
            target = villagers[0]
            message = (
                f"我是预言家，到目前为止查验过 {target} 是好人，"
                f"我会继续验未验过的位置。"
            )
            return self._speak_with_check_claim(
                context,
                message,
                target=target,
                alignment=ClaimedAlignment.VILLAGER,
                reason_summary="预言家跳明并报金水。",
                selected_by="seer_claim_with_villager_align",
            )

        return build_speak_action(
            context,
            "我会结合大家的发言和投票推进视角，请按公开信息发言。",
            reason_summary="预言家信息不足时保持含蓄发言。",
            metadata={
                "strategy": self.__class__.__name__,
                "selected_by": "seer_low_info_speech",
            },
        )

    def decide_vote(self, context: AgentContext) -> AgentAction:
        werewolves = _known_werewolves(context, alive_only=True)
        if werewolves:
            target = werewolves[0]
            return build_vote_action(
                context,
                target,
                reason_summary="预言家投自己查验确认的狼人。",
                metadata={
                    "strategy": self.__class__.__name__,
                    "selected_by": "seer_vote_known_werewolf",
                },
            )

        target = select_public_werewolf_claim_target(context) or select_alive_non_self(context)
        if target:
            return build_vote_action(
                context,
                target,
                reason_summary="预言家投公开查杀目标或合法存活目标。",
                metadata={
                    "strategy": self.__class__.__name__,
                    "selected_by": "seer_vote_fallback_target",
                },
            )
        return self.decide_fallback(context)

    def decide_tie_revote(self, context: AgentContext) -> AgentAction:
        werewolves = set(_known_werewolves(context, alive_only=True))
        for candidate in context.tie_candidates:
            if candidate != context.agent_id and candidate in werewolves:
                return build_vote_action(
                    context,
                    candidate,
                    reason_summary="平票二次投票优先投自己查验确认的狼人。",
                    metadata={
                        "strategy": self.__class__.__name__,
                        "selected_by": "seer_tie_known_werewolf",
                    },
                )

        target = select_tie_candidate(context)
        if target:
            return build_vote_action(
                context,
                target,
                reason_summary="预言家在平票候选人中选择投票目标。",
                metadata={
                    "strategy": self.__class__.__name__,
                    "selected_by": "seer_tie_candidate",
                },
            )
        return self.decide_fallback(context)

    def decide_last_words(self, context: AgentContext) -> AgentAction:
        werewolves = _known_werewolves(context, alive_only=True)
        villagers = _known_villager_aligns(context, alive_only=True)
        unchecked = _unchecked_alive_ids(context)

        parts: list[str] = ["我是预言家。"]
        if werewolves:
            parts.append(f"我查验过的狼人是 {', '.join(werewolves)}，请优先投出。")
        if villagers:
            parts.append(f"我查验过的金水是 {', '.join(villagers)}，可以信任发言。")
        if unchecked:
            parts.append(f"我还未查验的存活玩家是 {', '.join(unchecked)}，请好人多关注。")
        if not werewolves and not villagers and not unchecked:
            parts.append("我没有查到关键信息，请大家按公开发言继续推进。")

        message = " ".join(parts)
        return build_speak_action(
            context,
            message,
            reason_summary="预言家遗言：跳身份 + 报查验结果 + 列漏验 + 推荐投向。",
            metadata={
                "strategy": self.__class__.__name__,
                "selected_by": "seer_last_words_full_disclosure",
                "known_werewolves": werewolves,
                "known_villagers": villagers,
                "unchecked_alive": unchecked,
            },
        ).model_copy(update={"role_claim": Role.SEER})

    # ---- 内部 helper ----

    def _speak_with_check_claim(
        self,
        context: AgentContext,
        message: str,
        *,
        target: str,
        alignment: ClaimedAlignment,
        reason_summary: str,
        selected_by: str,
    ) -> AgentAction:
        """通过 builder 构造 SPEAK，再补 role_claim/claim_result 让 Engine 可机读。"""
        action = build_speak_action(
            context,
            message,
            reason_summary=reason_summary,
            metadata={
                "strategy": self.__class__.__name__,
                "selected_by": selected_by,
            },
        )
        return action.model_copy(
            update={
                "role_claim": Role.SEER,
                "claim_result": ClaimResult(
                    target=target,
                    claimed_alignment=alignment,
                ),
            }
        )
