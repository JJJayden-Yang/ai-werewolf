"""LLMAgent —— v0 纯 LLM agent：把 PromptTemplateLoader + LLMProvider + ActionParser
+ ActionCanonicalizer 串成 Supervisor 可直接注入的 ``act(context) -> dict``。

这是 v0 的"最后一公里"：Supervisor 通过 ``AgentRuntime`` Protocol
（``async def act(context: dict) -> dict``，见 supervisor/protocols.py）驱动 agent。
本类实现该接口，内部链路与 finalPlan 标准调用链一致：

    AgentContext(dict) → load_for_role(role) → render(ctx) → provider.generate(messages)
    → ActionParser.parse(raw, ctx) → ActionCanonicalizer.canonicalize → AgentAction(dict)

错误处理：LLM 调用失败 / 解析失败**不抛、不崩局**——返回空 dict，让
``Supervisor.validate_or_fallback`` 走安全兜底（被记成 fallback 事件，保持可观测）。
失败计数累计到 ``self.stats``，供 run harness 事后读取定位（ok / parse_error / llm_error）。

S7 决策 trace 持久化：构造时可传 ``trace_store``（opt-in，默认 None 不落盘），
传入后每次 ``act`` 在三个出口（ok / parse_error / llm_error）都 append 一条
``AgentDecisionTrace`` 到 store，含 ``prompt_version_id`` / ``model_name`` /
``input_summary`` / ``decision_output`` / ``decision_quality_flags``
（canonicalize_triggered + parse_error + llm_error + retry_count）。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from contracts import AgentContext
from contracts.schemas import AgentDecisionTrace

from agent_runtime.action_canonicalizer import ActionCanonicalizer
from agent_runtime.action_parser import ActionParser
from agent_runtime.exceptions import AgentRuntimeError, ParseError
from agent_runtime.prompt_template_loader import PromptTemplateLoader

if TYPE_CHECKING:
    from agent_policy.advanced_strategy.strategy_selector import StrategySelector
    from agent_runtime.llm_provider import LLMProvider
    from agent_runtime.types import LLMResponse, PromptTemplate
    from contracts import AgentAction
    from stores.trace_store import TraceStore


class LLMAgent:
    """v0 纯 LLM agent（满足 ``supervisor.protocols.AgentRuntime``）。

    与 ``RoleStrategyMockAgent`` 同位：对外只暴露 ``act(context: dict) -> dict``，
    不接触 TruthState / GameSession / Store。provider 由调用方注入（真实 ArkLLMProvider
    或测试用 FakeLLMProvider 皆可），便于本地 smoke 与 CI 解耦。
    """

    def __init__(
        self,
        provider: "LLMProvider",
        *,
        loader: PromptTemplateLoader | None = None,
        parser: ActionParser | None = None,
        canonicalizer: ActionCanonicalizer | None = None,
        model_config: dict | None = None,
        template_name: str = "v0_free_llm",
        soul_id: str | None = None,
        strategy_selector: "StrategySelector | None" = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        trace_store: "TraceStore | None" = None,
        agent_version: str = "v0",
    ) -> None:
        self._provider = provider
        # soul_id（phase2 全局人格）：只在 caller 没显式传 loader 时用于构造默认 loader。
        # 若显式传了 loader，则尊重该 loader 自带的 soul_id（不覆盖），避免双重真相源。
        self._loader = loader or PromptTemplateLoader(soul_id=soul_id)
        # strategy_selector（phase3 高级策略库）：None = 不注入策略，行为与现在完全一致（opt-in）。
        # 非 None 时每次 act 按场景选 0-K 段策略，注入 system 并记进 trace。
        self._strategy_selector = strategy_selector
        self._parser = parser or ActionParser()
        self._canonicalizer = canonicalizer or ActionCanonicalizer()
        self._model_config = dict(model_config) if model_config else {}
        self._template_name = template_name
        # 最小重试：C 的 RetryPolicy 仍是空壳，真实 Ark 偶发 ReadTimeout。重试只针对 LLM 调用
        # （generate）层的 AgentRuntimeError，线性退避。C 的 RetryPolicy 落地后可替换本地逻辑。
        self._max_retries = max(0, max_retries)
        self._retry_backoff = retry_backoff_seconds
        # S7 决策 trace 持久化（opt-in）：传 store 进来则每次 act 落一条 AgentDecisionTrace。
        # 不传 → 完全等价于 trace 落盘前的旧行为（A 的 run_v0_batch.py 不传也跑得动）。
        self._trace_store = trace_store
        self._agent_version = agent_version
        # retry 计入统计（可观测）：被重试挽回的瞬时超时也要透出，便于评估真实接口稳定性。
        # canonicalize_* 三个 key 计 ActionCanonicalizer 拦截类型分布（S7 baseline 量化）。
        self.stats: dict[str, int] = {
            "ok": 0,
            "parse_error": 0,
            "llm_error": 0,
            "retry": 0,
            "canonicalize_meta_ai": 0,
            "canonicalize_cot_leak": 0,
            "canonicalize_role_leak": 0,
        }
        # 失败采样（可观测）：每条记 kind/phase/role/detail，供 run harness 事后定位。
        self.errors: list[dict[str, str]] = []

    _MAX_ERROR_SAMPLES = 50

    def _record_error(self, kind: str, exc: Exception, ctx: AgentContext) -> None:
        if len(self.errors) < self._MAX_ERROR_SAMPLES:
            self.errors.append(
                {
                    "kind": kind,
                    "phase": ctx.phase.value,
                    "role": ctx.role.value,
                    "detail": f"{type(exc).__name__}: {exc}"[:200],
                }
            )

    async def _generate_with_retry(self, messages: list[dict], ctx: AgentContext):
        """调用 provider.generate，瞬时 AgentRuntimeError（如 ReadTimeout）线性退避重试。

        重试耗尽 → 计 llm_error + 记采样 + 返回 None（由 act 转成空 dict 走 Supervisor 兜底）。
        """
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._provider.generate(messages, dict(self._model_config))
            except AgentRuntimeError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    self.stats["retry"] += 1
                    await asyncio.sleep(self._retry_backoff * (attempt + 1))
        self.stats["llm_error"] += 1
        if last_exc is not None:
            self._record_error("llm_error", last_exc, ctx)
        return None

    async def act(self, context: dict) -> dict:
        ctx = AgentContext.model_validate(context)
        template = self._loader.load_for_role(ctx.role.value, self._template_name)

        # phase3 高级策略：按场景选 0-K 段注入 system；selector=None 时完全跳过（opt-in）。
        # snippet_ids / scene_tags 在三个出口都记进 trace，无论本次决策成功与否。
        strategy_snippets: list = []
        strategy_tags: list[str] = []
        if self._strategy_selector is not None:
            strategy_snippets = self._strategy_selector.select(ctx)
            strategy_tags = sorted(self._strategy_selector.detect_tags(ctx))
        extra_sections = [s.text for s in strategy_snippets] or None
        strategy_snippet_ids = [s.id for s in strategy_snippets]

        messages = self._loader.render(template, ctx, extra_system_sections=extra_sections)

        # snapshot 当前 retry 计数，便于把"本次决策被重试了几次"装进 trace.decision_quality_flags
        retry_start = self.stats["retry"]

        response = await self._generate_with_retry(messages, ctx)
        if response is None:
            # LLM 层失败且重试耗尽：交给 Supervisor 安全兜底，不崩局。
            self._emit_trace(
                ctx, template, response=None, action=None,
                outcome="llm_error",
                retries=self.stats["retry"] - retry_start,
                strategy_snippet_ids=strategy_snippet_ids,
                strategy_tags=strategy_tags,
            )
            return {}

        try:
            action = self._parser.parse(response.raw_output, ctx)
        except ParseError as exc:
            # 模型输出无法解析成合法 AgentAction：同样兜底，不崩局。
            self.stats["parse_error"] += 1
            self._record_error("parse_error", exc, ctx)
            self._emit_trace(
                ctx, template, response=response, action=None,
                outcome="parse_error",
                retries=self.stats["retry"] - retry_start,
                strategy_snippet_ids=strategy_snippet_ids,
                strategy_tags=strategy_tags,
            )
            return {}

        action = self._canonicalizer.canonicalize(action, ctx)
        # canonicalize 拦截类型统计（A 文档 §P2 要求量化）：
        triggered = action.metadata.get("canonicalize_triggered") or []
        for cat in triggered:
            key = f"canonicalize_{cat}"
            if key in self.stats:
                self.stats[key] += 1
        self.stats["ok"] += 1
        self._emit_trace(
            ctx, template, response=response, action=action,
            outcome="ok",
            retries=self.stats["retry"] - retry_start,
            canonicalize_triggered=list(triggered),
            strategy_snippet_ids=strategy_snippet_ids,
            strategy_tags=strategy_tags,
        )
        return action.model_dump(mode="json")

    # --- trace 落盘 ---------------------------------------------------------

    def _emit_trace(
        self,
        ctx: AgentContext,
        template: "PromptTemplate",
        *,
        response: "LLMResponse | None",
        action: "AgentAction | None",
        outcome: str,
        retries: int,
        canonicalize_triggered: list[str] | None = None,
        strategy_snippet_ids: list[str] | None = None,
        strategy_tags: list[str] | None = None,
    ) -> None:
        """构造 AgentDecisionTrace 写入 store（store 未注入则 no-op）。

        失败路径（response/action 为 None）的 decision_output 装 fallback 标记，
        Supervisor 实际落的 fallback 事件由 EventLog 记录；trace 这里只保留"我这次决策
        没有产出合法 action"这件事，让 PostGameAnalyzer 能定位到决策点。
        """
        if self._trace_store is None:
            return

        # trace_id：保证每次 act 唯一（同 actor 同 phase 同 round 一般唯一，但加 uuid 后缀
        # 兜底"NIGHT_WEREWOLF 同夜多狼依次决策但 phase/round 相同"等边缘 case）。
        trace_id = (
            f"{ctx.game_id}:{ctx.agent_id}:{ctx.phase.value}:{ctx.round}:{uuid.uuid4().hex[:8]}"
        )

        # input_summary：从 ctx 派生摘要，**不含 TruthState**，按 Interface_v2_1 §6.4 红线。
        input_summary = {
            "phase": ctx.phase.value,
            "round": ctx.round,
            "role": ctx.role.value,
            "agent_id": ctx.agent_id,
            "visible_players_count": len(ctx.visible_players),
            "allowed_actions": [a.value for a in ctx.allowed_actions],
            "current_round_events_count": len(ctx.current_round_events),
            "recent_public_events_count": len(ctx.recent_public_events),
            "private_events_count": len(ctx.private_events),
        }
        if ctx.tie_candidates:
            input_summary["tie_candidates"] = list(ctx.tie_candidates)

        # decision_output：成功摘要 / 失败标记
        if action is not None:
            # claim_result 是 ClaimResult pydantic model（target + claimed_alignment），不是 enum，
            # 不能 .value——seer 跳预报查杀时 LLM 会填这个字段，曾导致 5/27 5 局 batch 全
            # AttributeError 崩在 round 2 DAY_DISCUSSION seer 发言。
            claim_result = action.claim_result
            decision_output = {
                "action_type": action.action_type.value,
                "target": action.target,
                "public_message_chars": len(action.public_message) if action.public_message else 0,
                "role_claim": action.role_claim.value if action.role_claim else None,
                "claim_result": (
                    {
                        "target": claim_result.target,
                        "claimed_alignment": claim_result.claimed_alignment.value,
                    }
                    if claim_result is not None
                    else None
                ),
                "reason_summary": action.reason_summary,
            }
        else:
            decision_output = {"fallback": True, "outcome": outcome}

        decision_quality_flags: dict[str, object] = {
            "outcome": outcome,
            "parse_error": outcome == "parse_error",
            "llm_error": outcome == "llm_error",
            "retry_count": retries,
            "canonicalize_triggered": canonicalize_triggered or [],
        }
        # phase2：把生效的 soul/template 来源记进 trace（decision_quality_flags 是非冻结 dict，
        # 零契约成本）。soul_id 仅在 loader 配了 soul 时出现；template_name 让复盘能区分
        # v0/v1/未来模板，无需解析 prompt_version_id。
        soul_id = template.metadata.get("soul_id")
        if soul_id is not None:
            decision_quality_flags["soul_id"] = soul_id
        decision_quality_flags["template_name"] = self._template_name
        # phase3：命中的策略片段 id + 激活 scene_tags（仅 selector 在场时记，便于 Phase 4
        # 复盘"哪条策略影响了哪个决策"）。非触发回合为空列表，可区分"没 selector" vs "有但没命中"。
        if self._strategy_selector is not None:
            decision_quality_flags["strategy_snippet_ids"] = list(strategy_snippet_ids or [])
            decision_quality_flags["activated_scene_tags"] = list(strategy_tags or [])
        if response is not None:
            decision_quality_flags["llm_latency_ms"] = response.latency_ms
            if response.token_usage:
                decision_quality_flags["token_usage"] = dict(response.token_usage)

        trace = AgentDecisionTrace(
            trace_id=trace_id,
            game_id=ctx.game_id,
            round=ctx.round,
            phase=ctx.phase,
            agent_id=ctx.agent_id,
            role=ctx.role,
            agent_version=self._agent_version,
            prompt_version_id=template.prompt_version_id,
            model_name=(response.model_name if response else None),
            input_summary=input_summary,
            decision_output=decision_output,
            decision_quality_flags=decision_quality_flags,
        )
        self._trace_store.append(trace)
