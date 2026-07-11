"""策略复盘触发脚本 —— 每跑 ~50 局手动跑一次。

选局 → 聚合(纯代码) → LLM 复盘(6角色+全局) → 落盘 draft + belief 命中率，供前端人审。

用法：
    AI_WOLF_STORAGE_BACKEND=jsonl AI_WOLF_DATA_DIR=/var/lib/ai_wolf/data \
        python scripts/run_strategy_review.py --last 50   # 默认 DEEPSEEK；要豆包加 --model-flavor PRO

    # 不烧 token 的连通性自测（FakeLLM，不产生真实建议）：
    python scripts/run_strategy_review.py --last 20 --fake

设计见 ``docs/strategy_review_loop.md``。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.runtime import (  # noqa: E402
    build_belief_store_from_env,
    build_event_store_from_env,
    build_replay_truth_store_from_env,
    build_trace_store_from_env,
)
from evaluation.strategy_review.aggregator import aggregate  # noqa: E402
from evaluation.strategy_review.arm import resolve_arm  # noqa: E402
from evaluation.strategy_review.models import ReviewMeta  # noqa: E402
from evaluation.strategy_review.reviewer import run_review  # noqa: E402
from evaluation.strategy_review.store import StrategyReviewStore  # noqa: E402


def _data_root() -> Path:
    import os

    return Path(os.getenv("AI_WOLF_DATA_DIR", "./data"))


def _select_game_ids(args: argparse.Namespace, trace_store) -> list[str]:
    """选局：显式 game-ids 优先；否则按 events 文件 mtime 取最近 N，且必须有 replay_truth。"""
    root = _data_root()
    # 可分析的局 = 有 traces 的局（真实身份从 trace.role 反推；replay_truth 批量跑通常不落）。
    traces_dir = root / "traces"
    have_traces = {p.stem for p in traces_dir.glob("*.jsonl")} if traces_dir.is_dir() else set()

    if args.game_ids:
        gids = [g for g in args.game_ids if g in have_traces]
    else:
        events_dir = root / "events"
        files = sorted(events_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        gids = []
        for p in files:
            gid = p.stem
            if gid not in have_traces:
                continue
            if args.arm and resolve_arm(gid, trace_store.list_by_game(gid)) != args.arm:
                continue
            gids.append(gid)
            if len(gids) >= args.last:
                break
    return gids


def _build_provider(args: argparse.Namespace):
    if args.fake:
        from agent_runtime.llm_provider import FakeLLMProvider

        # 自测用：永远返回空数组（不产生真实建议，只验证链路通）。
        return FakeLLMProvider("[]", model_name="fake-llm")
    from runner.launcher import build_provider

    provider = build_provider(args.model_flavor)
    # 复盘 prompt 比对局 prompt 大很多（角色 prompt 全文 + 多条 digest），默认 60s 易 ReadTimeout，放宽。
    if hasattr(provider, "_timeout"):
        provider._timeout = 180.0
    return provider


def main() -> int:
    parser = argparse.ArgumentParser(description="策略复盘 + 人审 draft 生成")
    parser.add_argument("--last", type=int, default=50, help="取最近 N 局（默认 50）")
    parser.add_argument("--game-ids", nargs="*", default=None, help="显式指定 game_id 列表")
    parser.add_argument("--arm", default=None, help="只选某 arm（v0/v1/v2）")
    parser.add_argument(
        "--model-flavor",
        default="DEEPSEEK",
        help="分析用模型档位 PRO/CODE/DEEPSEEK（默认 DEEPSEEK：独立 endpoint，不与服务器批跑抢豆包）",
    )
    parser.add_argument("--temperature", type=float, default=0.4)
    parser.add_argument("--out", default=None, help="review_id（默认时间戳）")
    parser.add_argument("--fake", action="store_true", help="用 FakeLLM 自测，不烧 token")
    args = parser.parse_args()

    event_store = build_event_store_from_env()
    trace_store = build_trace_store_from_env()
    belief_store = build_belief_store_from_env()
    replay_truth_store = build_replay_truth_store_from_env()

    game_ids = _select_game_ids(args, trace_store)
    if not game_ids:
        print("没有可分析的对局（需同时有 events 与 replay_truth）。", file=sys.stderr)
        return 1
    print(f"选中 {len(game_ids)} 局，开始聚合 …")

    agg = aggregate(
        game_ids,
        event_store=event_store,
        trace_store=trace_store,
        replay_truth_store=replay_truth_store,
        belief_store=belief_store,
    )
    print(f"arm 分布: {agg.arm_counts}")

    provider = _build_provider(args)
    model_name = getattr(provider, "model_name", args.model_flavor)
    print("调用 LLM 复盘（6 角色 + 全局）…")
    out = run_review(agg, provider=provider, model_name=model_name, temperature=args.temperature)

    review_id = args.out or "review_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    drafts_by_role: dict[str, int] = {}
    for d in out.drafts:
        drafts_by_role[d.role] = drafts_by_role.get(d.role, 0) + 1

    meta = ReviewMeta(
        review_id=review_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        source_game_ids=game_ids,
        n_games=len(game_ids),
        arm_counts=agg.arm_counts,
        model_flavor=args.model_flavor if not args.fake else "fake",
        model_name=model_name,
        draft_count=len(out.drafts),
        drafts_by_role=drafts_by_role,
        dropped_out_of_scope=out.dropped,
        belief_accuracy=agg.belief.to_dict(),
    )

    store = StrategyReviewStore(_data_root() / "strategy_reviews")
    store.save_review(meta, out.drafts)

    print(
        f"完成：review_id={review_id} drafts={len(out.drafts)} "
        f"(丢弃越界/泄漏 {out.dropped}, LLM 失败角色 {out.errors}) 按角色={drafts_by_role}"
    )
    print(f"落盘: {_data_root() / 'strategy_reviews' / review_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
