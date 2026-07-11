"""Supervisor —— 调度层（Interface §3）。A 主导，B/C 对接。

A1.5 最小薄切片：`run_phase` 跑通一条数据流
    get_phase → get_required_actors → build_context(注入) → agent.act(注入)
    → 解析 → apply_action → emit → append。

后续接入：真实校验 RuleValidator(A3)、结算 ActionResolver(A4)、整局循环 next_phase(A2)、
Belief 更新 RealtimeBeliefUpdater(B)。跨边界依赖通过构造注入 + Protocol，不硬依赖具体实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from pydantic import ValidationError

from contracts import ActionType, AgentAction, AgentContext, BeliefState, EventType, Phase, PlayerStatus, Role

if TYPE_CHECKING:
    from contracts import GameEvent
    from game_core import GameEngine

    from supervisor.protocols import (
        AgentRuntime,
        BeliefUpdater,
        ContextAssembler,
        DiagnosticSink,
        EventSink,
        SlowThinkPolicy,
    )

    EventObserver = Callable[["GameEvent"], None]


_MAX_PHASE_STEPS = 1000  # run_game 死循环保护

# 顺序发言阶段：每人发完即时 emit+append，后发言者的 build_context 能看到本轮已发言者
# （含历史轮）的全部发言，更贴近真实讨论。
_SEQUENTIAL_SPEECH_PHASES = (Phase.DAY_DISCUSSION, Phase.DAY_TIE_DISCUSSION)
# 顺序投票阶段：单张 vote_cast 先作为观测事件实时 append；票型/平票/出局仍等全员
# action 收齐后批量结算，避免破坏投票规则。
_SEQUENTIAL_VOTE_PHASES = (Phase.DAY_VOTE, Phase.DAY_TIE_REVOTE)


class GameRunError(RuntimeError):
    """一局运行中断的可定位异常：携带 game_id / phase / actor，供 C 的 runner 填
    `GameRunResult.{error_phase, error_actor, error_type, error_message}` 定位失败。

    继承 RuntimeError（向后兼容此前 phase_stuck 抛的 RuntimeError）。原始异常经 `from exc`
    链在 `__cause__` 上，C 可用 `type(err.__cause__).__name__` 取 error_type。
    """

    def __init__(
        self, *, game_id: str, phase, actor: str | None = None, reason: str | None = None
    ) -> None:
        self.game_id = game_id
        self.phase = phase
        self.actor = actor
        self.reason = reason
        super().__init__(
            f"game run failed: game_id={game_id} phase={phase} actor={actor} reason={reason}"
        )


class Supervisor:
    def __init__(
        self,
        engine: GameEngine,
        context_assembler: ContextAssembler,
        agent_runtime: AgentRuntime,
        event_sink: EventSink,
        belief_updater: BeliefUpdater | None = None,
        slow_think_policy: "SlowThinkPolicy | None" = None,
        belief_store: "Any | None" = None,
        diagnostic_sink: "DiagnosticSink | None" = None,
        deliver_witch_kill_info: bool = False,
        event_observer: "EventObserver | None" = None,
    ) -> None:
        self._engine = engine
        self._context = context_assembler
        self._agent = agent_runtime
        self._sink = event_sink
        self._belief_updater = belief_updater
        self._slow_think_policy = slow_think_policy
        # System2 慢思持久化用：读当前 belief 传给 reflect、把返回的 enriched 落盘。
        # 与 ContextAssembler 读取的应是同一个 belief_store。None → 慢思读到空 belief、
        # 返回值不落盘（v0 / 无 belief lane 时慢思自然无效）。
        self._belief_store = belief_store
        self._diagnostic_sink = diagnostic_sink
        # 进 NIGHT_WITCH 前是否给女巫下发当晚刀口（PRIVATE_TO_WITCH）。
        # 默认 False：mock baseline 的 RoleStrategyMockAgent 女巫尚未挡"自救"，开了会在
        # 女巫被刀的局触发 target_self fallback。v0 LLM 女巫靠 prompt 处理(不自救)，构造时传 True。
        # 待 B 的 mock witch 加上"不救自己"守卫后，可改默认 True / 移除本开关。
        self._deliver_witch_kill_info = deliver_witch_kill_info
        # 旁观者回调（phase5 三方向并行地基条款 §2.3）：每条 append 的 GameEvent
        # 都会回调一次。默认 None=无开销；C 的实时上帝视角 SSE 端点用它把事件推给观众。
        # 红线：observer 只读，不能修改事件 / 影响游戏；observer 异常被吞掉。
        self._event_observer = event_observer
        self._belief_update_errors: list[dict[str, str | None]] = []
        self._pending_observation_events: list[GameEvent] = []
        self._slow_think_results: dict[tuple[str, str], BeliefState] = {}
        self._diagnostic_reports: list = []
        self._diagnostics_completed_game_ids: set[str] = set()

    async def _decide_action(self, game_id: str, agent_id: str, phase: Phase) -> AgentAction:
        """单个 actor：build_context(注入) → agent.act(注入) → 解析/校验/兜底 → 合法 AgentAction。"""
        context = self._context.build_context(game_id, agent_id, phase)
        context = await self._maybe_reflect_before_decision(game_id, agent_id, phase, context)
        payload = context.model_dump(mode="json")  # 序列化边界：Agent 只拿纯 JSON
        raw = await self.call_agent_with_retry(payload)
        # 传权威 actor_id：兜底用它（一定是合法 required actor），不信任可能脏的 context
        return self.validate_or_fallback(game_id, raw, context, actor_id=agent_id)

    async def _maybe_reflect_before_decision(
        self, game_id: str, agent_id: str, phase: Phase, context: AgentContext
    ) -> AgentContext:
        """System2 慢思：决策点前让 policy 重估 belief，由 **Supervisor** 负责持久化。

        契约（复审 finding2 修正）：``reflect`` 是**纯变换**——读入当前 belief + context，
        返回 enriched belief。**Supervisor** 把返回值写回 belief_store（与 ContextAssembler
        读取同一个），再重建 context 让本次决策用上。这样任何按 Protocol 直觉"只返回
        enriched"的实现都能生效，不再要求实现自行落盘。reflect 只动 belief 概率、不改
        context、不向其他 agent 注入文本，信息隔离不破。policy 为 None / 不该反思 / 无
        belief_store → 行为零变化。
        """
        if self._slow_think_policy is None:
            return context
        if not self._slow_think_policy.should_reflect(game_id, phase, context.round):
            return context
        current = self._read_current_belief(game_id, agent_id, phase, context.round)
        enriched = await self._slow_think_policy.reflect(
            game_id, agent_id, current, context
        )
        self._slow_think_results[(game_id, agent_id)] = enriched
        if self._belief_store is not None and enriched is not None:
            self._belief_store.save(enriched)
            # enriched 已落盘 → 重建 context 拾取慢思后的 belief。
            return self._context.build_context(game_id, agent_id, phase)
        return context

    def _read_current_belief(
        self, game_id: str, agent_id: str, phase: Phase, round: int | None
    ) -> BeliefState:
        """读观察者当前 real belief 传给 reflect；无 store / 未找到 → 返回空 BeliefState。"""
        if self._belief_store is not None:
            try:
                return self._belief_store.get(game_id, agent_id, is_shadow=False)
            except Exception:  # noqa: BLE001 —— 未找到/任意异常都退回空 belief，不崩
                pass
        return BeliefState(
            game_id=game_id,
            agent_id=agent_id,
            round=round,
            phase=phase,
            beliefs={},
        )

    async def run_phase(self, game_id: str) -> list[GameEvent]:
        phase = self._engine.get_current_phase(game_id)
        actors = self._engine.get_required_actors(game_id, phase)

        self._pending_observation_events = []
        current_actor: str | None = None
        try:
            if phase in _SEQUENTIAL_SPEECH_PHASES:
                # 顺序发言：每人发完立即结算 + 落 sink，下一个发言者 build_context 即可见。
                all_events: list[GameEvent] = []
                for agent_id in actors:
                    current_actor = agent_id  # 记录正在处理的 actor，供异常定位
                    action = await self._decide_action(game_id, agent_id, phase)
                    # 本人发言（含任何兜底观测事件）即时 emit + append → 后发言者立刻看得到
                    step = self._pending_observation_events + self.apply_actions(game_id, [action])
                    self._pending_observation_events = []
                    self.append_events(step)
                    self.trigger_belief_update(game_id, step)
                    all_events.extend(step)
                current_actor = None
                return all_events

            if phase in _SEQUENTIAL_VOTE_PHASES:
                # 同时投票：先收齐全部决策，再唱票。投票是**同时**动作，后投者不得先看到
                # 先投者投了谁——而 vote_cast 一旦 append 进 sink，下一个 actor 的 build_context
                # 就会在 current_round_events 里读到它（VOTE_CAST ∈ PUBLIC_EVENT_TYPES）。
                # 故所有 build_context 必须在任何 vote_cast 落 sink 之前完成。
                #
                # 第一轮：纯收集决策，不落 sink。每个 actor 的兜底观测事件（rule_validation /
                # fallback_used）按 actor 快照保存，同样延后到收齐后再落，避免泄漏给后投者。
                decisions: list[tuple[AgentAction, list[GameEvent]]] = []
                for agent_id in actors:
                    current_actor = agent_id
                    action = await self._decide_action(game_id, agent_id, phase)
                    obs = self._pending_observation_events
                    self._pending_observation_events = []
                    decisions.append((action, obs))
                current_actor = None

                # 第二轮：收齐后再逐票唱票。每票仍单独成组落 sink（保留实时唱票给旁观者的
                # 分组语义），此时已无 agent 会再 build_context，故不破坏同时性。
                actions: list[AgentAction] = []
                all_events: list[GameEvent] = []
                for action, obs in decisions:
                    actions.append(action)
                    step = obs + self._engine.emit_vote_cast(game_id, action)
                    self.append_events(step)
                    self.trigger_belief_update(game_id, step)
                    all_events.extend(step)

                final_events = self.apply_actions(game_id, actions, include_vote_events=False)
                self.append_events(final_events)
                self.trigger_belief_update(game_id, final_events)
                all_events.extend(final_events)
                return all_events

            # 批量：夜晚狼共识等必须收齐一次性结算，不能拆。
            actions: list[AgentAction] = []
            for agent_id in actors:
                current_actor = agent_id  # 记录正在处理的 actor，供异常定位
                actions.append(await self._decide_action(game_id, agent_id, phase))

            current_actor = None  # 进入批量结算，已不属于单个 actor
            events = self._pending_observation_events + self.apply_actions(game_id, actions)
            self.append_events(events)
            self.trigger_belief_update(game_id, events)
            return events
        except GameRunError:
            raise
        except Exception as exc:
            # 装配/Agent/结算的意外异常：附 game_id/phase/actor 供 C 定位（GameRunResult）。
            raise GameRunError(game_id=game_id, phase=phase, actor=current_actor) from exc

    async def call_agent_with_retry(self, context_payload: dict) -> dict:
        # A1.5 最小：调用一次。retry 由 C 的 AgentRuntime 后续接入。
        return await self._agent.act(context_payload)

    def validate_or_fallback(
        self,
        game_id: str,
        raw_action: dict,
        context: AgentContext | None = None,
        *,
        actor_id: str | None = None,
    ) -> AgentAction:
        # 解析为 AgentAction + 校验 game_id 一致（防 action 被路由到错误的 game）。
        # 目标：真实/LLM agent 的脏输出（含连 schema 都不合法的）都不让整局崩。
        # 后续 C 的 FallbackPolicy 接入时可替换 `_fallback_from_context`。
        try:
            action = AgentAction.model_validate(raw_action)
        except ValidationError as exc:
            # game_id 不一致是路由错误：无论 schema 是否合法都硬失败，绝不把别局的脏
            # 动作悄悄恢复成本局的安全动作。先于安全兜底检查。
            raw_game_id = raw_action.get("game_id") if isinstance(raw_action, dict) else None
            if raw_game_id is not None and raw_game_id != game_id:
                raise ValueError(
                    f"action.game_id {raw_game_id!r} 与当前 game {game_id!r} 不一致"
                ) from exc
            # schema-invalid（缺字段 / 非法 enum / action_type 乱填）：根本 parse 不出动作。
            recovery = self._safe_recovery(game_id, actor_id, context)
            if recovery is None:
                raise  # 无 engine / 合法 actor 可兜底（薄切片或集成错误）：照常抛
            session, actor = recovery
            raw_type = raw_action.get("action_type") if isinstance(raw_action, dict) else None
            return self._degrade_to_safe(
                session,
                actor=actor,
                original_target=None,
                original_action_type=raw_type,
                violation_type="schema_invalid",
                message=f"raw action failed schema validation: {type(exc).__name__}",
            )

        if action.game_id != game_id:
            raise ValueError(
                f"action.game_id {action.game_id!r} 与当前 game {game_id!r} 不一致"
            )
        if context is None or self._engine is None:
            return action

        session = self._engine.sessions.get_game(game_id)
        result = self._engine.rules.validate(session, action)
        if result.is_valid:
            return action

        # 1) 先试 context 兜底（占位 C 的 FallbackPolicy，只用 Agent 可见信息）。
        fallback = self._fallback_from_context(context)
        if self._engine.rules.validate(session, fallback).is_valid:
            self._record_fallback(
                session,
                actor=action.agent_id,
                original_target=action.target,
                original_action_type=action.action_type.value,
                violation_type=result.violation_type,
                message=result.message,
                fallback=fallback,
                degraded=False,
                fallback_failed=False,
            )
            return fallback

        # 2) context 兜底也非法：退到真相态推导的安全动作。**绝不抛异常**。
        actor = self._pick_valid_actor(session, actor_id, context.agent_id, action.agent_id)
        if actor is None:
            # 连一个合法 actor 都定位不到（极端 / 集成错误）：原非法动作交给 Engine 兜（不结算、
            # 发 rule_validation），supervisor 不再加工，但同样不抛、不卡死。
            return action
        return self._degrade_to_safe(
            session,
            actor=actor,
            original_target=action.target,
            original_action_type=action.action_type.value,
            violation_type=result.violation_type,
            message=result.message,
        )

    def _safe_recovery(self, game_id: str, actor_id: str | None, context: AgentContext | None):
        """schema-invalid 时定位 (session, 合法 actor)；无法兜底返回 None。"""
        if self._engine is None:
            return None
        session = self._engine.sessions.get_game(game_id)
        ctx_actor = context.agent_id if context is not None else None
        actor = self._pick_valid_actor(session, actor_id, ctx_actor)
        return (session, actor) if actor is not None else None

    @staticmethod
    def _pick_valid_actor(session, *candidates: str | None) -> str | None:
        """返回第一个确实存在于本局 players 里的 actor，避免 _safe_legal_action 触发 KeyError。"""
        players = session.truth_state.players
        for candidate in candidates:
            if candidate is not None and candidate in players:
                return candidate
        return None

    def _degrade_to_safe(
        self,
        session,
        *,
        actor: str,
        original_target: str | None,
        original_action_type,
        violation_type: str | None,
        message: str | None,
    ) -> AgentAction:
        """退到真相态安全动作并复验：仍非法则标 fallback_failed（Engine 是最终关卡）。"""
        safe = self._safe_legal_action(session, actor)
        safe_valid = self._engine.rules.validate(session, safe).is_valid
        self._record_fallback(
            session,
            actor=actor,
            original_target=original_target,
            original_action_type=original_action_type,
            violation_type=violation_type,
            message=message,
            fallback=safe,
            degraded=True,
            fallback_failed=not safe_valid,
        )
        return safe

    def _record_fallback(
        self,
        session,
        *,
        actor: str,
        original_target: str | None,
        original_action_type,
        violation_type: str | None,
        message: str | None,
        fallback: AgentAction,
        degraded: bool,
        fallback_failed: bool,
    ) -> None:
        """把"原动作非法 + 已用兜底替换"记成可观测事件（原非法动作必须可见）。"""
        self._pending_observation_events.extend(
            [
                self._engine.events.emit(
                    session,
                    EventType.RULE_VALIDATION.value,
                    {
                        "actor": actor,
                        "target": original_target,
                        "action_type": original_action_type,
                        "is_valid": False,
                        "violation_type": violation_type,
                        "message": message,
                    },
                ),
                self._engine.events.emit(
                    session,
                    EventType.FALLBACK_USED.value,
                    {
                        "actor": actor,
                        "target": fallback.target,
                        "original_error": violation_type,
                        "fallback_action": fallback.action_type.value,
                        "retry_count": 0,
                        # degraded=True：连 context 兜底都非法，已退到真相态安全兜底；
                        # fallback_failed=True：安全兜底复验仍非法（极端态），由 Engine 最终把关。
                        "degraded": degraded,
                        "fallback_failed": fallback_failed,
                    },
                ),
            ]
        )

    def _safe_legal_action(self, session, agent_id: str) -> AgentAction:
        """从真相态推导一个最后兜底动作。

        只在 Supervisor/Engine 侧使用、不经过 Agent，与信息隔离无关；用真相态而非
        （可能脏的）context。**前置条件**：`agent_id` 必须在 `session.players` 内
        （由调用方经 `_pick_valid_actor` 保证），否则 KeyError。

        对所有"可达"phase 状态它都合法（讨论/遗言=speak、女巫=skip、猎人=pass、
        刀/查/投=真相态里的存活合法目标；skip 只在 NIGHT_WITCH 合法，不能通用）。
        极端/不可达态（如空 tie_candidates）下仍可能产出非法动作，因此**调用方
        （`_degrade_to_safe`）会复验并标 fallback_failed**，Engine 是最终关卡。
        """
        phase = session.current_phase
        players = session.truth_state.players
        role = players[agent_id].role
        action_type = ActionType.SPEAK
        target: str | None = None
        message: str | None = None

        def _first_alive_non_self(*, exclude_wolves: bool = False) -> str | None:
            for pid, p in players.items():
                if pid == agent_id or p.status != PlayerStatus.ALIVE:
                    continue
                if exclude_wolves and p.role == Role.WEREWOLF:
                    continue
                return pid
            return None

        if phase == Phase.NIGHT_WEREWOLF:
            action_type = ActionType.NIGHT_KILL_NOMINATE
            target = _first_alive_non_self(exclude_wolves=True)
        elif phase == Phase.NIGHT_SEER:
            action_type = ActionType.CHECK
            target = _first_alive_non_self()
        elif phase == Phase.NIGHT_WITCH:
            action_type = ActionType.SKIP
        elif phase == Phase.DAY_VOTE:
            action_type = ActionType.VOTE
            target = _first_alive_non_self()
        elif phase == Phase.DAY_TIE_REVOTE:
            action_type = ActionType.VOTE
            target = next(
                (
                    pid
                    for pid in session.truth_state.round_state.tie_candidates
                    if pid != agent_id and players[pid].status == PlayerStatus.ALIVE
                ),
                None,
            )
        elif phase == Phase.HUNTER_SHOOT:
            action_type = ActionType.HUNTER_SHOOT  # target=None（pass）永远合法
        else:  # DAY_DISCUSSION / DAY_TIE_DISCUSSION / EXILE_LAST_WORDS 等
            action_type = ActionType.SPEAK
            message = "(safe fallback) 暂无更多信息。"

        return AgentAction(
            game_id=session.game_id,
            agent_id=agent_id,
            role=role,
            phase=phase,
            action_type=action_type,
            target=target,
            public_message=message,
            metadata={"fallback_used": True, "safe_fallback": True},
        )

    def _fallback_from_context(self, context: AgentContext) -> AgentAction:
        phase = context.phase
        action_type = ActionType.SPEAK
        target: str | None = None
        public_message: str | None = None
        metadata: dict = {"fallback_used": True}

        if phase == Phase.NIGHT_WEREWOLF:
            action_type = ActionType.NIGHT_KILL_NOMINATE
            target = self._first_context_target(context)
        elif phase == Phase.NIGHT_SEER:
            action_type = ActionType.CHECK
            target = self._first_context_target(context)
        elif phase == Phase.NIGHT_WITCH:
            action_type = ActionType.SKIP
        elif phase in (Phase.DAY_DISCUSSION, Phase.DAY_TIE_DISCUSSION):
            action_type = ActionType.SPEAK
            public_message = "I will stay cautious and listen to more information."
        elif phase == Phase.DAY_VOTE:
            action_type = ActionType.VOTE
            target = self._first_context_target(context)
        elif phase == Phase.DAY_TIE_REVOTE:
            action_type = ActionType.VOTE
            target = next((pid for pid in context.tie_candidates if pid != context.agent_id), None)
        elif phase == Phase.HUNTER_SHOOT:
            action_type = ActionType.HUNTER_SHOOT
            target = None
            metadata["pass"] = True
        elif phase == Phase.EXILE_LAST_WORDS:
            action_type = ActionType.SPEAK
            public_message = "These are my last words. Please review the votes carefully."

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

    @staticmethod
    def _first_context_target(context: AgentContext) -> str | None:
        hinted = context.rule_hints.get("fallback_targets")
        if isinstance(hinted, list):
            for pid in hinted:
                if isinstance(pid, str) and pid != context.agent_id:
                    return pid
        for player in context.visible_players:
            if player.player_id != context.agent_id and player.status == PlayerStatus.ALIVE:
                return player.player_id
        return None

    def apply_actions(
        self,
        game_id: str,
        actions: list[AgentAction],
        *,
        include_vote_events: bool = True,
    ) -> list[GameEvent]:
        return self._engine.apply_actions(
            game_id, actions, include_vote_events=include_vote_events
        )

    def append_events(self, events: list[GameEvent]) -> None:
        self._sink.append_many(events)
        if self._event_observer is None:
            return
        for ev in events:
            # 深拷贝隔离：InMemoryEventStore 保留同一对象引用，如果 observer 误改
            # ev.payload / ev.target，会污染 sink 落盘事件、进而污染 trigger_belief_update /
            # ContextAssembler / Replay。"observer 只读"从代码层强制，不靠约定。
            try:
                self._event_observer(ev.model_copy(deep=True))
            except Exception:  # noqa: BLE001 - 旁观者异常绝不影响游戏走向
                pass

    def trigger_belief_update(self, game_id: str, events: list[GameEvent]) -> None:
        if self._belief_updater is None:
            return
        for event in events:
            try:
                self._belief_updater.update(game_id, event.event_id)
            except Exception as exc:  # noqa: BLE001 - shadow belief must not block game flow
                self._belief_update_errors.append(
                    {
                        "game_id": game_id,
                        "event_id": event.event_id,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )

    def _run_diagnostics(self, game_id: str) -> None:
        if self._diagnostic_sink is None:
            return
        if game_id in self._diagnostics_completed_game_ids:
            return
        self._diagnostic_reports.extend(self._diagnostic_sink.on_game_end(game_id))
        self._diagnostics_completed_game_ids.add(game_id)

    async def run_game(self, game_id: str) -> None:
        """跑完整一局：循环 取 phase → run_phase(有 actors)/跳过 → advance_phase，直到 GAME_OVER。

        终局由 PhaseController 以 round >= max_rounds 收敛（真实胜负判定 A4/A6 WinChecker 接入）。
        无 required actors 的阶段(死亡角色夜晚、纯结算阶段)自动跳过并推进。
        带步数保护，防状态机 bug 死循环；所有事件经 run_phase → event_sink 累积。
        """
        # 已结束的 session 再次 run_game 时直接返回：否则会在终局 game_over 之后又追加
        # 一条 role_assigned，污染审计时间线。role_assigned 必须只在一局真正开始时发一次。
        if self._engine.get_current_phase(game_id) == Phase.GAME_OVER:
            self._run_diagnostics(game_id)
            return
        # 开局先发 role_assigned 锚点（方案 A：无身份映射），作为整局事件流的起点。
        role_assigned = [self._engine.emit_role_assigned(game_id)]
        self.append_events(role_assigned)
        self.trigger_belief_update(game_id, role_assigned)
        # 紧接着播一条狼私有「队友名单」事件（PRIVATE_TO_WOLVES）：狼夜刀避队友的信息源。
        # 必须在第一夜 build_context 之前进 sink，否则狼第一刀就可能误伤队友。
        wolf_teammates = self._engine.emit_wolf_teammates(game_id)
        self.append_events(wolf_teammates)
        self.trigger_belief_update(game_id, wolf_teammates)
        for _ in range(_MAX_PHASE_STEPS):
            phase = self._engine.get_current_phase(game_id)
            if phase == Phase.GAME_OVER:
                self._run_diagnostics(game_id)
                return
            try:
                # 进入每个 phase 先发 phase_started（含被跳过的阶段），给 replay 完整时间轴锚点。
                # 纯观测事件，不传入 advance_phase（不携带任何状态机转换信号）。
                phase_started = [self._engine.emit_phase_started(game_id)]
                self.append_events(phase_started)
                self.trigger_belief_update(game_id, phase_started)
                # 进女巫阶段前下发当晚刀口（PRIVATE_TO_WITCH），让女巫(v0 LLM)看得到刀口。
                # 必须在 build_context 之前进同一个 sink；gate 在 NIGHT_WITCH（kill_target 整夜有值）。
                # 受 deliver_witch_kill_info 开关控制（默认关，详见 __init__）。
                if self._deliver_witch_kill_info and phase == Phase.NIGHT_WITCH:
                    witch_kill_info = self._engine.emit_witch_kill_info(game_id)
                    self.append_events(witch_kill_info)
                    self.trigger_belief_update(game_id, witch_kill_info)
                actors = self._engine.get_required_actors(game_id, phase)
                if actors:
                    events = await self.run_phase(game_id)
                else:
                    events = self._engine.resolve_phase(game_id)
                    self.append_events(events)
                    self.trigger_belief_update(game_id, events)
                self._engine.advance_phase(game_id, events)
            except GameRunError:
                raise  # run_phase 已带 actor 定位，原样上抛
            except Exception as exc:
                # 系统结算阶段（resolve_phase/advance_phase/emit）的意外异常：附 game_id/phase。
                raise GameRunError(game_id=game_id, phase=phase, actor=None) from exc
        raise GameRunError(
            game_id=game_id,
            phase=self._engine.get_current_phase(game_id),
            actor=None,
            reason="phase_stuck",
        )
