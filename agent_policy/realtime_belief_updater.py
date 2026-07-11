"""Rule-based realtime belief updater for mock / v1 belief experiments.

The updater consumes already-emitted ``GameEvent`` objects and writes one
agent-local ``BeliefState`` per observer. It deliberately does not change
contracts: information isolation is enforced by event visibility and the
observer's own role as projected by ``VisibilityRuleSpec``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import yaml

from contracts import (
    BeliefCurvePoint,
    BeliefState,
    BeliefUpdateBatch,
    BeliefUpdateDelta,
    ClaimedAlignment,
    DeathCause,
    EventType,
    PlayerStatus,
    Role,
    RoleBelief,
)

from agent_policy.belief_math import apply_delta_and_normalize
from agent_policy.factorized_belief import (
    FactorizedEvidence,
    apply_factorized_evidence,
    source_credibility_from_belief,
)
from agent_policy.tom_features import seer_claim_result_consistency
from context.visibility_rules import VisibilityRuleSpec
from stores.exceptions import BeliefStateNotFoundError

if TYPE_CHECKING:
    from contracts.schemas import GameEvent
    from context.protocols import GameSessionProvider
    from stores.belief_observability_store import BeliefObservabilityStore
    from stores.belief_state_store import BeliefStateStore
    from stores.event_store import EventStore


_UNIFORM_ROLE_BELIEF = RoleBelief(
    werewolf=0.2,
    seer=0.2,
    witch=0.2,
    hunter=0.2,
    villager=0.2,
)

_ROLE_TO_FIELD: dict[Role, str] = {
    Role.WEREWOLF: "werewolf",
    Role.SEER: "seer",
    Role.WITCH: "witch",
    Role.HUNTER: "hunter",
    Role.VILLAGER: "villager",
}
_FIELD_TO_ROLE: dict[str, Role] = {field: role for role, field in _ROLE_TO_FIELD.items()}

_UNIQUE_CLAIM_ROLES = {Role.SEER, Role.WITCH, Role.HUNTER}
_DEFAULT_RULES_PATH = Path(__file__).with_name("belief_rules_v1.yaml")
_DEFAULT_V2_RULES_PATH = Path(__file__).with_name("belief_rules_v2.yaml")
_VALID_BELIEF_KERNELS = {"additive_v1", "factorized_v2"}


class RuleBasedRealtimeBeliefUpdater:
    """Update per-agent BeliefState from visible events.

    This class is intentionally small and deterministic. It is meant to make
    mock agents actually consume ``belief_top_suspects`` before LLM agents use
    the same signal.
    """

    def __init__(
        self,
        *,
        event_store: "EventStore",
        belief_store: "BeliefStateStore",
        session_provider: "GameSessionProvider",
        visibility: VisibilityRuleSpec | None = None,
        is_shadow: bool = False,
        observability_store: "BeliefObservabilityStore | None" = None,
        rules_path: str | Path | None = None,
        rules: dict[str, Any] | None = None,
        belief_kernel: str = "additive_v1",
    ) -> None:
        if belief_kernel not in _VALID_BELIEF_KERNELS:
            raise ValueError(
                f"belief_kernel must be one of {sorted(_VALID_BELIEF_KERNELS)}, "
                f"got {belief_kernel!r}"
            )
        self._events = event_store
        self._beliefs = belief_store
        self._sessions = session_provider
        self._visibility = visibility or VisibilityRuleSpec()
        self._is_shadow = is_shadow
        self._observability = observability_store
        self._belief_kernel = belief_kernel
        self._rules = rules if rules is not None else _load_rules(
            rules_path or (_DEFAULT_V2_RULES_PATH if belief_kernel == "factorized_v2" else None)
        )
        self._credibility_lambda = float(self._rules.get("credibility_lambda", 0.5))
        self._unique_role_claimers: dict[tuple[str, str, Role], list[str]] = {}
        # v1.最终(真人1校准):为新规则增量跟踪派生事实(Markov,不重扫事件流)。
        # 谁曾跳过预言家(全场公开事实) —— 用于 claimed_seer_night_killed / survives_night / vote_against_seer。
        self._claimed_seer_actors: dict[str, set[str]] = {}
        # 谁曾被某跳预言家公开查杀(claim_check_werewolf) —— 用于 vote_follow_claimed_seer_black。
        self._claimed_check_werewolf_targets: dict[str, set[str]] = {}
        # 全场死者集合(任何死因) —— 防 survives_night 对死者错误回加狼嫌(Codex P1 bug)。
        self._dead_players_by_game: dict[str, set[str]] = {}
        self._seer_claim_results: dict[tuple[str, str], dict[str, dict[str, str]]] = {}

    def update(self, game_id: str, event_id: str) -> None:
        event = self._events.get(event_id)
        session = self._sessions.get_session(game_id)
        observers = self._visible_observers(event, session)
        for observer_id, observer_role in observers:
            belief = self._load_or_init_belief(game_id, observer_id, event)
            updated = self._apply_event(
                belief,
                event,
                observer_id=observer_id,
                observer_role=observer_role,
            )
            if updated is not None:
                self._beliefs.save(updated)
                self._record_observability(belief, updated, event, observer_id)

    def _visible_observers(self, event: "GameEvent", session) -> list[tuple[str, Role]]:
        alive_ids = [
            player.player_id
            for player in self._visibility.visible_players(session, event.actor or "")
            if player.status == PlayerStatus.ALIVE
        ]
        observers: list[tuple[str, Role]] = []
        for agent_id in alive_ids:
            role = self._visibility.observer_role(session, agent_id)
            if role is None:
                continue
            if self._event_visible_to_agent(event, session, agent_id):
                observers.append((agent_id, role))
        return observers

    def _event_visible_to_agent(self, event: "GameEvent", session, agent_id: str) -> bool:
        events = [event]
        return bool(
            self._visibility.visible_public_events(events, session, agent_id)
            or self._visibility.visible_private_events(events, session, agent_id)
        )

    def _load_or_init_belief(
        self, game_id: str, agent_id: str, event: "GameEvent"
    ) -> BeliefState:
        try:
            return self._beliefs.get(game_id, agent_id, is_shadow=self._is_shadow)
        except BeliefStateNotFoundError:
            return self._initial_belief(game_id, agent_id, event)

    def _initial_belief(
        self, game_id: str, agent_id: str, event: "GameEvent"
    ) -> BeliefState:
        beliefs: dict[str, RoleBelief] = {}
        session = self._sessions.get_session(game_id)
        observer_role = self._visibility.observer_role(session, agent_id)
        for player in self._visibility.visible_players(session, agent_id):
            pid = player.player_id
            beliefs[pid] = _UNIFORM_ROLE_BELIEF.model_copy(deep=True)
            if pid == agent_id and observer_role is not None:
                beliefs[pid] = _locked_role_belief(observer_role, "own_role_known")
        return BeliefState(
            game_id=game_id,
            agent_id=agent_id,
            round=event.round,
            phase=event.phase,
            is_shadow=self._is_shadow,
            beliefs=beliefs,
            last_updated_event_id=event.event_id,
        )

    def _apply_event(
        self,
        belief: BeliefState,
        event: "GameEvent",
        *,
        observer_id: str,
        observer_role: Role,
    ) -> BeliefState | None:
        event_type = _as_event_type(event.event_type)
        updated = belief.model_copy(deep=True)
        updated.round = event.round
        updated.phase = event.phase
        updated.last_updated_event_id = event.event_id

        if event_type == EventType.SEER_CHECK_RESULT:
            self._apply_seer_check(updated, event)
        elif event_type == EventType.WOLF_NOMINATION:
            self._apply_wolf_teammates(updated, event, observer_id)
        elif event_type == EventType.SPEECH:
            self._apply_speech(updated, event, observer_id, observer_role)
        elif event_type == EventType.DEATH_CONFIRMED:
            self._apply_death_confirmed(updated, event)
        elif event_type == EventType.DAY_ANNOUNCEMENT:
            self._apply_day_announcement(updated, event)
        elif event_type == EventType.VOTE_CAST:
            self._apply_vote_cast(updated, event)
        elif event_type == EventType.HUNTER_SHOT:
            self._apply_hunter_shot(updated, event)
        else:
            return updated
        return updated

    def _apply_seer_check(self, belief: BeliefState, event: "GameEvent") -> None:
        if not event.target:
            return
        result = str(event.payload.get("result", ""))
        if result == ClaimedAlignment.WEREWOLF.value:
            belief.beliefs[event.target] = _role_belief_from_rule(
                self._rule("private_confirmations", "seer_check_werewolf", "target")
            )
        elif result == ClaimedAlignment.VILLAGER.value:
            belief.beliefs[event.target] = _role_belief_from_rule(
                self._rule("private_confirmations", "seer_check_villager", "target")
            )

    def _apply_wolf_teammates(
        self, belief: BeliefState, event: "GameEvent", observer_id: str
    ) -> None:
        teammates = event.payload.get("teammates")
        if not isinstance(teammates, list):
            return
        for teammate in teammates:
            if teammate == observer_id:
                continue
            if isinstance(teammate, str):
                belief.beliefs[teammate] = _role_belief_from_rule(
                    self._rule("private_confirmations", "wolf_teammate", "target")
                )

    def _apply_speech(
        self,
        belief: BeliefState,
        event: "GameEvent",
        observer_id: str,
        observer_role: Role,
    ) -> None:
        role_claim = _role_from_payload(event.payload.get("role_claim"))
        if event.actor and role_claim is not None:
            self._apply_role_claim(
                belief,
                event,
                observer_id=observer_id,
                observer_role=observer_role,
                claimed_role=role_claim,
            )
        claim_result = event.payload.get("claim_result")
        if isinstance(claim_result, dict):
            self._apply_claim_result(belief, claim_result, event=event, observer_id=observer_id)

    def _apply_role_claim(
        self,
        belief: BeliefState,
        event: "GameEvent",
        *,
        observer_id: str,
        observer_role: Role,
        claimed_role: Role,
    ) -> None:
        if not event.actor:
            return
        # v1.最终(真人1校准):增量跟踪跳预言家者 → 后续 claimed_seer_night_killed /
        # survives_night / vote_against_claimed_seer / vote_follow_claimed_seer_black 用。
        if claimed_role == Role.SEER:
            self._claimed_seer_actors.setdefault(event.game_id, set()).add(event.actor)
        if (
            claimed_role in _UNIQUE_CLAIM_ROLES
            and observer_role == claimed_role
            and event.actor != observer_id
        ):
            # This is grounded in the observer's private role certainty: if I am
            # the unique role, another claimant is suspicious regardless of how
            # much I trusted that claimant before the speech.
            self._apply_delta(
                belief,
                event.actor,
                self._own_role_counterclaim_delta(claimed_role),
            )
        else:
            self._apply_delta(
                belief,
                event.actor,
                self._role_claim_delta(claimed_role),
                source_player_id=event.actor,
                claimed_role=claimed_role,
            )

        if claimed_role in _UNIQUE_CLAIM_ROLES:
            self._apply_unique_role_counter_claim(
                belief,
                event,
                observer_id=observer_id,
                claimed_role=claimed_role,
            )

    def _apply_unique_role_counter_claim(
        self,
        belief: BeliefState,
        event: "GameEvent",
        *,
        observer_id: str,
        claimed_role: Role,
    ) -> None:
        if not event.actor:
            return
        key = (event.game_id, observer_id, claimed_role)
        prior_claimers = [
            actor for actor in self._unique_role_claimers.get(key, []) if actor != event.actor
        ]
        if not prior_claimers:
            self._remember_unique_role_claim(key, event.actor)
            return
        self._apply_delta(
            belief,
            event.actor,
            self._claimed_role_delta(
                self._rule(
                    "conflicting_claims",
                    "second_claim_same_unique_role",
                    "current_claimer_delta",
                ),
                claimed_role,
            ),
            source_player_id=event.actor,
            claimed_role=claimed_role,
        )
        for claimant in prior_claimers:
            self._apply_delta(
                belief,
                claimant,
                self._claimed_role_delta(
                    self._rule(
                        "conflicting_claims",
                        "second_claim_same_unique_role",
                        "previous_claimers_delta",
                    ),
                    claimed_role,
                ),
                source_player_id=event.actor,
                claimed_role=claimed_role,
            )
        self._remember_unique_role_claim(key, event.actor)

    def _remember_unique_role_claim(
        self, key: tuple[str, str, Role], actor: str
    ) -> None:
        claimers = self._unique_role_claimers.setdefault(key, [])
        if actor not in claimers:
            claimers.append(actor)

    def _apply_claim_result(
        self,
        belief: BeliefState,
        claim_result: dict[str, Any],
        *,
        event: "GameEvent | None" = None,
        observer_id: str,
    ) -> None:
        target = claim_result.get("target")
        alignment = claim_result.get("claimed_alignment")
        if not isinstance(target, str):
            return
        if event is not None and event.actor:
            self._apply_claim_result_actor_signal(
                belief,
                event,
                target,
                alignment,
                observer_id=observer_id,
            )
        if alignment == ClaimedAlignment.WEREWOLF.value:
            self._apply_delta(
                belief,
                target,
                self._rule("public_claims", "claim_check_werewolf", "target_delta"),
                source_player_id=event.actor if event is not None else None,
                claimed_role=Role.SEER,
            )
            # v1.最终(真人1校准):若发查杀的发言人是跳预言家的人,记录被查杀目标
            # → 后续 vote_follow_claimed_seer_black 用。Markov:状态里增量。
            if event is not None and event.actor:
                if event.actor in self._claimed_seer_actors.get(event.game_id, set()):
                    self._claimed_check_werewolf_targets.setdefault(
                        event.game_id, set()
                    ).add(target)
        elif alignment == ClaimedAlignment.VILLAGER.value:
            self._apply_delta(
                belief,
                target,
                self._rule("public_claims", "claim_check_villager", "target_delta"),
                source_player_id=event.actor if event is not None else None,
                claimed_role=Role.SEER,
            )

    def _apply_claim_result_actor_signal(
        self,
        belief: BeliefState,
        event: "GameEvent",
        target: str,
        alignment: Any,
        observer_id: str,
    ) -> None:
        if self._belief_kernel != "factorized_v2" or not event.actor:
            return
        if alignment not in {ClaimedAlignment.WEREWOLF.value, ClaimedAlignment.VILLAGER.value}:
            return

        self._apply_delta(
            belief,
            event.actor,
            self._rule("public_claims", "claim_result_actor", "actor_delta"),
            source_player_id=event.actor,
            claimed_role=Role.SEER,
        )
        if event.actor in self._claimed_seer_actors.get(event.game_id, set()):
            history = self._seer_claim_results.setdefault((event.game_id, observer_id), {})
            evidence = seer_claim_result_consistency(
                actor_id=event.actor,
                target_id=target,
                claimed_alignment=str(alignment),
                prior_claims=history,
            )
            self._apply_tom_evidence(belief, evidence, source_player_id=event.actor)
            history.setdefault(event.actor, {})[target] = str(alignment)

    def _apply_tom_evidence(
        self,
        belief: BeliefState,
        evidence,
        *,
        source_player_id: str | None,
    ) -> None:
        delta: dict[str, float] = {}
        if evidence.seer_delta:
            delta["seer"] = evidence.seer_delta
        if evidence.werewolf_delta:
            delta["werewolf"] = evidence.werewolf_delta
        if delta:
            self._apply_delta(
                belief,
                evidence.actor_id,
                delta,
                source_player_id=source_player_id,
                claimed_role=Role.SEER,
            )

    def _apply_death_confirmed(self, belief: BeliefState, event: "GameEvent") -> None:
        if event.target is None:
            return
        # 增量记录死者(任何死因) —— 用于 survives_night 跳过历史死者(Codex P1)。
        self._dead_players_by_game.setdefault(event.game_id, set()).add(event.target)
        if event.payload.get("death_cause") != DeathCause.NIGHT_KILL.value:
            return
        self._apply_delta(
            belief,
            event.target,
            self._rule("public_events", "night_killed", "target_delta"),
        )
        # v1.最终(真人1校准 排序 #1):若死者曾跳过预言家 → 强真预 + 强非狼。
        if event.target in self._claimed_seer_actors.get(event.game_id, set()):
            self._apply_delta(
                belief,
                event.target,
                self._rule("public_events", "claimed_seer_night_killed", "target_delta"),
            )

    def _apply_day_announcement(self, belief: BeliefState, event: "GameEvent") -> None:
        """v1.最终(真人1校准):公开预言家当晚没死 → 轻狼嫌(+0.45)。

        触发:DAY_ANNOUNCEMENT 时,对每个跳过预言家但**不在本次 deaths 列表**的玩家
        应用 claimed_seer_survives_night。考虑女巫救人:被救的预言家也会触发(他真的"没死"),
        这种情况下狼嫌轻 +0.45 也是合理的(真预可能被狼刀但被救,普通玩家不知差异)。
        """
        deaths_payload = event.payload.get("deaths")
        if not isinstance(deaths_payload, list):
            return
        dead_ids = set()
        for d in deaths_payload:
            if isinstance(d, dict) and d.get("player_id"):
                dead_ids.add(d["player_id"])
                # 顺手把本次 deaths 记入全局死者(防 DEATH_CONFIRMED 顺序乱)。
                self._dead_players_by_game.setdefault(event.game_id, set()).add(d["player_id"])
        # 全场已死(任何过往死因)→ 也排除(Codex P1:避免对历史已死的跳预者错加 survives_night)。
        all_dead = self._dead_players_by_game.get(event.game_id, set())
        delta = self._rule("public_events", "claimed_seer_survives_night", "target_delta")
        for claimer in self._claimed_seer_actors.get(event.game_id, set()):
            if claimer in dead_ids or claimer in all_dead:
                continue
            self._apply_delta(belief, claimer, delta)

    def _apply_vote_cast(self, belief: BeliefState, event: "GameEvent") -> None:
        """v1.最终(真人1校准):带语境的投票轻量更新。

        - vote_against_claimed_seer(★ 排序 #3):投票 target 曾跳过预言家 → 投票者 +0.35 狼嫌
        - vote_follow_claimed_seer_black(★ 排序 #6):投票 target 曾被某跳预言家公开查杀 → 投票者 -0.35 狼嫌
        - raw vote_cast(无上述语境):不更新(规则中 scope=post_game_only)。
        两个语境若同时命中,加法叠加(投票者投了一个『既跳了预言家又被另一个预言家查杀』的人,
        这种少见情形,叠加偏中性,合理)。
        """
        if not event.actor or not event.target:
            return
        gid = event.game_id
        # 排序 #3:投跳预言家者
        if event.target in self._claimed_seer_actors.get(gid, set()):
            self._apply_delta(
                belief,
                event.actor,
                self._rule("public_events", "vote_against_claimed_seer", "voter_delta"),
                source_player_id=event.actor,
            )
        # 排序 #6:跟预言家查杀投票
        if event.target in self._claimed_check_werewolf_targets.get(gid, set()):
            self._apply_delta(
                belief,
                event.actor,
                self._rule("public_events", "vote_follow_claimed_seer_black", "voter_delta"),
                source_player_id=event.actor,
            )

    def _apply_hunter_shot(self, belief: BeliefState, event: "GameEvent") -> None:
        """v1.最终(真人1校准 排序 #9):猎人开枪带人 → 被带的轻狼嫌 +0.15。

        猎人也可能打错,所以**只轻判**(不完全保守的『只记录』,也不强判)。
        猎人 pass(target=None)不触发。
        """
        if self._belief_kernel == "factorized_v2" and event.actor:
            belief.beliefs[event.actor] = _locked_role_belief(
                Role.HUNTER,
                "hunter_shot_confirmed",
            )
        if not event.target:
            return
        # 防御:payload 里 pass=True 时也跳过(双重保险)
        if event.payload.get("pass") is True:
            return
        self._apply_delta(
            belief,
            event.target,
            self._rule("public_events", "hunter_shot_target", "target_delta"),
            source_player_id=event.actor,
            claimed_role=Role.HUNTER,
        )

    def _role_claim_delta(self, claimed_role: Role) -> dict[str, float]:
        rule_name = f"claim_{claimed_role.value}"
        return _float_delta(self._rule("public_claims", rule_name, "actor_delta"))

    def _own_role_counterclaim_delta(self, claimed_role: Role) -> dict[str, float]:
        return self._claimed_role_delta(
            self._rule(
                "conflicting_claims",
                "claim_observers_own_unique_role",
                "actor_delta",
            ),
            claimed_role,
        )

    @staticmethod
    def _claimed_role_delta(rule: dict[str, Any], claimed_role: Role) -> dict[str, float]:
        delta: dict[str, float] = {}
        for key, value in rule.items():
            field = _ROLE_TO_FIELD[claimed_role] if key == "claimed_role" else key
            delta[field] = float(value)
        return delta

    def _rule(self, *path: str) -> dict[str, Any]:
        current: Any = self._rules
        for key in path:
            current = current[key]
        if not isinstance(current, dict):
            raise TypeError(f"belief rule {'.'.join(path)} must be a mapping")
        return current

    def _apply_delta(
        self,
        belief: BeliefState,
        player_id: str,
        delta: dict[str, float],
        *,
        source_player_id: str | None = None,
        claimed_role: Role | None = None,
    ) -> None:
        current = belief.beliefs.get(player_id, _UNIFORM_ROLE_BELIEF)
        if self._belief_kernel == "additive_v1":
            belief.beliefs[player_id] = apply_delta_and_normalize(current, delta)
            return

        credibility = 1.0
        if source_player_id is not None:
            source_belief = belief.beliefs.get(source_player_id, _UNIFORM_ROLE_BELIEF)
            credibility = source_credibility_from_belief(
                source_belief,
                claimed_role=claimed_role,
            )

        updated = current
        for field, value in delta.items():
            role = _FIELD_TO_ROLE.get(field)
            if role is None:
                continue
            weight = float(value)
            if weight == 0.0:
                continue
            updated = apply_factorized_evidence(
                updated,
                FactorizedEvidence(
                    target_role=role,
                    base_weight=abs(weight),
                    direction=1 if weight > 0 else -1,
                    source_credibility=credibility,
                    credibility_lambda=self._credibility_lambda,
                ),
            )
        belief.beliefs[player_id] = updated

    def _record_observability(
        self,
        before: BeliefState,
        after: BeliefState,
        event: "GameEvent",
        observer_id: str,
    ) -> None:
        if self._observability is None:
            return
        event_type = _as_event_type(event.event_type).value
        deltas = _belief_update_deltas(before, after, reason=event_type)
        self._observability.append_update(
            BeliefUpdateBatch(
                belief_update_id=f"belief_update_{uuid4().hex}",
                game_id=event.game_id,
                agent_id=observer_id,
                round=event.round,
                phase=event.phase,
                trigger_event_id=event.event_id,
                deltas=deltas,
                no_update_reason=None if deltas else "no_probability_change",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        self._observability.append_curve_points(
            event.game_id,
            _belief_curve_points(after, round_=event.round, phase=event.phase),
        )


def _locked_role_belief(role: Role, reason: str) -> RoleBelief:
    values = {field: 0.0 for field in _ROLE_TO_FIELD.values()}
    values[_ROLE_TO_FIELD[role]] = 1.0
    return RoleBelief(**values, locked=True, lock_reason=reason)


def _role_belief_from_rule(rule: dict[str, Any]) -> RoleBelief:
    return RoleBelief(**rule)


def _belief_update_deltas(
    before: BeliefState, after: BeliefState, *, reason: str
) -> list[BeliefUpdateDelta]:
    deltas: list[BeliefUpdateDelta] = []
    player_ids = sorted(set(before.beliefs) | set(after.beliefs))
    for player_id in player_ids:
        before_belief = before.beliefs.get(player_id, _UNIFORM_ROLE_BELIEF)
        after_belief = after.beliefs.get(player_id, _UNIFORM_ROLE_BELIEF)
        for field, role in _FIELD_TO_ROLE.items():
            prob_before = float(getattr(before_belief, field))
            prob_after = float(getattr(after_belief, field))
            delta = prob_after - prob_before
            if abs(delta) < 1e-12:
                continue
            deltas.append(
                BeliefUpdateDelta(
                    target_player_id=player_id,
                    role=role,
                    prob_before=prob_before,
                    delta=delta,
                    prob_after=prob_after,
                    rule_id=reason,
                    reason=reason,
                    was_locked=before_belief.locked,
                )
            )
    return deltas


def _belief_curve_points(
    belief: BeliefState, *, round_: int, phase: Phase
) -> list[BeliefCurvePoint]:
    return [
        BeliefCurvePoint(
            round=round_,
            phase=phase,
            agent_id=belief.agent_id,
            target_player_id=target_player_id,
            werewolf_prob=float(role_belief.werewolf),
            derived_by="realtime_belief_updater",
        )
        for target_player_id, role_belief in sorted(belief.beliefs.items())
    ]


def _float_delta(rule: dict[str, Any]) -> dict[str, float]:
    return {key: float(value) for key, value in rule.items()}


def _load_rules(rules_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(rules_path) if rules_path is not None else _DEFAULT_RULES_PATH
    with path.open(encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    if not isinstance(loaded, dict):
        raise ValueError(f"belief rules file must contain a mapping: {path}")
    return loaded


def _role_from_payload(value: Any) -> Role | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, Role) else Role(str(value))
    except ValueError:
        return None


def _as_event_type(value: Any) -> EventType:
    return value if isinstance(value, EventType) else EventType(str(value))
