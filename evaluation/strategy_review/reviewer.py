"""LLM 复盘分析师 —— 把聚合材料喂给 LLM，产出经校验的 StrategyInsightDraft。

每个角色（+全局）跑一次 LLM。两道闸门（§2.1 / §2.3）：
- `target_layer ∈ {role, advanced}` 且 `target_file` 命中「可改目标」真实文件 → 否则丢弃。
- draft 文本含真相态泄漏 → 丢弃。
两道都计入 `dropped`，不静默吞。
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_policy.advanced_strategy.strategy_library import StrategyLibrary
from agent_runtime.llm_provider import LLMProvider, generate_sync
from agent_runtime.prompt_template_loader import PromptTemplateLoader
from evaluation.strategy_review.aggregator import AggregateResult, RoleBatchReview
from evaluation.strategy_review.models import (
    ALLOWED_TARGET_LAYERS,
    EvidenceRef,
    StrategyInsightDraft,
)
from evaluation.strategy_review.prompt_assets import (
    RolePromptBundle,
    build_global_bundle,
    build_role_bundle,
    editable_target_files,
    is_allowed_new_snippet,
)
from evaluation.strategy_review.sanitize import contains_truth_leak

_ANALYST_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "agent_policy" / "prompts" / "analyst" / "strategy_review.md"
)


@dataclass
class ReviewOutput:
    drafts: list[StrategyInsightDraft]
    dropped: int  # 越界/泄漏被丢弃数
    errors: int = 0  # LLM 调用失败（超时/网络）的角色数


def _analyst_system_prompt() -> str:
    return _ANALYST_PROMPT_PATH.read_text(encoding="utf-8")


def _bundle_text(bundle: RolePromptBundle) -> str:
    lines = ["## 可改目标（建议只能落在这些文件）"]
    for a in bundle.editable:
        lines.append(f"\n### [{a.label}] file=`{a.target_file}`\n{a.text}")
    if bundle.new_snippet_dirs:
        dirs = " 或 ".join(f"`{d}/`" for d in bundle.new_snippet_dirs)
        lines.append(
            f"\n## 可新增 advanced snippet（仅当现有片段都不合适、确需补一类新打法时）"
            f"\n在 {dirs} 下新建 `.md` 文件：`target_layer=advanced`、`target_file` 写"
            f"该目录下一个描述性文件名（如 `<dir>/early_claim_timing.md`）、`current_excerpt` 留空、"
            f"`proposed_change` 给**完整 snippet 内容**（含 frontmatter：id/role_scope/phase_scope/"
            f"scene_tags/priority + markdown 正文）。"
        )
    lines.append("\n## 只读背景（理解约束用，禁止作为建议对象）")
    for a in bundle.read_only:
        lines.append(f"\n### [{a.label}] file=`{a.target_file}`\n{a.text}")
    return "\n".join(lines)


def _material_text(role: str, review: RoleBatchReview, belief_rows: list[dict[str, Any]]) -> str:
    lines = [f"# 角色复盘材料：{role}", f"实例数 n={review.n_instances}"]
    lines.append("## 统计指标")
    lines.append(json.dumps(review.stats, ensure_ascii=False, indent=1))
    if belief_rows:
        lines.append("## belief 命中率（只读参考，按 arm）")
        lines.append(json.dumps(belief_rows, ensure_ascii=False))
    lines.append("## 代表性对局摘要（已脱敏）")
    for i, s in enumerate(review.samples):
        ev = json.dumps(s.evidence, ensure_ascii=False)
        lines.append(f"\n### 样本{i + 1} [{s.kind}] game={s.game_id} arm={s.arm}\n{s.digest}\n证据: {ev}")
    return "\n".join(lines)


def _model_config(model_name: str, temperature: float) -> dict[str, Any]:
    return {"model_name": model_name, "temperature": temperature}


def _extract_json_array(raw: str) -> list[Any]:
    """从 LLM 原文里抠出 JSON 数组（容忍 ```json 围栏与前后噪声）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.startswith("json"):
            text = text[4:]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _to_drafts(
    role: str,
    items: list[Any],
    editable_files: set[str],
    bundle: RolePromptBundle | None = None,
) -> ReviewOutput:
    drafts: list[StrategyInsightDraft] = []
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        layer = item.get("target_layer")
        target_file = item.get("target_file")
        observed = (item.get("observed_issue") or "").strip()
        proposed = (item.get("proposed_change") or "").strip()
        excerpt = item.get("current_excerpt") or None

        # 闸门 1：范围校验。target_file 须命中现有可改文件，或是允许新增的 advanced snippet。
        in_scope = layer in ALLOWED_TARGET_LAYERS and (
            target_file in editable_files
            or (
                layer == "advanced"
                and bundle is not None
                and isinstance(target_file, str)
                and is_allowed_new_snippet(target_file, bundle)
            )
        )
        if not in_scope:
            dropped += 1
            continue
        if not observed or not proposed:
            dropped += 1
            continue
        # 闸门 2：真相态脱敏
        if any(contains_truth_leak(t) for t in (observed, proposed, excerpt or "")):
            dropped += 1
            continue

        evidence = [
            EvidenceRef(**{k: e.get(k) for k in ("game_id", "round", "phase", "trace_id")})
            for e in (item.get("supporting_evidence") or [])
            if isinstance(e, dict) and e.get("game_id")
        ]
        drafts.append(
            StrategyInsightDraft(
                draft_id=f"{role}-{uuid.uuid4().hex[:8]}",
                role=role,
                target_layer=layer,  # type: ignore[arg-type]
                target_file=target_file,
                current_excerpt=excerpt,
                observed_issue=observed,
                proposed_change=proposed,
                supporting_evidence=evidence,
                potential_risk=(item.get("potential_risk") or None),
            )
        )
    return ReviewOutput(drafts=drafts, dropped=dropped)


def review_one(
    role: str,
    review: RoleBatchReview,
    bundle: RolePromptBundle,
    *,
    provider: LLMProvider,
    model_name: str,
    temperature: float,
    belief_rows: list[dict[str, Any]] | None = None,
) -> ReviewOutput:
    if review.n_instances == 0 and not review.samples:
        return ReviewOutput(drafts=[], dropped=0)
    messages = [
        {"role": "system", "content": _analyst_system_prompt() + "\n\n" + _bundle_text(bundle)},
        {"role": "user", "content": _material_text(role, review, belief_rows or [])},
    ]
    resp = _generate_with_retry(provider, messages, _model_config(model_name, temperature))
    items = _extract_json_array(getattr(resp, "raw_output", "") or "")
    return _to_drafts(role, items, editable_target_files(bundle), bundle)


_MAX_RETRIES = 3
_BACKOFF_BASE_S = 4.0


def _generate_with_retry(provider: LLMProvider, messages, model_config):
    """对瞬时错误（429 限流 / 超时）退避重试；最后一次失败原样抛出，交上层计数。

    服务器批跑常年占用同一 Ark endpoint，本地复盘易撞 429，故退避要足够长。
    """
    last: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return generate_sync(provider, messages, model_config)
        except Exception as exc:  # ArkLLMError(429/timeout) 等
            last = exc
            if attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_BASE_S * (2**attempt)
                print(f"[strategy-review] LLM 调用失败({type(exc).__name__}),{wait:.0f}s 后重试 "
                      f"({attempt + 1}/{_MAX_RETRIES})", file=sys.stderr)
                time.sleep(wait)
    raise last  # type: ignore[misc]


def run_review(
    agg: AggregateResult,
    *,
    provider: LLMProvider,
    model_name: str,
    temperature: float = 0.4,
    loader: PromptTemplateLoader | None = None,
    library: StrategyLibrary | None = None,
) -> ReviewOutput:
    """对 6 角色 + 全局各跑一次，汇总 drafts 与丢弃数。"""
    loader = loader or PromptTemplateLoader()
    library = library or StrategyLibrary()
    belief_by_role = _belief_rows_by_role(agg)

    all_drafts: list[StrategyInsightDraft] = []
    total_dropped = 0
    total_errors = 0

    def _safe(role: str, review, bundle) -> None:
        nonlocal total_dropped, total_errors
        try:
            out = review_one(
                role, review, bundle,
                provider=provider, model_name=model_name, temperature=temperature,
                belief_rows=belief_by_role.get("all" if role == "global" else role, []),
            )
            all_drafts.extend(out.drafts)
            total_dropped += out.dropped
        except Exception as exc:  # 单角色 LLM 失败（超时/网络）不连累其余角色
            total_errors += 1
            print(f"[strategy-review] 角色 {role} LLM 调用失败，跳过：{type(exc).__name__}: {exc}",
                  file=sys.stderr)

    for role, review in agg.role_reviews.items():
        _safe(role, review, build_role_bundle(role, loader=loader, library=library))

    # 全局
    from evaluation.strategy_review.aggregator import RoleBatchReview as _RBR

    global_as_role = _RBR(
        role="global",
        n_instances=agg.global_review.n_games,
        stats=agg.global_review.stats,
        samples=agg.global_review.samples,
    )
    _safe("global", global_as_role, build_global_bundle(loader=loader, library=library))

    return ReviewOutput(drafts=all_drafts, dropped=total_dropped, errors=total_errors)


def _belief_rows_by_role(agg: AggregateResult) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in agg.belief.rows:
        out.setdefault(row.role, []).append(row.to_dict())
    return out
