"""GameEngine —— 对外门面，组合 game_core 的各子模块。

Engine 管"真相和规则"。绝不：调用 LLM、写 prompt、做角色策略、
把 TruthState 暴露给 Agent、直接落盘 EventLog（只 emit，落盘交给 EventLogger）。
Supervisor 修改状态必须通过 Engine。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from contracts.enums import DeathCause, EventType, Phase, PlayerStatus, Role, Visibility

from game_core.action_resolver import ActionResolver
from game_core.event_emitter import EventEmitter
from game_core.hunter_shoot_resolver import HunterShootResolver
from game_core.phase_controller import PhaseController
from game_core.rule_validator import RuleValidator
from game_core.session_manager import GameSessionManager
from game_core.truth_state_store import TruthStateStore
from game_core.win_checker import WinChecker

if TYPE_CHECKING:
    from collections.abc import Callable

    from contracts.schemas import AgentAction, GameEvent

    from game_core.types import GameSession, WinCheckResult


class GameEngine:
    def __init__(self, clock: Callable[[], str] | None = None) -> None:
        self.sessions = GameSessionManager()
        self.phases = PhaseController()
        self.rules = RuleValidator()
        self.hunter = HunterShootResolver()
        self.win = WinChecker()
        # clock 注入到 EventEmitter：默认 wall-clock；注入逻辑时钟可让 replay 字节级可复现。
        self.events = EventEmitter(clock=clock)
        self.resolver = ActionResolver(self.events)
        self.truth = TruthStateStore()

    def get_current_phase(self, game_id: str) -> Phase:
        return self.sessions.get_game(game_id).current_phase

    def get_session(self, game_id: str) -> GameSession:
        """对外只读访问 GameSession（满足 SessionProvider Protocol）。

        给 C 的 ContextAssembler / VisibilityRuleSpec 取真相态用——它们靠这一层读
        truth_state 装配 AgentContext。调用方只读，**禁止透传 GameSession/TruthState
        进 AgentContext**（信息隔离红线）。比直接戳 engine.sessions.get_game 稳定，
        A 可在不破坏 C 的前提下换内部实现。
        """
        return self.sessions.get_game(game_id)

    def emit_phase_started(self, game_id: str) -> GameEvent:
        """进入某 phase 时产出 phase_started 观测事件，作为 replay 的阶段时间轴锚点。

        round / phase 已是 GameEvent 顶层字段（emit 从 session 取），payload 留空即可。
        纯观测事件，不改 TruthState、不参与 advance_phase 的状态机判定。
        """
        session = self.sessions.get_game(game_id)
        return self.events.emit(session, EventType.PHASE_STARTED.value, {})

    def emit_role_assigned(self, game_id: str) -> GameEvent:
        """开局发牌锚点（方案 A：不含 pid→role 真实映射，杜绝信息泄漏）。

        payload 只放公开设置信息（人数 + 角色数量分布）——这本就是 GameConfig 里人人
        皆知的设定，不是"谁是什么"。真实身份留在 TruthState，由赛后 PostGameAnalyzer /
        Replay 直接读取（spec 允许游戏结束后读真身份）。

        ⚠️ 发牌 seed **绝不**放进本事件：role_assigned 默认 public，agent 一旦看到 seed，
        结合公开 config + 发牌算法即可反推 pid→role。seed 仅留在 GameSession.seed，供赛后
        replay/export 直接读取（赛后读真相是允许的，不经 AgentContext）。
        """
        session = self.sessions.get_game(game_id)
        return self.events.emit(
            session,
            EventType.ROLE_ASSIGNED.value,
            {
                "player_count": session.config.player_count,
                "role_counts": session.config.roles.model_dump(),
            },
        )

    def emit_wolf_teammates(self, game_id: str) -> list[GameEvent]:
        """开局给狼阵营播一条私有「队友名单」事件（狼夜刀避队友、白天不误投队友的唯一信息源）。

        载体刻意选 WOLF_NOMINATION + visibility=PRIVATE_TO_WOLVES：这是 C 的 VisibilityRuleSpec
        里**唯一对全狼公开**的私密事件类型。role_assigned 被 C 的 AI 可见白名单挡在外面
        （防真相泄漏），不能用来传队友——所以队友名单必须搭 WOLF_NOMINATION 这趟车。
        payload.teammates 由 C 的 _to_private_event 读出 → AgentContext.private_events[*].teammates，
        供 select_wolf_kill_target / 狼策略据此避开队友。

        只发狼可见的狼 pid 列表（含本人，select_* 各自再排除自己）；绝不进 public 事件、
        绝不含 seed / 非狼身份（方案 A 红线）。无狼则不发。一局只在开局发一次：
        ContextAssembler 读全量历史、ContextWindowPolicy 不裁 private_events，故跨轮持续可见。
        """
        session = self.sessions.get_game(game_id)
        wolves = sorted(
            pid
            for pid, player in session.truth_state.players.items()
            if player.role == Role.WEREWOLF
        )
        if not wolves:
            return []
        return [
            self.events.emit(
                session,
                EventType.WOLF_NOMINATION.value,
                {"visibility": Visibility.PRIVATE_TO_WOLVES.value, "teammates": wolves},
            )
        ]

    def emit_witch_kill_info(self, game_id: str) -> list[GameEvent]:
        """进入 NIGHT_WITCH 时把当晚刀口作为 PRIVATE_TO_WITCH 事件下发给女巫。

        修 `witch_knows_kill_target=true` 的空头承诺：在此之前引擎从不 emit
        WITCH_KILL_TARGET_INFO，女巫在真实 ContextAssembler 装配的 context 里永远看不到刀口。

        ⚠️ 接线时机（与 W1 狼队友同型）：Supervisor 必须在 `phase_started(NIGHT_WITCH)` 之后、
        `build_context` 之前把本事件 append 到**同一个 EventStore**，否则女巫仍看不到。
        本方法只「生成」事件，由 Supervisor append（Engine 不落盘）。

        仅当 `witch_knows_kill_target=true`、本晚有刀口、且有存活女巫时才发（避免无意义事件）。
        payload.target=kill_target, actor=None（C 的 _agent_can_see_private_actor 对
        WITCH_KILL_TARGET_INFO 允许 actor=None）。纯观测事件，不改 TruthState。
        """
        session = self.sessions.get_game(game_id)
        if not session.config.rules.witch_knows_kill_target:
            return []
        kill_target = session.truth_state.night_state.kill_target
        if kill_target is None:
            return []
        has_alive_witch = any(
            player.role == Role.WITCH and player.status == PlayerStatus.ALIVE
            for player in session.truth_state.players.values()
        )
        if not has_alive_witch:
            return []
        return [
            self.events.emit(
                session,
                EventType.WITCH_KILL_TARGET_INFO.value,
                {"visibility": Visibility.PRIVATE_TO_WITCH.value, "target": kill_target},
            )
        ]

    def get_required_actors(self, game_id: str, phase: Phase) -> list[str]:
        return self.phases.get_required_actors(self.sessions.get_game(game_id), phase)

    def apply_action(self, game_id: str, action: AgentAction) -> list[GameEvent]:
        """校验 → 结算 → emit 事件。返回本次产生的 GameEvent 列表。

        单 action 入口保留给对外接口；批量阶段（狼人提名/投票）由 apply_actions 统一结算。
        """
        return self.apply_actions(game_id, [action])

    def apply_actions(
        self,
        game_id: str,
        actions: list[AgentAction],
        *,
        include_vote_events: bool = True,
    ) -> list[GameEvent]:
        """A 内部批量入口：逐个校验，非法动作各记 rule_validation 并丢弃，合法动作正常结算。

        语义：**非法动作不作废整批**。一张废票/一个非法提名只把自己作废（仍可观测），
        其余合法动作照常结算（如投票按合法票统计）。非法动作本身永不结算——红线不变。
        """
        session = self.sessions.get_game(game_id)
        valid_actions: list[AgentAction] = []
        invalid_events: list[GameEvent] = []
        for action in actions:
            result = self.rules.validate(session, action)
            if result.is_valid:
                valid_actions.append(action)
            else:
                invalid_events.append(
                    self.events.emit(
                        session,
                        EventType.RULE_VALIDATION.value,
                        {
                            "actor": action.agent_id,
                            "target": action.target,
                            "action_type": action.action_type.value,
                            "is_valid": False,
                            "violation_type": result.violation_type,
                            "message": result.message,
                        },
                    )
                )
        return invalid_events + self._resolve_valid_actions(
            session, valid_actions, include_vote_events=include_vote_events
        )

    def _resolve_valid_actions(
        self,
        session,
        actions: list[AgentAction],
        *,
        include_vote_events: bool = True,
    ) -> list[GameEvent]:
        """按当前 phase 把已校验合法的动作交给 ActionResolver 结算（空子集 → 不结算）。"""
        phase = session.current_phase
        if phase == Phase.NIGHT_WEREWOLF:
            return self.resolver.resolve_wolf_nomination(session, actions)
        if phase == Phase.NIGHT_SEER:
            return self.resolver.resolve_seer_check(session, actions[0]) if actions else []
        if phase == Phase.NIGHT_WITCH:
            return self.resolver.resolve_witch_action(session, actions[0]) if actions else []
        if phase in (Phase.DAY_DISCUSSION, Phase.DAY_TIE_DISCUSSION):
            events: list[GameEvent] = []
            for action in actions:
                events.extend(self.resolver.resolve_speech(session, action))
            return events
        if phase == Phase.DAY_VOTE:
            return self.resolver.resolve_vote(
                session, actions, include_vote_events=include_vote_events
            )
        if phase == Phase.DAY_TIE_REVOTE:
            return self.resolver.resolve_tie_revote(
                session, actions, include_vote_events=include_vote_events
            )
        if phase == Phase.HUNTER_SHOOT:
            events = self.resolver.resolve_hunter_shoot(session, actions[0]) if actions else []
            self._mark_hunter_flags(session, events)
            events.extend(self._win_events(session))
            return events
        if phase == Phase.EXILE_LAST_WORDS:
            return self.resolver.resolve_last_words(session, actions[0]) if actions else []

        return []

    def emit_vote_cast(self, game_id: str, action: AgentAction) -> list[GameEvent]:
        """为实时观战提前发出单张投票记录；不改 TruthState，最终票型仍由批量结算统计。"""
        session = self.sessions.get_game(game_id)
        return self.resolver.resolve_vote_cast(session, action)

    def resolve_phase(self, game_id: str) -> list[GameEvent]:
        """处理无 actor 的系统结算阶段。Supervisor 只调用 Engine，不手写结算事件。"""
        session = self.sessions.get_game(game_id)
        phase = session.current_phase
        if phase == Phase.DAY_ANNOUNCEMENT:
            events = self._resolve_day_announcement(session)
            self._mark_hunter_flags(session, events, return_phase=Phase.DAY_DISCUSSION)
            if self._has_hunter_shoot_pending(events):
                return events
            events.extend(self._win_events(session))
            return events
        if phase == Phase.EXILE_RESOLUTION:
            events = self.resolver.resolve_exile(session)
            self._mark_hunter_flags(session, events, return_phase=Phase.EXILE_LAST_WORDS)
            if self._has_hunter_shoot_pending(events):
                return events
            events.extend(self._win_events(session))
            return events
        if phase == Phase.NO_EXILE_RESOLUTION:
            return []
        if phase == Phase.WIN_CHECK:
            if session.truth_state.round >= session.config.max_rounds:
                return [
                    self.events.emit(
                        session,
                        EventType.WIN_CHECK.value,
                        {
                            "game_over": True,
                            "winner": None,
                            "reason": "max_rounds_reached",
                        },
                    ),
                    self.events.emit(
                        session,
                        EventType.GAME_OVER.value,
                        {"winner": None, "reason": "max_rounds_reached"},
                    ),
                ]
            return self._win_events(session, emit_continue=True)
        return []

    def _resolve_day_announcement(self, session) -> list[GameEvent]:
        night = session.truth_state.night_state
        deaths: list[tuple[str, str]] = []
        if night.kill_target is not None and night.kill_target != night.saved_target:
            deaths.append((night.kill_target, DeathCause.NIGHT_KILL.value))
        if night.poison_target is not None and night.poison_target not in {pid for pid, _ in deaths}:
            deaths.append((night.poison_target, DeathCause.WITCH_POISON.value))

        events: list[GameEvent] = []
        for pid, cause in deaths:
            session.truth_state.players[pid].status = PlayerStatus.DEAD
            events.append(
                self.events.emit(
                    session,
                    EventType.DEATH_CONFIRMED.value,
                    {"target": pid, "death_cause": cause},
                )
            )
        events.append(
            self.events.emit(
                session,
                EventType.DAY_ANNOUNCEMENT.value,
                {"deaths": [{"player_id": pid, "death_cause": cause} for pid, cause in deaths]},
            )
        )
        night.wolf_nominations = {}
        night.kill_target = None
        night.saved_target = None
        night.poison_target = None
        return events

    def _mark_hunter_flags(
        self, session, events: list[GameEvent], *, return_phase: Phase | None = None
    ) -> None:
        for event in events:
            if event.event_type != EventType.DEATH_CONFIRMED:
                continue
            next_phase = self.hunter.maybe_enter_hunter_shoot(session, event)
            if next_phase == Phase.HUNTER_SHOOT:
                session.truth_state.round_state.hunter_death_cause = event.payload.get("death_cause")
                session.hunter_shoot_return_phase = return_phase
                event.payload["hunter_can_shoot"] = True

    @staticmethod
    def _has_hunter_shoot_pending(events: list[GameEvent]) -> bool:
        return any(
            event.event_type == EventType.DEATH_CONFIRMED
            and event.payload.get("hunter_can_shoot") is True
            for event in events
        )

    def _win_events(self, session, *, emit_continue: bool = False) -> list[GameEvent]:
        result = self.win.check(session)
        if not result.game_over:
            if not emit_continue:
                return []
            return [
                self.events.emit(
                    session,
                    EventType.WIN_CHECK.value,
                    {"game_over": False, "winner": None, "reason": result.reason},
                )
            ]
        return [
            self.events.emit(
                session,
                EventType.WIN_CHECK.value,
                {"game_over": True, "winner": result.winner, "reason": result.reason},
            ),
            self.events.emit(
                session,
                EventType.GAME_OVER.value,
                {"winner": result.winner, "reason": result.reason},
            ),
        ]

    def advance_phase(self, game_id: str, latest_events: list[GameEvent]) -> Phase:
        """计算并应用下一个 phase（状态变更只在 Engine 内发生）。

        WIN_CHECK → NIGHT_WEREWOLF 视为新一夜，round + 1。供 Supervisor.run_game 推进整局。
        （A 自有的内部协调方法，不属于 B/C 依赖的对外契约。）
        """
        session = self.sessions.get_game(game_id)
        current = session.truth_state.phase
        next_phase = self.phases.next_phase(session, latest_events)
        if current == Phase.WIN_CHECK and next_phase == Phase.NIGHT_WEREWOLF:
            session.truth_state.round += 1
        if current == Phase.HUNTER_SHOOT:
            session.hunter_shoot_return_phase = None
        session.truth_state.phase = next_phase
        return next_phase

    def check_win(self, game_id: str) -> WinCheckResult:
        return self.win.check(self.sessions.get_game(game_id))
