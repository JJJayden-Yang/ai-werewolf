"""RuleValidator —— Task A3。

校验一个 AgentAction 在当前 session 下是否合法。非法 action 不进入 ActionResolver。
进入这里的 action 已被 ActionCanonicalizer 规范化为标准 action_type。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from contracts.enums import ActionType, DeathCause, Phase, PlayerStatus, Role
from game_core.phase_controller import PhaseController
from game_core.types import ValidationResult

if TYPE_CHECKING:
    from contracts.schemas import AgentAction

    from game_core.types import GameSession


class RuleValidator:
    def __init__(self, phase_controller: PhaseController | None = None) -> None:
        self._phases = phase_controller or PhaseController()

    def validate(self, session: GameSession, action: AgentAction) -> ValidationResult:
        """通用校验：
        - action_type 在当前 phase 的 allowed_actions 内；
        - actor 存活且为当前 phase 的 required actor；
        - target 存在、存活、非自己；
        - 狼人不刀队友；预言家不查自己；女巫解药/毒药不重复使用；
        - 遗言阶段只有被放逐玩家能发言。

        预留红线：
        - HUNTER_SHOOT：actor 是死去猎人、未开过枪、非女巫毒死，target 为存活非自己或 pass；
        - DAY_TIE_REVOTE：action_type 必为 vote 且 target ∈ tie_candidates。
        """
        if action.game_id != session.game_id:
            return self._invalid("game_id_mismatch", "action does not belong to this game")
        if action.phase != session.current_phase:
            return self._invalid("phase_mismatch", "action phase is not current phase")

        players = session.truth_state.players
        actor = players.get(action.agent_id)
        if actor is None:
            return self._invalid("actor_not_found", "actor does not exist")
        if action.role != actor.role:
            return self._invalid("actor_role_mismatch", "action role does not match actor role")

        phase = session.current_phase
        allowed = self.allowed_actions(phase)
        if action.action_type not in allowed:
            return self._invalid("action_type_not_allowed", "action type is not allowed in phase")

        if phase == Phase.HUNTER_SHOOT:
            return self._validate_hunter_shoot(session, action)

        if phase == Phase.EXILE_LAST_WORDS:
            return self._validate_last_words(session, action)

        if actor.status != PlayerStatus.ALIVE:
            return self._invalid("actor_not_alive", "actor is not alive")

        required = self._phases.get_required_actors(session, phase)
        if action.agent_id not in required:
            return self._invalid("actor_not_required", "actor is not required in current phase")

        if action.action_type == ActionType.SPEAK:
            return self._valid()
        if action.action_type == ActionType.SKIP:
            return self._validate_skip(action)
        if action.action_type == ActionType.NIGHT_KILL_NOMINATE:
            return self._validate_night_kill_nominate(session, action)
        if action.action_type == ActionType.CHECK:
            return self._validate_check(session, action)
        if action.action_type == ActionType.SAVE:
            return self._validate_save(session, action)
        if action.action_type == ActionType.POISON:
            return self._validate_poison(session, action)
        if action.action_type == ActionType.VOTE:
            return self._validate_vote(session, action)

        return self._invalid("unsupported_action_type", "unsupported action type")

    @staticmethod
    def allowed_actions(phase: Phase) -> set[ActionType]:
        """某 phase 允许的标准 action 集合 —— 全系统唯一真相源。

        跨模块共享接口：C 的 ContextAssembler / VisibilityRuleSpec 装配
        AgentContext.allowed_actions 时**调用这里**，禁止自行复制本逻辑（避免 DRY 漂移）。
        返回 phase 级允许集；按 agent 的更细收窄（如女巫两药用完）由调用方叠加。
        """
        return {
            Phase.NIGHT_WEREWOLF: {ActionType.NIGHT_KILL_NOMINATE},
            Phase.NIGHT_SEER: {ActionType.CHECK},
            Phase.NIGHT_WITCH: {ActionType.SAVE, ActionType.POISON, ActionType.SKIP},
            Phase.DAY_DISCUSSION: {ActionType.SPEAK},
            Phase.DAY_VOTE: {ActionType.VOTE},
            Phase.DAY_TIE_DISCUSSION: {ActionType.SPEAK},
            Phase.DAY_TIE_REVOTE: {ActionType.VOTE},
            Phase.EXILE_LAST_WORDS: {ActionType.SPEAK},
            Phase.HUNTER_SHOOT: {ActionType.HUNTER_SHOOT},
        }.get(phase, set())

    def _validate_night_kill_nominate(
        self, session: GameSession, action: AgentAction
    ) -> ValidationResult:
        target = self._target_player(session, action, required=True)
        if not target.is_valid:
            return target
        target_player = session.truth_state.players[action.target]
        if target_player.role == Role.WEREWOLF:
            return self._invalid("wolf_cannot_kill_teammate", "werewolf cannot nominate a wolf")
        return self._valid()

    def _validate_check(self, session: GameSession, action: AgentAction) -> ValidationResult:
        # MVP 允许预言家重复查验；是否禁止重复查验属于策略/历史记录问题，
        # 后续若要收紧，需要由 private_events 或专门状态记录 checked targets。
        target = self._target_player(session, action, required=True)
        if not target.is_valid:
            return target
        if session.truth_state.players[action.agent_id].role != Role.SEER:
            return self._invalid("actor_not_seer", "only seer can check")
        return self._valid()

    def _validate_save(self, session: GameSession, action: AgentAction) -> ValidationResult:
        if session.truth_state.players[action.agent_id].role != Role.WITCH:
            return self._invalid("actor_not_witch", "only witch can save")
        if session.truth_state.witch_state.antidote_used:
            return self._invalid("antidote_already_used", "antidote has already been used")
        if (
            session.truth_state.night_state.poison_target is not None
            and not session.config.rules.witch_can_save_and_poison_same_night
        ):
            return self._invalid(
                "save_and_poison_same_night_forbidden",
                "witch cannot save and poison in the same night",
            )
        if session.truth_state.night_state.kill_target is None:
            return self._invalid("no_kill_target_to_save", "there is no night kill target")
        # 女巫仅第一夜可自救（标准规则）；之后刀口是自己只能 skip。结算侧 saved_target==kill_target 即不死。
        allow_self = session.truth_state.round == 1
        target = self._target_player(session, action, required=True, allow_self=allow_self)
        if not target.is_valid:
            return target
        if action.target != session.truth_state.night_state.kill_target:
            return self._invalid("invalid_save_target", "witch can only save the night kill target")
        return self._valid()

    def _validate_poison(self, session: GameSession, action: AgentAction) -> ValidationResult:
        if session.truth_state.players[action.agent_id].role != Role.WITCH:
            return self._invalid("actor_not_witch", "only witch can poison")
        if session.truth_state.witch_state.poison_used:
            return self._invalid("poison_already_used", "poison has already been used")
        if (
            session.truth_state.night_state.saved_target is not None
            and not session.config.rules.witch_can_save_and_poison_same_night
        ):
            return self._invalid(
                "save_and_poison_same_night_forbidden",
                "witch cannot save and poison in the same night",
            )
        return self._target_player(session, action, required=True)

    def _validate_vote(self, session: GameSession, action: AgentAction) -> ValidationResult:
        target = self._target_player(session, action, required=True)
        if not target.is_valid:
            return target
        if session.current_phase == Phase.DAY_TIE_REVOTE:
            if action.target not in session.truth_state.round_state.tie_candidates:
                return self._invalid(
                    "target_not_in_tie_candidates",
                    "tie revote target must be one of tie candidates",
                )
        return self._valid()

    def _validate_skip(self, action: AgentAction) -> ValidationResult:
        if action.target is not None:
            return self._invalid("skip_target_must_be_none", "skip cannot have a target")
        return self._valid()

    def _validate_last_words(self, session: GameSession, action: AgentAction) -> ValidationResult:
        last_exiled = session.truth_state.round_state.last_exiled_player
        if last_exiled is None:
            return self._invalid("no_last_words_actor", "there is no exiled player for last words")
        if action.agent_id != last_exiled:
            return self._invalid("actor_not_last_exiled", "only last exiled player can speak")
        if session.truth_state.round_state.last_words_done:
            return self._invalid("last_words_already_done", "last words already done")
        if action.target is not None:
            return self._invalid("speak_target_must_be_none", "speak cannot have a target")
        return self._valid()

    def _validate_hunter_shoot(
        self, session: GameSession, action: AgentAction
    ) -> ValidationResult:
        players = session.truth_state.players
        actor = players[action.agent_id]
        if actor.role != Role.HUNTER:
            return self._invalid("actor_not_hunter", "only hunter can shoot")
        if actor.status != PlayerStatus.DEAD:
            return self._invalid("hunter_not_dead", "hunter shoot actor must be dead")
        if session.truth_state.hunter_state.shot_used:
            return self._invalid("hunter_shot_already_used", "hunter shot has already been used")
        if session.truth_state.round_state.hunter_death_cause == DeathCause.WITCH_POISON.value:
            return self._invalid("hunter_poisoned_cannot_shoot", "poisoned hunter cannot shoot")
        if action.target is None:
            return self._valid()
        return self._target_player(session, action, required=True)

    def _target_player(
        self, session: GameSession, action: AgentAction, *, required: bool, allow_self: bool = False
    ) -> ValidationResult:
        if action.target is None:
            if required:
                return self._invalid("target_required", "action requires a target")
            return self._valid()
        if action.target not in session.truth_state.players:
            return self._invalid("target_not_found", "target does not exist")
        if action.target == action.agent_id and not allow_self:
            return self._invalid("target_self", "actor cannot target self")
        if session.truth_state.players[action.target].status != PlayerStatus.ALIVE:
            return self._invalid("target_not_alive", "target is not alive")
        return self._valid()

    @staticmethod
    def _valid() -> ValidationResult:
        return ValidationResult(is_valid=True)

    @staticmethod
    def _invalid(violation_type: str, message: str) -> ValidationResult:
        return ValidationResult(is_valid=False, violation_type=violation_type, message=message)
