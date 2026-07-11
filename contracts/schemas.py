"""冻结 Schema 清单 —— 来源 Schema_v2_1.md 第 35 节「必须冻结的 Schema」。

Day 1 冻结。所有模型继承 ContractModel：
- extra="forbid"      → 禁止未声明字段偷偷混入（防契约腐烂的第一层锁）。
- protected_namespaces=() → 允许 model_name / model_id 这类字段名。
- populate_by_name=True   → 既可用字段名也可用 alias 构造。

修改任何字段前必须走变更流程（三人确认 + bump 版本 + 更新快照）。

设计取舍：
- 高度异构 / 易演进的审计 blob（payload / input_summary / metrics / rule_hints /
  compressed_context 等）用 dict[str, Any]，避免假冻结带来的频繁 churn。
- agent_version 统一收 str（短名 v0/v1/v2 与长名枚举在文档里并存，见 enums.AgentVersion 注释）。
- 未对 GameEvent/AgentAction 等加 frozen=True（运行时 TruthState 需可变）；
  实例不可变是另一层可选项，不在本次范围。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from contracts.enums import (
    ActionType,
    Camp,
    ClaimedAlignment,
    DeviationOutcome,
    EventType,
    FailureCategory,
    Phase,
    PlayerStatus,
    Role,
    Visibility,
    VisibilityLevel,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        protected_namespaces=(),
        populate_by_name=True,
    )


# --- 通用小结构 ---

class ClaimResult(ContractModel):
    target: str
    claimed_alignment: ClaimedAlignment


class Timing(ContractModel):
    mode: str = "turn_order"
    intent: str | None = None
    trigger: str | None = None


class Deviation(ContractModel):
    is_deviation_from_belief: bool = False
    belief_recommended_target: str | None = None
    selected_target: str | None = None
    deviation_type: str | None = None
    reason: str | None = None


# --- GameConfig ---

class RoleCounts(ContractModel):
    werewolf: int = 0
    seer: int = 0
    witch: int = 0
    hunter: int = 0
    villager: int = 0


class ModelConfig(ContractModel):
    provider: str | None = None
    model_display_name: str | None = None
    model_id: str | None = None
    endpoint_id: str | None = None
    temperature: float = 0.7


class GameRules(ContractModel):
    sheriff_enabled: bool = False
    witch_knows_kill_target: bool = True
    witch_can_save_and_poison_same_night: bool = False
    last_words_enabled: bool = True
    wolf_coordination_mode: str = "nomination_random_tiebreak"
    tie_break_enabled: bool = True
    tie_discussion_round_limit: int = 1
    tie_revote_target_scope: str = "tie_candidates_only"
    second_tie_result: str = "no_exile_go_night"
    hunter_enabled: bool = True
    hunter_can_shoot_after_poisoned: bool = False


class GameConfig(ContractModel):
    game_id: str
    player_count: int
    roles: RoleCounts
    max_rounds: int = 8
    mode: str = "ai_vs_ai"
    agent_version: str = "v1"
    # JSON 键为 "model_config"，但该名是 pydantic 保留字，故 python 字段名用 model_settings + alias。
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    rules: GameRules = Field(default_factory=GameRules)


# --- PlayerState / TruthState ---

class PlayerState(ContractModel):
    # player_id 在 TruthState.players 里作为 dict key，故此处可省略。
    player_id: str | None = None
    role: Role
    camp: Camp | None = None
    status: PlayerStatus = PlayerStatus.ALIVE
    public_claim: str | None = None
    vote_weight: float = 1.0


class WitchState(ContractModel):
    antidote_used: bool = False
    poison_used: bool = False


class HunterState(ContractModel):
    shot_used: bool = False
    can_shoot_after_poisoned: bool = False


class NightState(ContractModel):
    wolf_nominations: dict[str, str] = Field(default_factory=dict)
    kill_target: str | None = None
    saved_target: str | None = None
    poison_target: str | None = None


class RoundState(ContractModel):
    last_exiled_player: str | None = None
    last_words_done: bool = False
    tie_candidates: list[str] = Field(default_factory=list)
    tie_vote_round: int = 0
    is_secondary_stage: bool = False
    previous_vote_summary: dict[str, int] = Field(default_factory=dict)
    hunter_death_cause: str | None = None


class TruthState(ContractModel):
    game_id: str
    round: int = 1
    phase: Phase = Phase.INIT
    players: dict[str, PlayerState] = Field(default_factory=dict)
    witch_state: WitchState = Field(default_factory=WitchState)
    hunter_state: HunterState = Field(default_factory=HunterState)
    night_state: NightState = Field(default_factory=NightState)
    round_state: RoundState = Field(default_factory=RoundState)


# --- 事件视图 ---

class VisiblePlayer(ContractModel):
    player_id: str
    status: PlayerStatus
    public_claim: str | None = None


class PublicEvent(ContractModel):
    event_id: str
    round: int
    phase: Phase
    event_type: EventType
    actor: str | None = None
    target: str | None = None
    public_message: str | None = None
    role_claim: Role | None = None
    claim_result: ClaimResult | None = None
    summary: str | None = None


class PrivateEvent(ContractModel):
    event_type: EventType
    # 事件所属轮次：消费方据此区分"当前轮 vs 历史"私密信息（女巫取 max round 拿当晚刀口；
    # 预言家忽略 round、读全量查验史；狼队友 roster 跨轮共用）。可选、默认 None，旧数据不受影响。
    round: int | None = None
    target: str | None = None
    result: str | None = None
    teammates: list[str] | None = None
    visibility: Visibility | None = None


class GameEvent(ContractModel):
    event_id: str
    game_id: str
    round: int
    phase: Phase
    event_type: EventType
    actor: str | None = None
    target: str | None = None
    visibility: Visibility = Visibility.PUBLIC
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


# --- 结构化台账（事件投影；机读，与字符串 FactStream 并存）---
# 不变量：以下投影记录必须能从 GameEvent 流重建，不另立真相（见审计稿三层数据模型）。

class ClaimRecord(ContractModel):
    """跳身份/查杀台账：从 SPEECH（含 claim_result）事件派生。"""
    record_id: str
    game_id: str
    round: int
    phase: Phase
    actor: str
    claimed_role: Role | None = None
    claim_target: str | None = None
    claimed_alignment: ClaimedAlignment | None = None
    is_counter_claim: bool = False
    source_event_id: str | None = None
    derived_by: str | None = None


class VoteRecord(ContractModel):
    """投票台账：从 VOTE_CAST 事件派生（跨轮票矩阵）。"""
    record_id: str
    game_id: str
    round: int
    phase: Phase
    stage: str = "primary"  # primary / revote
    voter: str
    target: str | None = None
    is_revote: bool = False
    is_tie_candidate_vote: bool = False
    source_event_id: str | None = None
    derived_by: str | None = None


# --- AgentContext / AgentAction ---

class AgentContext(ContractModel):
    game_id: str
    agent_id: str
    role: Role
    round: int
    phase: Phase
    is_secondary_stage: bool = False
    secondary_stage_type: str | None = None
    tie_candidates: list[str] = Field(default_factory=list)
    previous_vote_summary: dict[str, int] = Field(default_factory=dict)
    compressed_context: dict[str, Any] | None = None
    visible_players: list[VisiblePlayer] = Field(default_factory=list)
    current_round_events: list[PublicEvent] = Field(default_factory=list)
    recent_public_events: list[PublicEvent] = Field(default_factory=list)
    public_memory_summary: list[Any] = Field(default_factory=list)
    public_events: list[PublicEvent] = Field(default_factory=list)
    private_events: list[PrivateEvent] = Field(default_factory=list)
    # v0 不注入；v1 注入。具体 belief 结构见 BeliefState.beliefs。
    belief_state: dict[str, Any] = Field(default_factory=dict)
    belief_top_suspects: list[Any] = Field(default_factory=list)
    strategy_memory: list[Any] = Field(default_factory=list)
    allowed_actions: list[ActionType] = Field(default_factory=list)
    rule_hints: dict[str, Any] = Field(default_factory=dict)
    # 机读台账（v2.2 预留；C 从事件派生注入，与字符串 FactStream 并存，供 9 人策略稳定使用）。
    claim_records: list[ClaimRecord] = Field(default_factory=list)
    vote_records: list[VoteRecord] = Field(default_factory=list)


class AgentAction(ContractModel):
    game_id: str
    agent_id: str
    role: Role
    phase: Phase
    action_type: ActionType
    target: str | None = None
    public_message: str | None = None
    role_claim: Role | None = None
    claim_result: ClaimResult | None = None
    timing: Timing | None = None
    reason_summary: str | None = None
    confidence: float | None = None
    belief_used: dict[str, Any] | None = None
    deviation: Deviation | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- Belief ---

class RoleBelief(ContractModel):
    werewolf: float = 0.0
    seer: float = 0.0
    witch: float = 0.0
    hunter: float = 0.0
    villager: float = 0.0
    locked: bool = False
    lock_reason: str | None = None


class BeliefState(ContractModel):
    game_id: str
    agent_id: str
    round: int | None = None
    phase: Phase | None = None
    is_shadow: bool = False
    beliefs: dict[str, RoleBelief] = Field(default_factory=dict)
    last_updated_event_id: str | None = None


class DeviationEvent(ContractModel):
    event_type: EventType = EventType.BELIEF_DEVIATION
    game_id: str | None = None
    round: int | None = None
    agent_id: str
    phase: Phase
    belief_recommended_target: str | None = None
    selected_target: str | None = None
    deviation_type: str | None = None
    reason: str | None = None
    outcome: DeviationOutcome | None = None


# --- 上下文预算 / 摘要 ---

class ContextBudgetConfig(ContractModel):
    max_input_tokens_per_agent: int = 4000
    max_recent_public_events: int = 20
    max_current_day_speeches_raw: int = 9
    max_historical_speech_raw: int = 0
    max_belief_top_suspects: int = 3
    max_strategy_memory_items: int = 3


class FactStreamSummary(ContractModel):
    event_type: str = "fact_stream_summary"
    round: int
    facts: list[str] = Field(default_factory=list)


class PlayerFactSummary(ContractModel):
    round: int
    player_fact_summary: dict[str, list[str]] = Field(default_factory=dict)


# --- 调优证据链 ---

class StrategyMemoryItem(ContractModel):
    memory_id: str | None = None
    role: Role
    lesson: str
    trigger_condition: str | None = None
    source_game_id: str | None = None
    confidence: float | None = None
    created_from: str | None = None
    usage_count: int = 0


class FewShotCase(ContractModel):
    situation: str
    expected_action: str


class PromptVersion(ContractModel):
    prompt_version_id: str
    role: Role
    agent_version: str
    model_name: str | None = None
    created_at: str | None = None
    based_on: str | None = None
    design_goal: str | None = None
    key_strategy_rules: list[str] = Field(default_factory=list)
    few_shot_cases: list[FewShotCase] = Field(default_factory=list)
    known_risks: list[str] = Field(default_factory=list)
    # v2.2 预留：self-construct 溯源——这版 prompt 是从哪批对局学来的。
    # 形如 {"training_batch_id": ..., "game_ids": [...], "game_count": N}。
    derived_from: dict[str, Any] | None = None


class AgentDecisionTrace(ContractModel):
    trace_id: str
    game_id: str
    round: int
    phase: Phase
    agent_id: str
    role: Role
    agent_version: str
    prompt_version_id: str | None = None
    model_name: str | None = None
    input_summary: dict[str, Any] = Field(default_factory=dict)
    decision_output: dict[str, Any] = Field(default_factory=dict)
    decision_quality_flags: dict[str, Any] = Field(default_factory=dict)
    # v2.2 预留（全 additive；旧 decision_quality_flags(dict) 留一版，迁移后再收紧）。
    context_ref: str | None = None  # → ContextSnapshot.context_id，忠实角色复盘
    typed_decision_quality_flags: DecisionQualityFlags | None = None
    run_config_snapshot_id: str | None = None
    schema_version: str = "1.0"


class EvidenceEvent(ContractModel):
    event_id: str
    description: str


class BadCaseReport(ContractModel):
    bad_case_id: str
    game_id: str
    agent_id: str
    role: Role
    agent_version: str
    prompt_version_id: str | None = None
    phase: Phase | None = None
    case_type: str
    severity: str | None = None
    problem: str
    evidence_events: list[EvidenceEvent] = Field(default_factory=list)
    root_cause: str | None = None
    suggested_fix: str | None = None
    affected_metrics: dict[str, Any] = Field(default_factory=dict)
    status: str | None = None
    fixed_by_prompt_version: str | None = None
    # v2.2 预留：多归因（失败常非单因）+ 来源类型（防合成数据污染）。
    primary_failure_category: FailureCategory | None = None
    contributing_factors: list[FailureCategory] = Field(default_factory=list)
    source_type: str | None = None  # mock_game/llm_game/human_review/external_ai_generated/...
    schema_version: str = "1.0"


class PromptChange(ContractModel):
    type: str
    content: str


class AgentTuningTrace(ContractModel):
    tuning_trace_id: str
    role: Role
    from_prompt_version: str
    to_prompt_version: str
    trigger_bad_cases: list[str] = Field(default_factory=list)
    change_summary: str | None = None
    prompt_changes: list[PromptChange] = Field(default_factory=list)
    before_metrics: dict[str, Any] = Field(default_factory=dict)
    after_metrics: dict[str, Any] = Field(default_factory=dict)
    tradeoff: str | None = None


# --- 评测 / Leaderboard / Replay ---

class PerAgentMetric(ContractModel):
    model_name: str | None = None
    agent_version: str | None = None
    prompt_version_id: str | None = None
    role: Role | None = None
    camp: Camp | None = None
    survived: bool | None = None
    vote_accuracy: float | None = None
    fallback_count: int | None = None
    belief_override_count: int | None = None
    harmful_override_count: int | None = None
    final_belief_accuracy: float | None = None


class KeyTurningPoint(ContractModel):
    round: int
    phase: Phase | str
    description: str


class EvaluationReport(ContractModel):
    game_id: str
    winner: str | None = None
    rounds: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    per_agent_metrics: dict[str, PerAgentMetric] = Field(default_factory=dict)
    key_turning_points: list[KeyTurningPoint] = Field(default_factory=list)
    # v2.2 预留：分层胜负/失败归因（game/camp/agent/role）。
    outcome_attribution: list[OutcomeAttribution] = Field(default_factory=list)


class LeaderboardRow(ContractModel):
    model_name: str | None = None
    agent_version: str | None = None
    prompt_version_id: str | None = None
    role: Role | None = None
    camp: Camp | None = None
    matchup: str | None = None
    win_rate: float | None = None
    vote_accuracy: float | None = None
    fallback_rate: float | None = None
    harmful_override_rate: float | None = None
    wolf_survival_rate: float | None = None


class ReplayData(ContractModel):
    game_id: str
    players: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[Any] = Field(default_factory=list)
    events: list[GameEvent] = Field(default_factory=list)
    belief_curves: list[Any] = Field(default_factory=list)
    deviation_points: list[Any] = Field(default_factory=list)
    bad_cases: list[Any] = Field(default_factory=list)
    evaluation_summary: dict[str, Any] = Field(default_factory=dict)
    # v2.2 预留 typed 化（加法；旧 players/belief_curves 保留一版，不破 C 现有 replay/UI）。
    typed_players: list[ReplayPlayer] = Field(default_factory=list)
    typed_belief_curves: list[BeliefCurvePoint] = Field(default_factory=list)


# --- API 响应 ---

class CreateGameResponse(ContractModel):
    game_id: str
    status: str


class StepGameResponse(ContractModel):
    game_id: str
    phase: Phase | str
    events: list[GameEvent] = Field(default_factory=list)


class RunGameResponse(ContractModel):
    game_id: str
    status: str
    winner: str | None = None


class LeaderboardResponse(ContractModel):
    rows: list[LeaderboardRow] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# v2.2 契约预留（9P + Observability + Learning）
# 全部 additive：独立新 schema / 可选字段。未用到时即空，9 人后只填不改。
# 高不确定项（TimelineItem / StrategyInsight / RoleStrategyVersion）暂不纳入，造时再加。
# ops 遥测（SystemMetricSample）不进契约，走日志/metrics。
# ---------------------------------------------------------------------------

# --- 复盘 ---

class ReplayPlayer(ContractModel):
    """上帝视角真身（仅 GAME_OVER 后生成；可见性=post_game_replay_visible，绝不进 AgentContext）。

    死因须可从 GameEvent 派生：夜刀→killer_camp=werewolf；女巫毒/猎枪→相邻事件取 actor；
    放逐→vote summary。保留 death_source_event_id 锁住来源（派生不变量）。
    """
    player_id: str
    role: Role
    camp: Camp | None = None
    final_status: PlayerStatus = PlayerStatus.ALIVE
    survived: bool = True
    death_round: int | None = None
    death_phase: Phase | None = None
    death_cause: str | None = None
    death_source_event_id: str | None = None
    killer_agent_id: str | None = None
    killer_camp: Camp | None = None


class ContextSnapshot(ContractModel):
    """某 agent 某回合实际收到的 AgentContext 忠实快照（可见性=evaluator/debug，不进 AgentContext）。"""
    context_id: str
    game_id: str
    agent_id: str
    round: int
    phase: Phase
    agent_version: str | None = None
    prompt_version_id: str | None = None
    agent_context_json: dict[str, Any] = Field(default_factory=dict)
    rendered_prompt_ref: str | None = None  # bad case 时保存喂给 LLM 的最终 prompt 引用
    created_at: str | None = None
    schema_version: str = "1.0"


# --- Bayes 信念过程审计 ---

class BeliefUpdateDelta(ContractModel):
    """单条信念变化（哪个事件把谁的哪种角色概率改了多少、哪条规则、是否锁定）。"""
    target_player_id: str
    role: Role
    prob_before: float = 0.0
    delta: float = 0.0
    prob_after: float = 0.0
    rule_id: str | None = None
    reason: str | None = None
    was_locked: bool = False


class BeliefUpdateBatch(ContractModel):
    """一个 trigger_event 触发的一批 delta（一事件可影响多人多角色）。

    未触发变化时也显式记录（no_update_reason），不能用"没记录"表示"没变化"。
    """
    belief_update_id: str
    game_id: str
    agent_id: str
    round: int | None = None
    phase: Phase | None = None
    trigger_event_id: str | None = None
    deltas: list[BeliefUpdateDelta] = Field(default_factory=list)
    no_update_reason: str | None = None
    created_at: str | None = None
    schema_version: str = "1.0"


class BeliefCurvePoint(ContractModel):
    """信念曲线采样点（从 BeliefState 快照派生，给前端 belief curve）。"""
    round: int
    phase: Phase | None = None
    agent_id: str
    target_player_id: str
    werewolf_prob: float = 0.0
    derived_by: str | None = None


# --- 学习 / 失败归因 ---

class DecisionQualityFlags(ContractModel):
    """决策质量标签（学习器要的明确标签，取代模糊 dict）。"""
    illegal_action: bool = False
    fallback_used: bool = False
    role_leaked: bool = False
    cot_leaked: bool = False
    belief_override: bool = False
    harmful_override: bool = False
    deviation_taken: bool = False


class OutcomeAttribution(ContractModel):
    """分层胜负/失败归因：game/camp/agent/role。"""
    scope: str = "game"  # game / camp / agent / role
    camp: Camp | None = None
    agent_id: str | None = None
    role: Role | None = None
    description: str = ""
    key_event_ids: list[str] = Field(default_factory=list)


# --- 可复现 / 压测 ---

class RunConfigSnapshot(ContractModel):
    """一局/一批的完整运行配置快照——解决"为什么这次和上次不一样"。"""
    contract_version: str | None = None
    rules_version: str | None = None
    belief_rules_version: str | None = None
    game_config_id: str | None = None
    agent_version: str | None = None
    prompt_version_id: str | None = None
    strategy_profile_id: str | None = None
    model_provider: str | None = None
    model_id: str | None = None
    endpoint_id: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    timeout_seconds: float | None = None
    retry_policy_id: str | None = None
    fallback_policy_id: str | None = None
    belief_update_policy_id: str | None = None
    normalization_method: str | None = None
    seed: int | None = None
    created_at: str | None = None


class GameRunResult(ContractModel):
    """单局运行结果——失败定位三件套：error_phase / error_actor / error_type。"""
    game_id: str
    status: str = "completed"  # completed / failed / cancelled / timeout
    winner: str | None = None
    rounds: int | None = None
    runtime_ms: float | None = None
    error_type: str | None = None
    error_phase: Phase | None = None
    error_actor: str | None = None
    error_message: str | None = None
    run_config_snapshot: RunConfigSnapshot | None = None
    schema_version: str = "1.0"


class BatchRunReport(ContractModel):
    """批量压测报告——failed_game_ids/representative_failed_runs 让失败可点回。"""
    total: int = 0
    completed: int = 0
    failed: int = 0
    avg_runtime_ms: float | None = None
    winner_distribution: dict[str, int] = Field(default_factory=dict)
    error_count: int = 0
    failed_game_ids: list[str] = Field(default_factory=list)
    representative_failed_runs: list[GameRunResult] = Field(default_factory=list)
    run_config_snapshot: RunConfigSnapshot | None = None
    schema_version: str = "1.0"


class RunStatus(ContractModel):
    """实时运行状态（并发压测 + 前端等待态）。"""
    game_id: str
    status: str = "queued"  # queued/running/waiting_for_llm/resolving/finished/failed/cancelled
    queue_position: int | None = None
    active_games: int | None = None
    max_concurrency: int | None = None
    current_phase: Phase | None = None
    current_actor: str | None = None
    retry_count: int = 0
    fallback_count: int = 0
    progress: float | None = None


# 解析对后定义 schema 的前向引用（`from __future__ import annotations` 下需显式 rebuild）。
AgentDecisionTrace.model_rebuild()
EvaluationReport.model_rebuild()
ReplayData.model_rebuild()
