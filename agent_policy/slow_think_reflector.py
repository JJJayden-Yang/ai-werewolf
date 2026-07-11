"""System2 慢思反思器（M4）。

在关键决策点前，用一次 LLM 调用**重估每个存活玩家的狼人可疑度**，把结论以 log-odds
增量混进观察者当前 belief（System1 由规则维护，System2 在此之上做有理由的再评估）。

红线：
- **信息隔离**：反思 prompt 只用传入的 ``AgentContext``（已是 agent 可见、经可见性过滤的视图，
  物理上不含 ``TruthState`` / role_map）。绝不读真身。
- **不泄漏**：反思只更新 belief 概率（数字），落进 belief_store；推理文本留在本反思器的
  ``reflections`` 里供观测/展示，**绝不**写进任何会注入其他 agent 的 context 字段。
- **不崩局**：LLM / JSON 解析任何失败 → 返回当前 belief 不变，记一次 error。
- ``locked`` 项（seer 硬查验 / 自身身份 / 狼队友）不被慢思改动。

与 fast/slow 的衔接（复审 finding2 修正）：``reflect`` 是**纯变换**——读入当前 belief +
context，返回 enriched belief，**不自行落盘**。由 Supervisor 把返回值写回 belief_store 并重建
context，所以本反思器不持有 belief_store。System1 后续事件更新从 enriched 继续累积。
"""

from __future__ import annotations

import json
from math import log
from typing import TYPE_CHECKING, Any

from contracts import BeliefState, PlayerStatus, Role
from agent_policy.factorized_belief import apply_log_odds_update

if TYPE_CHECKING:
    from contracts import AgentContext, Phase

_EPS = 1e-6


def _logit(p: float) -> float:
    p = min(1.0 - _EPS, max(_EPS, float(p)))
    return log(p / (1.0 - p))


def _default_reflect_phases() -> frozenset:
    from contracts import Phase

    return frozenset({Phase.DAY_VOTE})


_SYSTEM_PROMPT = (
    "你是社交推理游戏（狼人杀）中的一名玩家，只能看到不完整的公开信息。"
    "请基于给定的可见信息，重新评估每个**存活**玩家是狼人的可疑度、"
    "预言家可能性、女巫可能性，以及你对该判断的置信度（均为0~1）。"
    "seer_likelihood/witch_likelihood 表示该玩家是预言家/女巫的可能性，"
    "应基于跳身份、报查、行为一致性等可见线索。"
    "只依据发言/查验声明/投票等公开线索推理，不要编造你看不到的信息。"
    "严格只输出 JSON，格式："
    '{"assessments":[{"player_id":"P1","werewolf_suspicion":0.0,'
    '"seer_likelihood":0.0,"witch_likelihood":0.0,"confidence":1.0}],'
    '"self_reasoning":"一句话总体判断"}'
)


class LLMSlowThinkReflector:
    """实现 ``SlowThinkPolicy``：决策点前的 LLM belief 反思。"""

    def __init__(
        self,
        provider: Any,
        *,
        reflect_phases: "frozenset | None" = None,
        gain: float = 0.5,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        max_reflections: int = 8,
        model_config: dict | None = None,
    ) -> None:
        self._provider = provider
        self._reflect_phases = (
            frozenset(reflect_phases) if reflect_phases is not None else _default_reflect_phases()
        )
        self._gain = float(gain)
        self._max_reflections = int(max_reflections)
        self._model_config = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            **(model_config or {}),
        }
        # 可观测：反思次数 / 失败次数 / 实际应用次数；推理留底（不进他人 context）。
        self.stats: dict[str, int] = {
            "reflections": 0,
            "reflect_errors": 0,
            "reflect_llm_errors": 0,
            "reflect_parse_errors": 0,
            "applied": 0,
        }
        self.reflections: list[dict] = []

    # --- SlowThinkPolicy 接口 ---

    def should_reflect(self, game_id: str, phase: "Phase", round: int | None) -> bool:
        return (
            phase in self._reflect_phases
            and self.stats["reflections"] < self._max_reflections
        )

    async def reflect(
        self,
        game_id: str,
        agent_id: str,
        belief_state: BeliefState,
        context_view: "AgentContext",
    ) -> BeliefState:
        # belief_state 是 Supervisor 传入的当前 real belief（纯变换：不读不写 store）。
        # belief lane 没数据（空 beliefs）→ 无可 enrich，原样返回。
        current = belief_state
        if not current.beliefs:
            return current

        messages = self._build_messages(context_view, current)
        self.stats["reflections"] += 1
        try:
            response = await self._provider.generate(messages, dict(self._model_config))
        except Exception as exc:  # noqa: BLE001 —— LLM 失败绝不崩局
            self._record_error("reflect_llm_errors")
            self.reflections.append(
                {
                    "agent_id": agent_id,
                    "round": context_view.round,
                    "error": f"llm:{type(exc).__name__}: {exc}"[:160],
                }
            )
            return current

        try:
            assessments, reasoning = self._parse(response.raw_output)
        except Exception as exc:  # noqa: BLE001 —— JSON 解析失败绝不崩局
            self._record_error("reflect_parse_errors")
            self.reflections.append(
                {
                    "agent_id": agent_id,
                    "round": context_view.round,
                    "error": f"parse:{type(exc).__name__}: {exc}"[:160],
                }
            )
            return current

        enriched = self._apply(current, assessments, context_view, agent_id)
        self.stats["applied"] += 1
        self.reflections.append(
            {
                "agent_id": agent_id,
                "round": context_view.round,
                "assessments": assessments,
                "reasoning": reasoning[:300],
            }
        )
        return enriched

    # --- 内部 ---

    def _record_error(self, key: str) -> None:
        self.stats[key] += 1
        self.stats["reflect_errors"] = (
            self.stats["reflect_llm_errors"] + self.stats["reflect_parse_errors"]
        )

    def _alive_ids(self, context_view: "AgentContext") -> set[str]:
        return {
            vp.player_id
            for vp in context_view.visible_players
            if vp.status == PlayerStatus.ALIVE
        }

    def _build_messages(
        self, context_view: "AgentContext", current: BeliefState
    ) -> list[dict]:
        # 只放 AgentContext 里 agent 可见的信息（无 TruthState）。
        visible = [
            {"player_id": vp.player_id, "claim": vp.public_claim}
            for vp in context_view.visible_players
            if vp.status == PlayerStatus.ALIVE
        ]
        claims = [
            {"actor": c.actor, "claimed_role": getattr(c.claimed_role, "value", None),
             "target": c.claim_target}
            for c in context_view.claim_records
        ]
        votes = [
            {"voter": v.voter, "target": v.target, "round": v.round}
            for v in context_view.vote_records
        ]
        payload = {
            "your_role": context_view.role.value,
            "round": context_view.round,
            "phase": context_view.phase.value,
            "alive_players": visible,
            "claims": claims[-20:],
            "votes": votes[-20:],
            "current_top_suspects": context_view.belief_top_suspects[:5],
        }
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    @staticmethod
    def _clamped_float(value: Any) -> float | None:
        if not isinstance(value, (int, float)):  # noqa: UP038
            return None
        return min(1.0, max(0.0, float(value)))

    @classmethod
    def _parse(cls, raw: str) -> tuple[dict[str, dict[str, float | None]], str]:
        text = (raw or "").strip()
        if text.startswith("```"):
            # 去掉 ```json ... ``` 围栏
            text = text.strip("`")
            if text[:4].lower() == "json":
                text = text[4:]
            text = text.strip()
        data = json.loads(text)
        assessments: dict[str, dict[str, float | None]] = {}
        for item in data.get("assessments", []):
            if not isinstance(item, dict):
                continue
            pid = item.get("player_id")
            if not isinstance(pid, str):
                continue
            werewolf = cls._clamped_float(item.get("werewolf_suspicion"))
            seer = cls._clamped_float(item.get("seer_likelihood"))
            witch = cls._clamped_float(item.get("witch_likelihood"))
            confidence = cls._clamped_float(item.get("confidence"))
            if werewolf is None and seer is None and witch is None:
                continue
            assessments[pid] = {
                "werewolf": werewolf,
                "seer": seer,
                "witch": witch,
                "confidence": 1.0 if confidence is None else confidence,
            }
        return assessments, str(data.get("self_reasoning", ""))

    def _apply(
        self,
        current: BeliefState,
        assessments: dict[str, dict[str, float | None]],
        context_view: "AgentContext",
        self_id: str,
    ) -> BeliefState:
        alive = self._alive_ids(context_view)
        beliefs = {pid: rb.model_copy(deep=True) for pid, rb in current.beliefs.items()}
        role_fields = (
            ("werewolf", Role.WEREWOLF),
            ("seer", Role.SEER),
            ("witch", Role.WITCH),
        )
        for pid, assessment in assessments.items():
            if pid == self_id or pid not in alive:
                continue
            role_belief = beliefs.get(pid)
            if role_belief is None or role_belief.locked:
                continue
            eff_gain = self._gain * float(assessment.get("confidence") or 0.0)
            updated = role_belief
            # 把 belief 沿 log-odds 朝 LLM 的多维判断移动；locked 由 update 自身保护。
            for field, role in role_fields:
                target = assessment.get(field)
                if target is None:
                    continue
                delta = eff_gain * (_logit(float(target)) - _logit(getattr(updated, field)))
                updated = apply_log_odds_update(
                    updated, target_role=role, log_odds_delta=delta
                )
            beliefs[pid] = updated
        return BeliefState(
            game_id=current.game_id,
            agent_id=current.agent_id,
            round=context_view.round,
            phase=context_view.phase,
            is_shadow=current.is_shadow,
            beliefs=beliefs,
            last_updated_event_id=current.last_updated_event_id,
        )
