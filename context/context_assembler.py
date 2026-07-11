"""ContextAssembler —— Task C7。Agent 唯一的信息入口。

按 ``(game_id, agent_id, phase)`` 装配 ``AgentContext``，通过 ``ContextWindowPolicy``
裁剪到预算内，最后输出 JSON-serializable ``contracts.AgentContext``。

接口固定在 ``supervisor/protocols.py:15``（A 用 Protocol 框死），C 只填实现。
supervisor 注入式调用 ``self._context.build_context(...)``，supervisor 零改动。

红线（与 CLAUDE.md / Schema_v2_1 / Interface_v2_1 §4.2 一致）：

- 输出**不得**包含：``TruthState`` / ``GameSession`` 引用 / 任何 ``*Store`` 引用 /
  EventLogger 写接口 / Engine 内部对象
- 输出 JSON **禁止**出现：``truth_state / role_map / hidden_roles`` 键
- 序列化边界：``json.loads(json.dumps(context.model_dump()))`` 后再传给 ``agent.act()``
- v0 不注入 ``belief_state``；v1 注入 ``belief_state``（当前选 v1 + 空 BeliefState 占位）
- 总是注入 ``allowed_actions / visible_players / public_events / private_events``
- ``DAY_TIE_DISCUSSION / DAY_TIE_REVOTE`` 时 ``tie_candidates`` 已从 ``RoundState`` 读

读取来源：

- ``GameSessionProvider`` —— 拿 GameSession（含 TruthState）
- ``EventStore`` —— 完整事件流
- ``VisibilityRuleSpec`` —— 信息可见性决定权威
- ``BeliefStateStore`` —— v1 注入 belief_state（可选）
- ``ContextWindowPolicy`` —— 预算裁剪
- ``SpeechSummarizer`` —— Day 2+ 历史发言压缩

依赖注入：构造时传 6 个组件（其中 ``belief_store`` 可选）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from contracts import (
    AgentContext,
    ClaimRecord,
    ContextBudgetConfig,
    EventType,
    Phase,
    PlayerStatus,
    Role,
    VoteRecord,
)

from context.context_window_policy import ContextWindowPolicy
from context.speech_summarizer import SpeechSummarizer
from context.types import AgentContextDraft
from context.visibility_rules import VisibilityRuleSpec
from stores.exceptions import BeliefStateNotFoundError

if TYPE_CHECKING:
    from contracts.schemas import GameEvent, PublicEvent
    from context.protocols import GameSessionProvider
    from stores.belief_state_store import BeliefStateStore
    from stores.event_store import EventStore


_DERIVED_BY = "context_assembler"


class ContextAssembler:
    """Agent 唯一的信息入口。

    无状态：可共享一个实例给所有 Agent（依赖 stores / providers 是注入的）。
    """

    def __init__(
        self,
        session_provider: GameSessionProvider,
        event_store: EventStore,
        visibility: VisibilityRuleSpec | None = None,
        window_policy: ContextWindowPolicy | None = None,
        summarizer: SpeechSummarizer | None = None,
        belief_store: BeliefStateStore | None = None,
        budget: ContextBudgetConfig | None = None,
        *,
        belief_inject_filter: Callable[[str], bool] | None = None,
        enable_typed_records: bool = False,
    ) -> None:
        """所有依赖通过构造注入。

        Args:
            session_provider: 拿 GameSession 的窄接口（supervisor 通常传 GameEngine.sessions）
            event_store: 完整事件流来源（C 已实装）
            visibility: 信息可见性权威；None 用默认 ``VisibilityRuleSpec()``
            window_policy: 预算裁剪；None 用默认 ``ContextWindowPolicy()``
            summarizer: 历史发言压缩；None 用默认 ``SpeechSummarizer()``
            belief_store: v1 注入用；None 表示 v0（不注入 belief_state）
            budget: 预算配置；None 用默认 ``ContextBudgetConfig()``
            belief_inject_filter: 可选谓词 ``(agent_id) -> bool``，决定**是否给该 agent
                注入 belief**。混合实验（A）用它实现"只给部分玩家注入"（如只给狼）。
                - ``None``（默认）→ 全部注入（兼容现有 v1 行为，``belief_store`` 在场即注入）。
                - 返回 ``False`` 的 agent → ``belief_state={}`` / ``belief_top_suspects=[]``，
                  等价于该 agent 退化成 v0。
                只看 ``agent_id``、不接 ``role``：role→camp 的映射由调用方在闭包里查
                truth state 决定（ContextAssembler 内部不依赖 role 决定注入）。
                注意：本 filter **只控制注入侧**，不影响后台 ``belief_updater``（updater
                始终运行，shadow / 真实 belief 照常更新）——这是混合实验的关键。
            enable_typed_records: 是否把 ``ClaimRecord`` / ``VoteRecord`` typed 台账投影
                注入 ``AgentContext``（v2.2 预留字段；与字符串 FactStream 并存）。
                **默认 False**：9 人 D3 HUNTER_SHOOT 等满载阶段加上 typed records 会越过
                ``max_input_tokens_per_agent=4000``，触发 ``ContextBudgetExceededError``。
                等 ``ContextBudgetConfig`` 加 ``max_claim_records`` / ``max_vote_records``
                字段（contract MR）+ 上调 ``max_input_tokens_per_agent`` 后默认改 True。
        """
        self._session_provider = session_provider
        self._event_store = event_store
        self._visibility = visibility or VisibilityRuleSpec()
        self._window = window_policy or ContextWindowPolicy()
        self._summarizer = summarizer or SpeechSummarizer()
        self._belief_store = belief_store
        self._belief_inject_filter = belief_inject_filter
        self._budget = budget or ContextBudgetConfig()
        self._enable_typed_records = enable_typed_records

    # --- public API ---------------------------------------------------------

    def build_context(
        self, game_id: str, agent_id: str, phase: Phase
    ) -> AgentContext:
        """装配 ``AgentContext``，21 字段全填对。

        签名跟 ``supervisor/protocols.py:15 ContextAssembler`` Protocol 对齐。
        """
        session = self._session_provider.get_session(game_id)

        # === 基础字段 ===
        if agent_id not in session.truth_state.players:
            raise ValueError(
                f"agent_id {agent_id!r} not found in game {game_id!r}"
            )
        player = session.truth_state.players[agent_id]
        round_state = session.truth_state.round_state
        current_round = session.round

        # === 从 EventStore 拉所有事件 ===
        all_events: list[GameEvent] = self._event_store.list_by_game(game_id)

        # === 可见性过滤 ===
        visible_players = self._visibility.visible_players(session, agent_id)
        allowed_actions = self._visibility.allowed_actions(session, agent_id, phase)
        all_public = self._visibility.visible_public_events(
            all_events, session, agent_id
        )
        private_events = self._visibility.visible_private_events(
            all_events, session, agent_id
        )

        # current_round_events: 当前 round 的所有公开事件
        current_round_events = [ev for ev in all_public if ev.round == current_round]

        # recent_public_events: 排除历史轮 SPEECH（max_historical_speech_raw=0 红线），
        # 其他历史事件（EXILE / DEATH_CONFIRMED / HUNTER_SHOT 等）保留
        recent_public_events = [
            ev
            for ev in all_public
            if ev.round == current_round
            or not _is_speech_event(ev)
        ]

        # public_events: 跟 recent_public 一致（contracts 字段冗余，沿用同源）
        public_events = list(recent_public_events)

        # === Day 2+ 历史发言压成 Fact Stream ===
        public_memory_summary = []
        if current_round >= 2:
            fact_streams = self._summarizer.summarize_by_round(
                all_public, exclude_round=current_round
            )
            public_memory_summary = [fs.model_dump() for fs in fact_streams]

        # === v1 注入 belief_state ===
        belief_state_dict: dict = {}
        belief_top_suspects: list = []
        should_inject_belief = self._belief_store is not None and (
            self._belief_inject_filter is None
            or self._belief_inject_filter(agent_id)
        )
        if should_inject_belief:
            try:
                belief = self._belief_store.get(game_id, agent_id, is_shadow=False)
                belief_state_dict = belief.model_dump(mode="json")
                belief_top_suspects = _top_werewolf_suspects(
                    belief, top_n=self._budget.max_belief_top_suspects
                )
            except BeliefStateNotFoundError:
                # 没存过 belief —— v1 空占位（结构完整、概率全 0）
                belief_state_dict = {}
                belief_top_suspects = []

        # === rule_hints ===
        rule_hints = self._compute_rule_hints(session, agent_id)

        # === typed 台账投影（v2.2 预留，从可见公开事件流派生）===
        # claim_records 从 SPEECH（含 role_claim / claim_result）；
        # vote_records 从 VOTE_CAST（按 phase 区分 primary / revote）。
        # 默认 off：见 __init__ docstring 的 enable_typed_records 说明（budget 阻塞）。
        if self._enable_typed_records:
            claim_records = _project_claim_records(all_public, game_id)
            vote_records = _project_vote_records(all_public, game_id)
        else:
            claim_records = []
            vote_records = []

        # === 拼 Draft ===
        draft = AgentContextDraft(
            game_id=game_id,
            agent_id=agent_id,
            role=player.role,
            round=current_round,
            phase=phase,
            is_secondary_stage=round_state.is_secondary_stage,
            secondary_stage_type=_secondary_stage_type(phase),
            tie_candidates=list(round_state.tie_candidates),
            previous_vote_summary=dict(round_state.previous_vote_summary),
            visible_players=visible_players,
            current_round_events=current_round_events,
            recent_public_events=recent_public_events,
            public_memory_summary=public_memory_summary,
            public_events=public_events,
            private_events=private_events,
            belief_state=belief_state_dict,
            belief_top_suspects=belief_top_suspects,
            strategy_memory=[],  # v2 才用，第一阶段固定空
            allowed_actions=list(allowed_actions),
            rule_hints=rule_hints,
            claim_records=claim_records,
            vote_records=vote_records,
        )

        # === 预算裁剪 ===
        return self._window.apply(draft, self._budget)

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _compute_rule_hints(session, agent_id: str) -> dict:
        """计算 ``rule_hints``。

        当前约定的 key：

        - ``fallback_targets`` —— 存活且非自己的玩家 ID 列表（A 的临时 fallback +
          C 的 FallbackPolicy 都在用此字段做兜底目标候选）

        未来扩展的 key 等待会议确认（见 ``docs/c_modules.md §5``）。
        """
        fallback_targets = sorted(
            pid
            for pid, p in session.truth_state.players.items()
            if pid != agent_id and p.status == PlayerStatus.ALIVE
        )
        return {"fallback_targets": fallback_targets}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _is_speech_event(ev: PublicEvent) -> bool:
    et = ev.event_type
    if isinstance(et, EventType):
        return et == EventType.SPEECH
    return str(et) == EventType.SPEECH.value


def _secondary_stage_type(phase: Phase) -> str | None:
    """从 phase 推断 secondary_stage_type 字符串。"""
    if phase == Phase.DAY_TIE_DISCUSSION:
        return "tie_discussion"
    if phase == Phase.DAY_TIE_REVOTE:
        return "tie_revote"
    return None


def _project_claim_records(public_events: list[PublicEvent], game_id: str) -> list[ClaimRecord]:
    """从公开 SPEECH 事件投影出 ``ClaimRecord``。

    触发条件：``event_type == SPEECH`` 且至少有 ``role_claim`` 或 ``claim_result`` 之一。
    ``is_counter_claim`` 启发式：同一 game 内已有更早的 *不同 actor* 跳同一个 role → 当前
    这条算对跳（counter claim）。事件按 EventStore 时序处理（``visible_public_events`` 沿用顺序）。
    """
    records: list[ClaimRecord] = []
    seen_role_claims: dict[Role, str] = {}
    for ev in public_events:
        if ev.event_type != EventType.SPEECH:
            continue
        if ev.role_claim is None and ev.claim_result is None:
            continue
        if not ev.actor:
            continue
        is_counter = False
        if ev.role_claim is not None:
            prior_actor = seen_role_claims.get(ev.role_claim)
            if prior_actor and prior_actor != ev.actor:
                is_counter = True
            else:
                seen_role_claims.setdefault(ev.role_claim, ev.actor)
        records.append(
            ClaimRecord(
                record_id=f"cr_{ev.event_id}",
                game_id=game_id,
                round=ev.round,
                phase=ev.phase,
                actor=ev.actor,
                claimed_role=ev.role_claim,
                claim_target=ev.claim_result.target if ev.claim_result else None,
                claimed_alignment=(
                    ev.claim_result.claimed_alignment if ev.claim_result else None
                ),
                is_counter_claim=is_counter,
                source_event_id=ev.event_id,
                derived_by=_DERIVED_BY,
            )
        )
    return records


def _project_vote_records(public_events: list[PublicEvent], game_id: str) -> list[VoteRecord]:
    """从公开 ``VOTE_CAST`` 事件投影出 ``VoteRecord``。

    ``stage`` / ``is_revote`` 按 phase 区分：``DAY_TIE_REVOTE`` → "revote"+True；其它 → "primary"+False。
    ``is_tie_candidate_vote`` 与 ``is_revote`` 等价（引擎强制二次投票只能投平票候选）。
    """
    records: list[VoteRecord] = []
    for ev in public_events:
        if ev.event_type != EventType.VOTE_CAST:
            continue
        if not ev.actor:
            continue
        is_revote = ev.phase == Phase.DAY_TIE_REVOTE
        records.append(
            VoteRecord(
                record_id=f"vr_{ev.event_id}",
                game_id=game_id,
                round=ev.round,
                phase=ev.phase,
                stage="revote" if is_revote else "primary",
                voter=ev.actor,
                target=ev.target,
                is_revote=is_revote,
                is_tie_candidate_vote=is_revote,
                source_event_id=ev.event_id,
                derived_by=_DERIVED_BY,
            )
        )
    return records


def _top_werewolf_suspects(belief, top_n: int) -> list:
    """从 BeliefState 拿 werewolf 概率最高的前 N 个 player。

    返回格式：``[{"player_id": "P3", "werewolf_prob": 0.7}, ...]``
    """
    items: list[tuple[str, float]] = []
    for pid, role_belief in belief.beliefs.items():
        wolf_prob = float(getattr(role_belief, "werewolf", 0.0))
        items.append((pid, wolf_prob))
    items.sort(key=lambda x: -x[1])  # 概率降序
    return [
        {"player_id": pid, "werewolf_prob": prob}
        for pid, prob in items[:top_n]
    ]
