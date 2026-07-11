"""冻结枚举清单 —— 来源 Schema_v2_1.md 第 1 节。

Day 1 冻结。修改任何枚举值前必须走变更流程（三人确认 + 版本号 + 更新快照）。
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    WEREWOLF = "werewolf"
    SEER = "seer"
    WITCH = "witch"
    HUNTER = "hunter"
    VILLAGER = "villager"


class Camp(str, Enum):
    WEREWOLF = "werewolf"
    VILLAGER = "villager"


class PlayerStatus(str, Enum):
    ALIVE = "alive"
    DEAD = "dead"


class Phase(str, Enum):
    INIT = "INIT"
    ROLE_ASSIGNMENT = "ROLE_ASSIGNMENT"
    NIGHT_WEREWOLF = "NIGHT_WEREWOLF"
    NIGHT_SEER = "NIGHT_SEER"
    NIGHT_WITCH = "NIGHT_WITCH"
    DAY_ANNOUNCEMENT = "DAY_ANNOUNCEMENT"
    HUNTER_SHOOT = "HUNTER_SHOOT"
    DAY_DISCUSSION = "DAY_DISCUSSION"
    DAY_VOTE = "DAY_VOTE"
    DAY_TIE_DISCUSSION = "DAY_TIE_DISCUSSION"
    DAY_TIE_REVOTE = "DAY_TIE_REVOTE"
    EXILE_RESOLUTION = "EXILE_RESOLUTION"
    NO_EXILE_RESOLUTION = "NO_EXILE_RESOLUTION"
    EXILE_LAST_WORDS = "EXILE_LAST_WORDS"
    WIN_CHECK = "WIN_CHECK"
    GAME_OVER = "GAME_OVER"


class ActionType(str, Enum):
    SPEAK = "speak"
    VOTE = "vote"
    NIGHT_KILL_NOMINATE = "night_kill_nominate"
    CHECK = "check"
    SAVE = "save"
    POISON = "poison"
    HUNTER_SHOOT = "hunter_shoot"
    SKIP = "skip"


class EventType(str, Enum):
    PHASE_STARTED = "phase_started"
    ROLE_ASSIGNED = "role_assigned"
    WOLF_NOMINATION = "wolf_nomination"
    NIGHT_KILL_ANNOUNCED = "night_kill_announced"
    SEER_CHECK_RESULT = "seer_check_result"
    WITCH_KILL_TARGET_INFO = "witch_kill_target_info"
    WITCH_SAVE = "witch_save"
    WITCH_POISON = "witch_poison"
    DAY_ANNOUNCEMENT = "day_announcement"
    SPEECH = "speech"
    VOTE_CAST = "vote_cast"
    TIE_DETECTED = "tie_detected"
    NO_EXILE_DUE_TO_SECOND_TIE = "no_exile_due_to_second_tie"
    EXILE = "exile"
    LAST_WORDS = "last_words"
    HUNTER_SHOT = "hunter_shot"
    DEATH_CONFIRMED = "death_confirmed"
    WIN_CHECK = "win_check"
    GAME_OVER = "game_over"
    AGENT_ACTION = "agent_action"
    LLM_CALL = "llm_call"
    RULE_VALIDATION = "rule_validation"
    FALLBACK_USED = "fallback_used"
    BELIEF_SNAPSHOT = "belief_snapshot"
    BELIEF_DEVIATION = "belief_deviation"
    ACTION_CANONICALIZED = "action_canonicalized"
    ACTION_GUARD_TRIGGERED = "action_guard_triggered"
    CONTEXT_ASSEMBLED = "context_assembled"
    PROMPT_VERSION_USED = "prompt_version_used"
    AGENT_DECISION_TRACE = "agent_decision_trace"
    FACT_STREAM_SUMMARY = "fact_stream_summary"


class AgentVersion(str, Enum):
    """注意：第 1.7 节枚举用长名，但全文 JSON 示例（GameConfig / PromptVersion /
    per_agent_metrics 等）都用短名 v0/v1/v2。schema 里 agent_version 字段统一收 str，
    不强制本枚举，待全队对齐后再决定。
    """

    V0_FREE_LLM = "v0_free_llm"
    V1_BELIEF_GUIDED = "v1_belief_guided"
    V2_STRATEGY_MEMORY_OPTIONAL = "v2_strategy_memory_optional"


class ClaimedAlignment(str, Enum):
    WEREWOLF = "werewolf"
    VILLAGER = "villager"
    UNKNOWN = "unknown"


class Visibility(str, Enum):
    PUBLIC = "public"
    PRIVATE_TO_SEER = "private_to_seer"
    PRIVATE_TO_WITCH = "private_to_witch"
    PRIVATE_TO_WOLVES = "private_to_wolves"


class DeathCause(str, Enum):
    NIGHT_KILL = "night_kill"
    EXILE = "exile"
    HUNTER_SHOT = "hunter_shot"
    WITCH_POISON = "witch_poison"


class DeviationOutcome(str, Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    NEUTRAL = "neutral"


class VisibilityLevel(str, Enum):
    """数据对象的可见性分级（防前端/Agent 提前看到不该看的）。

    注意：与 `Visibility`（事件对 Agent 的可见性）不同，本枚举标的是「审计/复盘/学习/UI」
    各视角能看到哪些数据对象。runtime_agent_visible 最严，绝不含真身。
    """

    RUNTIME_AGENT_VISIBLE = "runtime_agent_visible"
    POST_GAME_REPLAY_VISIBLE = "post_game_replay_visible"
    EVALUATOR_VISIBLE = "evaluator_visible"
    DEBUG_ONLY = "debug_only"
    ADMIN_ONLY = "admin_only"


class FailureCategory(str, Enum):
    """失败分类 taxonomy（四层）—— BadCase 可学习的前提：先有明确分类，再谈归因。"""

    # 系统层
    SYSTEM_ERROR = "system_error"
    PHASE_STUCK = "phase_stuck"
    ILLEGAL_ACTION_PENETRATION = "illegal_action_penetration"
    CONTEXT_LEAK = "context_leak"
    MISSING_PRIVATE_INFO = "missing_private_info"
    # Agent 输出层
    JSON_PARSE_ERROR = "json_parse_error"
    CANONICALIZER_ERROR = "canonicalizer_error"
    FALLBACK_OVERUSED = "fallback_overused"
    ROLE_LEAK = "role_leak"
    META_AI_LEAK = "meta_ai_leak"
    COT_LEAK = "cot_leak"
    CONTEXT_TRUNCATION_ERROR = "context_truncation_error"
    TIMEOUT_OR_RATE_LIMIT = "timeout_or_rate_limit"
    # 策略层
    BAD_VOTE = "bad_vote"
    LATE_CLAIM = "late_claim"
    FALSE_CLAIM_FAILED = "false_claim_failed"
    OVER_DEFENSE = "over_defense"
    HARMFUL_BUSSING = "harmful_bussing"
    MISSED_SAVE = "missed_save"
    WRONG_POISON = "wrong_poison"
    HUNTER_WRONG_SHOT = "hunter_wrong_shot"
    # 推理/信念层
    BELIEF_UPDATE_ERROR = "belief_update_error"
    HARMFUL_BELIEF_OVERRIDE = "harmful_belief_override"
