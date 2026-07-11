"""ContextWindowPolicy —— Task C8。

把 ``AgentContextDraft`` 压到 ``ContextBudgetConfig`` 内，输出 ``contracts.AgentContext``。
9 人局必须启用；单 Agent ≤ 4000 tokens。

预算默认（见 ``contracts.ContextBudgetConfig`` 与 Interface_v2_1 4.3）::

    max_input_tokens_per_agent     = 4000
    max_recent_public_events       = 20
    max_current_day_speeches_raw   = 9
    max_historical_speech_raw      = 0     # Day 2+ 历史发言原文严禁进 prompt
    max_belief_top_suspects        = 3
    max_strategy_memory_items      = 3

红线：

- ``assert estimated_tokens <= max_input_tokens_per_agent``
- ``assert historical_raw_speech_count == 0`` —— 历史轮发言原文必须先经
  ``SpeechSummarizer`` 转成 Fact Stream 短句，绝不允许原文进 prompt
- 裁剪结束后产生 ``context_assembled`` 事件（由调用方 emit）

裁剪策略：

- ``recent_public_events`` —— 保留最后 N 个（最新优先）
- ``current_round_events`` —— 不裁剪（当前 round 的事件全保留）
- ``public_events`` —— 保留最后 N 个（兜底）
- ``belief_top_suspects`` —— 取前 N 个
- ``strategy_memory`` —— 取前 N 个
- 历史 SPEECH 原文（``round < current_round``）—— **必须为 0 条**（fail-loud）

Token 估算：用 ``字符数 ÷ 4`` 做近似（英文 / 中文混合的粗估）。
真用 tokenizer（如 ``tiktoken``）属于后续优化点，第一阶段先保证额度可估。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

from contracts import AgentContext, ContextBudgetConfig, EventType

if TYPE_CHECKING:
    from context.types import AgentContextDraft


# 字符数 → token 数的粗估系数。
# 中文 1 字 ≈ 1.5 token，英文平均 1 token ≈ 4 字符；折中取 4，对中文保守。
_CHARS_PER_TOKEN_APPROX = 4

# typed 台账（claim_records / vote_records）的硬上限。
# 9p 单轮投票 9 条，3 轮 = 27 条；15 条覆盖最近 ~1.5 轮投票，给 LLM 决策的近距上下文够用。
# Claim 事件天然稀疏（每局 < 10 条），15 上限不会触顶。后续给 ContextBudgetConfig 加
# max_claim_records / max_vote_records 字段需走 contract MR。
_MAX_TYPED_RECORDS = 15

# 单条 SPEECH ``public_message`` 字符上限（A 5/26 21:14 根因诊断头号阻塞）。
# 真实 LLM 发言常达 200-500 字符，9 条 × 400 chars ≈ 900 tokens 仅 SPEECH 原文 + 同样
# 内容在 recent_public_events / public_events 重复出现 3 次，DAY_TIE_REVOTE 满载阶段
# 实测 4280 > 4000 token budget 直接 raise abort 整局。
# 280 字符 ≈ 70 token，覆盖 LLM "一条带逻辑发言"长度；超长 LLM 输出在末尾加 ``…(已截断)`` 标记。
# 后续给 ContextBudgetConfig 加 ``max_speech_message_chars`` 字段需走 contract MR。
_MAX_SPEECH_MESSAGE_CHARS = 280
_MAX_SPEECH_MESSAGE_CHARS_AGGRESSIVE = 100  # safety net 二次降级用
_TRUNCATE_SUFFIX = "…(已截断)"


class ContextBudgetExceededError(ValueError):
    """token 估算超出预算上限。"""

    def __init__(self, estimated: int, budget: int) -> None:
        super().__init__(
            f"estimated tokens {estimated} exceeds budget {budget} —— "
            f"裁剪后仍超出，调用方需要进一步精简（如减少 visible_players、"
            f"压缩 public_memory_summary）"
        )
        self.estimated = estimated
        self.budget = budget


class HistoricalSpeechLeakError(ValueError):
    """历史轮 SPEECH 原文混进了 current_round_events / recent_public_events。"""

    def __init__(self, count: int, current_round: int) -> None:
        super().__init__(
            f"detected {count} historical raw SPEECH event(s) in context "
            f"(current_round={current_round}); they must be summarized by "
            f"SpeechSummarizer first"
        )
        self.count = count
        self.current_round = current_round


class ContextWindowPolicy:
    """把 ``AgentContextDraft`` 压成 ``AgentContext``。

    跨 ``apply`` 调用线程不安全，但**累加 stats**——S7 baseline 取数需要量化裁剪压力：
    被截断的 SPEECH 条数 / progressive_degrade 触发次数 / 兜底失败抛 raise 次数。
    A 5/27 handoff §P2 要求"baseline 该量化"，对 LLM 局尤其重要——单局 mock 测不到、
    批量 LLM 一拉就现形（A 实证 v0_batch_1 平票 HUNTER_SHOOT 4114>4000）。

    用法：``ContextAssembler(window_policy=ContextWindowPolicy())``（默认 new 一个），
    跑完一局后 ``assembler._window.stats`` 读累计；批量 runner 跨局聚合时把每局的
    stats 拷出来再 reset 即可。
    """

    def __init__(self) -> None:
        self.stats: dict[str, int] = {
            "applies": 0,                        # 总 apply 次数（baseline 锚点）
            "truncated_speech_events": 0,        # 触发 SPEECH 截断的 event 总数（含 280 + aggressive 100）
            "progressive_degrade_triggered": 0,  # apply 走进 _progressive_degrade 的次数
            "budget_exceeded": 0,                # progressive 之后仍超 → raise 的次数
        }

    def apply(
        self,
        raw_context: AgentContextDraft,
        budget: ContextBudgetConfig | None = None,
    ) -> AgentContext:
        """裁剪 Draft 各 list 字段到 budget 上限，构造 AgentContext。

        Args:
            raw_context: 未裁剪的中间产物
            budget: 预算配置；None 用默认值（``ContextBudgetConfig()``）

        Raises:
            HistoricalSpeechLeakError: 历史轮 SPEECH 原文出现在事件流里
                （应由 SpeechSummarizer 压成 Fact Stream，不能原文进 prompt）
            ContextBudgetExceededError: 裁剪后估算 tokens 仍超出 max_input_tokens_per_agent
        """
        if budget is None:
            budget = ContextBudgetConfig()

        self.stats["applies"] += 1

        # 1. 历史 SPEECH 原文红线检查（fail-loud）
        self._check_historical_speech_leak(raw_context)

        # 2. 裁剪各 list
        recent_public_events = list(raw_context.recent_public_events)[
            -budget.max_recent_public_events :
        ]
        # current_round_events 不裁，全保留（当前 round 的事件 AI 全应该看）
        current_round_events = list(raw_context.current_round_events)
        # public_events 兜底裁剪（避免无限增长）
        public_events = list(raw_context.public_events)[
            -budget.max_recent_public_events :
        ]
        belief_top_suspects = list(raw_context.belief_top_suspects)[
            : budget.max_belief_top_suspects
        ]
        strategy_memory = list(raw_context.strategy_memory)[
            : budget.max_strategy_memory_items
        ]
        # typed 台账截断（v2.2 预留；ContextBudgetConfig 暂未加 max_claim_records/max_vote_records，
        # 用与 max_recent_public_events 同档的硬上限 15，避免 9p 多轮 vote/claim 把 budget 撑爆。
        # 后续给 ContextBudgetConfig 加字段时再走 contract MR）。
        claim_records = list(raw_context.claim_records)[-_MAX_TYPED_RECORDS:]
        vote_records = list(raw_context.vote_records)[-_MAX_TYPED_RECORDS:]

        # 3. current_day_speeches_raw —— 当前 round 的 SPEECH 数量上限
        speech_events = [
            ev
            for ev in current_round_events
            if _is_speech(ev)
        ]
        if len(speech_events) > budget.max_current_day_speeches_raw:
            # 保留最新 N 个 SPEECH，其他 SPEECH 移除；非 SPEECH 事件保持
            keep_speeches = set(
                id(ev) for ev in speech_events[-budget.max_current_day_speeches_raw :]
            )
            current_round_events = [
                ev
                for ev in current_round_events
                if not _is_speech(ev) or id(ev) in keep_speeches
            ]

        # 3.5 SPEECH.public_message 长度截断（A 21:14 根因：真实 LLM 单条 200-500 字符
        # 撑爆 budget；mock 局发言短测不出来）。SPEECH 在 current_round_events /
        # recent_public_events / public_events 三处重复，全部截一遍。
        current_round_events = _truncate_speech_messages(
            current_round_events, _MAX_SPEECH_MESSAGE_CHARS, stats=self.stats
        )
        recent_public_events = _truncate_speech_messages(
            recent_public_events, _MAX_SPEECH_MESSAGE_CHARS, stats=self.stats
        )
        public_events = _truncate_speech_messages(
            public_events, _MAX_SPEECH_MESSAGE_CHARS, stats=self.stats
        )

        # 4. 拼成 AgentContext
        context = AgentContext(
            game_id=raw_context.game_id,
            agent_id=raw_context.agent_id,
            role=raw_context.role,
            round=raw_context.round,
            phase=raw_context.phase,
            is_secondary_stage=raw_context.is_secondary_stage,
            secondary_stage_type=raw_context.secondary_stage_type,
            tie_candidates=list(raw_context.tie_candidates),
            previous_vote_summary=dict(raw_context.previous_vote_summary),
            compressed_context=None,
            visible_players=list(raw_context.visible_players),
            current_round_events=current_round_events,
            recent_public_events=recent_public_events,
            public_memory_summary=list(raw_context.public_memory_summary),
            public_events=public_events,
            private_events=list(raw_context.private_events),
            belief_state=dict(raw_context.belief_state),
            belief_top_suspects=belief_top_suspects,
            strategy_memory=strategy_memory,
            allowed_actions=list(raw_context.allowed_actions),
            rule_hints=dict(raw_context.rule_hints),
            claim_records=claim_records,
            vote_records=vote_records,
        )

        # 5. 估算 token 数；超预算先走渐进降级安全网（A 21:14 选项 3），仍超才 fail-loud
        estimated = self.estimate_tokens(context)
        if estimated > budget.max_input_tokens_per_agent:
            self.stats["progressive_degrade_triggered"] += 1
            context = _progressive_degrade(
                context, budget, self.estimate_tokens, stats=self.stats
            )
            estimated = self.estimate_tokens(context)
            if estimated > budget.max_input_tokens_per_agent:
                self.stats["budget_exceeded"] += 1
                raise ContextBudgetExceededError(
                    estimated, budget.max_input_tokens_per_agent
                )

        return context

    # --- token estimate -----------------------------------------------------

    def estimate_tokens(self, context: AgentContext) -> int:
        """字符数 / 4 的粗估。

        对中文略保守（中文 1 字 ≈ 1.5 token），对英文略乐观。第一阶段够用，
        v2 可换 tiktoken。
        """
        serialized = json.dumps(
            context.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
        )
        return max(1, len(serialized) // _CHARS_PER_TOKEN_APPROX)

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _check_historical_speech_leak(draft: AgentContextDraft) -> None:
        """current_round / recent_public 里不能有历史轮的 SPEECH 原文。"""
        current_round = draft.round
        leak_count = 0
        for ev in list(draft.current_round_events) + list(draft.recent_public_events):
            if _is_speech(ev) and ev.round < current_round:
                leak_count += 1
        if leak_count > 0:
            raise HistoricalSpeechLeakError(leak_count, current_round)


def _is_speech(ev) -> bool:
    """PublicEvent 是否是 SPEECH 事件。"""
    et = getattr(ev, "event_type", None)
    if et is None:
        return False
    if isinstance(et, EventType):
        return et == EventType.SPEECH
    return str(et) == EventType.SPEECH.value


def _truncate_speech_messages(
    events: list,
    max_chars: int,
    *,
    stats: dict[str, int] | None = None,
) -> list:
    """对 list 中所有 SPEECH 事件的 ``public_message`` 截断到 ``max_chars`` 字符上限。

    保留 ``actor`` / ``role_claim`` / ``claim_result`` / ``event_type`` 等元数据完整；
    只对 ``public_message`` 字符串本体做尾截 + 加 ``…(已截断)`` 标记。
    非 SPEECH 事件 / public_message 已在长度内 / 为 ``None`` 的事件原样保留（identity）。

    Args:
        events: ``PublicEvent`` 列表
        max_chars: 单条 public_message 字符上限（不含 ``…(已截断)`` 后缀）
        stats: 可选累加器，每截一条 SPEECH 对 ``stats["truncated_speech_events"]`` +1。

    Returns:
        新 list；只对真正需要截断的事件用 ``model_copy(update=...)`` 替换，其它保持
        identity 不变（便于下游 ``id(ev) in keep_speeches`` 等 set 判断仍生效，但本函数
        应在那些 set 判断之后再调用）。
    """
    truncated: list = []
    for ev in events:
        if not _is_speech(ev):
            truncated.append(ev)
            continue
        msg = getattr(ev, "public_message", None)
        if msg is None or len(msg) <= max_chars:
            truncated.append(ev)
            continue
        new_msg = msg[:max_chars] + _TRUNCATE_SUFFIX
        # PublicEvent 是 pydantic BaseModel；model_copy(update={...}) 返回新实例
        truncated.append(ev.model_copy(update={"public_message": new_msg}))
        if stats is not None:
            stats["truncated_speech_events"] += 1
    return truncated


def _progressive_degrade(
    context: AgentContext,
    budget: ContextBudgetConfig,
    estimator: Callable[[AgentContext], int],
    *,
    stats: dict[str, int] | None = None,
) -> AgentContext:
    """超预算时的渐进降级安全网（A 21:14 选项 3）。

    Step-wise + 每 step 后 re-estimate，达标即返回（A 5/27 实证：平票局到
    HUNTER_SHOOT 仍超 114 token —— v0 默认 belief/strategy/typed 全空，旧版 1-4 步
    都是 no-op；新增 5/6/7 step 砍 recent_public / public / public_memory_summary
    救场，对 HUNTER_SHOOT 决策影响最小：猎人开枪靠死亡信息 + 当天动态，跨轮
    summary 减半不致命）。

    降级顺序（先丢"对 v0 决策最弱依赖"的字段）：

    1. 丢 ``strategy_memory``（v2 才用，v0 必空）
    2. 丢 ``belief_top_suspects``（v0 不注入 belief）
    3. 丢 ``claim_records`` / ``vote_records``（typed 投影，default off 已是空）
    4. 二次激进截 SPEECH ``public_message`` 到 100 chars
    5. ``recent_public_events`` 减半（min 5）
    6. ``public_events`` 减半（min 5）
    7. ``public_memory_summary`` 减半（min 1，FactStream 跨轮历史，最后才砍）
    """

    def _under(c: AgentContext) -> bool:
        return estimator(c) <= budget.max_input_tokens_per_agent

    # Step 1-3：丢 strategy / belief / typed（v0 默认全空，但仍按字段判断兼容 v1+）
    updates: dict = {}
    if context.strategy_memory:
        updates["strategy_memory"] = []
    if context.belief_top_suspects:
        updates["belief_top_suspects"] = []
    if context.claim_records:
        updates["claim_records"] = []
    if context.vote_records:
        updates["vote_records"] = []
    if updates:
        context = context.model_copy(update=updates)
        if _under(context):
            return context

    # Step 4：二次激进截 SPEECH 到 100 chars
    speech_updates: dict = {}
    new_current = _truncate_speech_messages(
        list(context.current_round_events), _MAX_SPEECH_MESSAGE_CHARS_AGGRESSIVE, stats=stats
    )
    if any(a is not b for a, b in zip(new_current, context.current_round_events)):
        speech_updates["current_round_events"] = new_current
    new_recent = _truncate_speech_messages(
        list(context.recent_public_events), _MAX_SPEECH_MESSAGE_CHARS_AGGRESSIVE, stats=stats
    )
    if any(a is not b for a, b in zip(new_recent, context.recent_public_events)):
        speech_updates["recent_public_events"] = new_recent
    new_public = _truncate_speech_messages(
        list(context.public_events), _MAX_SPEECH_MESSAGE_CHARS_AGGRESSIVE, stats=stats
    )
    if any(a is not b for a, b in zip(new_public, context.public_events)):
        speech_updates["public_events"] = new_public
    if speech_updates:
        context = context.model_copy(update=speech_updates)
        if _under(context):
            return context

    # Step 5：recent_public_events 减半（保留最新 N 条，最少 5）
    if len(context.recent_public_events) > 5:
        new_n = max(5, len(context.recent_public_events) // 2)
        context = context.model_copy(
            update={"recent_public_events": list(context.recent_public_events)[-new_n:]}
        )
        if _under(context):
            return context

    # Step 6：public_events 减半（最少 5）
    if len(context.public_events) > 5:
        new_n = max(5, len(context.public_events) // 2)
        context = context.model_copy(
            update={"public_events": list(context.public_events)[-new_n:]}
        )
        if _under(context):
            return context

    # Step 7：public_memory_summary 减半（最少 1，FactStream 跨轮主线索）
    if len(context.public_memory_summary) > 1:
        new_n = max(1, len(context.public_memory_summary) // 2)
        context = context.model_copy(
            update={"public_memory_summary": list(context.public_memory_summary)[-new_n:]}
        )

    return context
