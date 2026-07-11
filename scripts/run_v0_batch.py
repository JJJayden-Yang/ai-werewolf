"""本地 v0 批量运行器 —— 跑 N 局真实 v0，聚合成 contracts.BatchRunReport。

给 C 做 production BatchRunner（S5/S6）前的本地验证 + 参考实现：
- 每局**独立** GameEngine / EventStore / LLMAgent / Supervisor → 验证局间隔离（无串局）；
- 逐局产出 ``contracts.GameRunResult``，聚合成 ``contracts.BatchRunReport``（dogfood 预留 schema）；
- 额外打印 v0 质量指标（ok/parse/llm/retry、兜底率、平票命中、发言数）——这些 schema 暂无字段，
  先走 console，需要进 report 再走 contract MR。

用法::

    python scripts/run_v0_batch.py --games 2                 # 最小真实冒烟（2 局）
    python scripts/run_v0_batch.py --games 5 --seed-start 100 --out batch_report.json

凭证：同 run_v0_game.py（ARK_PRO_* 或 ARK_API_KEY/ARK_ENDPOINT_ID + ARK_BASE_URL；
或 scripts/Yuan_local/.env.local）。真实 API 消耗 token，按需跑。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent_policy.advanced_strategy import StrategySelector  # noqa: E402
from agent_policy.realtime_belief_updater import RuleBasedRealtimeBeliefUpdater  # noqa: E402
from agent_runtime import DEFAULT_SOUL_ID, ArkLLMProvider, LLMAgent  # noqa: E402


def _resolve_soul_arg(value: str | None) -> str | None:
    """把 --soul 的字符串值解析成 soul_id；``none/off`` → None（真·无 soul 基线）。"""
    if value is None or str(value).strip().lower() in {"none", "off", ""}:
        return None
    return value
from context.context_assembler import ContextAssembler  # noqa: E402
from context.context_window_policy import ContextWindowPolicy  # noqa: E402
from contracts import (  # noqa: E402
    CONTRACT_VERSION,
    BatchRunReport,
    EventType,
    GameConfig,
    GameRunResult,
    Phase,
    RunConfigSnapshot,
)
from game_core import GameEngine, GameSessionManager  # noqa: E402
from stores.belief_observability_store import InMemoryBeliefObservabilityStore  # noqa: E402
from stores.belief_state_store import InMemoryBeliefStateStore, JsonlBeliefStateStore  # noqa: E402
from stores.event_store import InMemoryEventStore  # noqa: E402
from stores.trace_store import InMemoryTraceStore, JsonlTraceStore, TraceStore  # noqa: E402
from supervisor import GameRunError, Supervisor  # noqa: E402

_FIXTURE = _ROOT / "contracts" / "fixtures" / "game_config_9p_mvp.json"
_LOCAL_ENV = _ROOT / "scripts" / "Yuan_local" / ".env.local"
_DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
_DEFAULT_MAX_TOKENS = 1024
_DEFAULT_TIMEOUT = 60.0


def _load_local_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _resolve_creds() -> tuple[str, str, str]:
    api_key = os.getenv("ARK_PRO_API_KEY") or os.getenv("ARK_API_KEY", "")
    endpoint_id = os.getenv("ARK_PRO_ENDPOINT_ID") or os.getenv("ARK_ENDPOINT_ID", "")
    base_url = os.getenv("ARK_BASE_URL", _DEFAULT_BASE_URL)
    if not api_key or not endpoint_id:
        raise SystemExit(
            "缺少 ARK 凭证：设置 ARK_PRO_API_KEY/ARK_PRO_ENDPOINT_ID（或 ARK_API_KEY/"
            "ARK_ENDPOINT_ID），或放进 scripts/Yuan_local/.env.local。"
        )
    return api_key, endpoint_id, base_url


def run_one_game(
    provider,
    seed: int,
    game_id: str,
    temperature: float,
    retry_backoff: float = 0.5,
    trace_store: TraceStore | None = None,
    *,
    arm: str = "v0",
    use_belief: bool = False,
    soul_id: str | None = None,
    strategy_selector: StrategySelector | None = None,
) -> tuple[GameRunResult, dict]:
    """跑一局，返回 (GameRunResult, 额外 v0 指标 dict)。每局独立 engine/store/agent。

    trace_store: 可选 TraceStore；传入后 LLMAgent 三个出口都落 AgentDecisionTrace。
    None 时完全不接触 trace 层（A 原版行为）。
    """
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    data["game_id"] = game_id
    engine = GameEngine()
    engine.sessions = GameSessionManager(rng=random.Random(seed))
    engine.sessions.create_game(GameConfig.model_validate(data))
    store = InMemoryEventStore()
    belief_enabled = use_belief or arm == "v1"
    belief_is_shadow = belief_enabled and arm == "v0"
    # 启用 Belief 文件存储（落盘到 data/belief_states/）
    belief_store = (
        JsonlBeliefStateStore(root_dir=str(_ROOT / "data" / "belief_states"))
        if belief_enabled
        else None
    )
    belief_observability_store = (
        InMemoryBeliefObservabilityStore() if belief_enabled else None
    )
    belief_updater = (
        RuleBasedRealtimeBeliefUpdater(
            event_store=store,
            belief_store=belief_store,
            session_provider=engine,
            is_shadow=belief_is_shadow,
            observability_store=belief_observability_store,
        )
        if belief_store is not None
        else None
    )
    window_policy = ContextWindowPolicy()
    agent_version = arm if not (arm == "v0" and belief_enabled) else "v0+belief"
    agent = LLMAgent(
        provider,
        model_config={"temperature": temperature},
        retry_backoff_seconds=retry_backoff,
        trace_store=trace_store,
        template_name="v1_belief_llm" if arm == "v1" else "v0_free_llm",
        soul_id=soul_id,
        strategy_selector=strategy_selector,
        agent_version=agent_version,
    )
    sup = Supervisor(
        engine,
        ContextAssembler(
            session_provider=engine,
            event_store=store,
            window_policy=window_policy,
            belief_store=belief_store if arm == "v1" else None,
        ),
        agent,
        store,
        belief_updater=belief_updater,
        deliver_witch_kill_info=True,
    )

    started = time.perf_counter()
    crash: GameRunError | None = None
    try:
        asyncio.run(sup.run_game(game_id))
    except GameRunError as exc:
        crash = exc
        # Dump 完整 traceback + 最后 8 个事件 + session 状态，便于定位崩点（A handoff §P0
        # 报的 ContextBudgetExceededError 之外的非典型崩需要更多信息才能 root cause）。
        import traceback
        print(f"\n--- [{game_id}] GameRunError traceback ---", flush=True)
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        print(f"--- [{game_id}] session state ---", flush=True)
        try:
            _sess = engine.get_session(game_id)
            print(f"  current_phase={_sess.current_phase.value} round={_sess.round}", flush=True)
        except Exception as _e:
            print(f"  (failed to read session: {_e})", flush=True)
        print(f"--- [{game_id}] last 8 events ---", flush=True)
        for _ev in store.list_by_game(game_id)[-8:]:
            print(
                f"  {_ev.event_type.value:<24s} round={_ev.round} phase={_ev.phase.value} "
                f"actor={_ev.actor} target={_ev.target}",
                flush=True,
            )
        print("--- end dump ---\n", flush=True)
    runtime_ms = (time.perf_counter() - started) * 1000.0

    session = engine.get_session(game_id)
    events = store.list_by_game(game_id)
    game_over = next((e for e in events if e.event_type == EventType.GAME_OVER), None)
    completed = session.current_phase == Phase.GAME_OVER
    rule_violations = sum(1 for e in events if e.event_type == EventType.RULE_VALIDATION)
    phases_seen = {e.phase for e in events}
    tie_hit = any(
        p in phases_seen for p in (Phase.DAY_TIE_DISCUSSION, Phase.DAY_TIE_REVOTE)
    )
    trace_count = (
        len(trace_store.list_by_game(game_id)) if trace_store is not None else 0
    )
    belief_histories: dict[str, int] = {}
    if belief_store is not None:
        belief_histories = {
            pid: len(belief_store.get_history(game_id, pid, is_shadow=belief_is_shadow))
            for pid in session.truth_state.players
        }
    belief_update_errors = len(sup._belief_update_errors)
    v1_belief_failed = arm == "v1" and belief_update_errors > 0
    status = "completed" if completed and not v1_belief_failed else "failed"

    result = GameRunResult(
        game_id=game_id,
        status=status,
        winner=(game_over.payload.get("winner") if game_over else None),
        rounds=session.round,
        runtime_ms=runtime_ms,
        error_type=(
            "belief_update_failed"
            if v1_belief_failed
            else (type(crash.__cause__).__name__ if crash and crash.__cause__ else None)
        ),
        error_phase=(crash.phase if crash else None),
        error_actor=(crash.actor if crash else None),
        error_message=(
            f"{belief_update_errors} belief updater errors"
            if v1_belief_failed
            else (str(crash.__cause__)[:200] if crash and crash.__cause__ else None)
        ),
    )
    extra = {
        "agent_stats": dict(agent.stats),
        "context_window_stats": dict(window_policy.stats),
        "fallbacks": rule_violations,
        "tie_hit": tie_hit,
        "speeches": sum(1 for e in events if e.event_type == EventType.SPEECH),
        "events": len(events),
        "trace_count": trace_count,
        "agent_errors": agent.errors[:5],
        "arm": arm,
        "belief_enabled": belief_enabled,
        "belief_injected": arm == "v1",
        "belief_is_shadow": belief_is_shadow,
        "belief_observers": sum(1 for count in belief_histories.values() if count > 0),
        "belief_saves": len(belief_store) if belief_store is not None else 0,
        "belief_update_errors": belief_update_errors,
        "belief_update_batches": (
            len(belief_observability_store.list_updates(game_id))
            if belief_observability_store is not None
            else 0
        ),
        "belief_curve_points": (
            len(belief_observability_store.list_curve_points(game_id))
            if belief_observability_store is not None
            else 0
        ),
    }
    return result, extra


def run_batch(
    games: int,
    seed_start: int,
    temperature: float,
    provider=None,
    retry_backoff: float = 0.5,
    trace_dir: Path | None = None,
    *,
    arm: str = "v0",
    use_belief: bool = False,
    soul_id: str | None = None,
    use_strategy: bool = False,
    model_flavor: str = "PRO",
) -> tuple[BatchRunReport, list[dict]]:
    """跑 N 局批量。

    trace_dir: None 时不落 trace；指定时为每局创建 JsonlTraceStore(trace_dir)，
    每局 trace 落到 ``trace_dir/<game_id>.jsonl``（A 5/27 P1 决策 trace 持久化）。
    model_flavor: PRO/CODE=火山 Doubao；DEEPSEEK=DeepSeek 官方 API（共用 launcher.build_provider）。
    """
    if arm not in {"v0", "v1"}:
        raise ValueError(f"arm must be 'v0' or 'v1', got {arm!r}")
    belief_enabled = use_belief or arm == "v1"
    agent_version = arm if not (arm == "v0" and belief_enabled) else "v0+belief"

    endpoint_id = ""
    model_provider = "ark"
    if provider is None:
        _load_local_env(_LOCAL_ENV)
        from runner.launcher import build_provider  # 与 start_game/API 共用同一档位逻辑

        provider = build_provider(model_flavor)
        if model_flavor.upper() == "DEEPSEEK":
            model_provider = "deepseek"
            endpoint_id = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        else:
            endpoint_id = os.getenv(f"ARK_{model_flavor.upper()}_ENDPOINT_ID") or os.getenv("ARK_ENDPOINT_ID", "")
    snapshot = RunConfigSnapshot(
        contract_version=CONTRACT_VERSION,
        agent_version=agent_version,
        model_provider=model_provider,
        endpoint_id=endpoint_id,
        temperature=temperature,
        max_tokens=_DEFAULT_MAX_TOKENS,
        timeout_seconds=_DEFAULT_TIMEOUT,
        prompt_version_id=(
            "<role>:v1_belief_llm" if arm == "v1" else "<role>:v0_free_llm"
        ),
        seed=seed_start,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    # 跨局共用同一个 JsonlTraceStore 实例：构造时扫 root_dir hydrate 进内存索引，
    # 每局 append 一边写文件一边更新索引，避免重复 instantiate 浪费 IO（A 的本地批量
    # 一次跑 5-30 局，trace 目录从空开始 hydrate 几乎是 0 成本）。
    batch_trace_store: TraceStore | None = None
    if trace_dir is not None:
        trace_dir.mkdir(parents=True, exist_ok=True)
        batch_trace_store = JsonlTraceStore(trace_dir)

    # phase3 高级策略：构造一次 selector（StrategyLibrary 扫一次目录），跨局复用。
    strategy_selector = StrategySelector() if use_strategy else None

    results: list[GameRunResult] = []
    extras: list[dict] = []
    for i in range(games):
        gid = f"v0_batch_{seed_start + i}"
        print(f"  [{i + 1}/{games}] 跑 {gid} (seed={seed_start + i}) ...", flush=True)
        result, extra = run_one_game(
            provider, seed_start + i, gid, temperature, retry_backoff,
            soul_id=soul_id,
            strategy_selector=strategy_selector,
            trace_store=batch_trace_store,
            arm=arm,
            use_belief=belief_enabled,
        )
        results.append(result)
        extras.append(extra)
        cw = extra["context_window_stats"]
        print(
            f"      -> {result.status} | winner={result.winner} | rounds={result.rounds} | "
            f"{result.runtime_ms:.0f}ms | ok/parse/llm/retry="
            f"{extra['agent_stats']['ok']}/{extra['agent_stats']['parse_error']}/"
            f"{extra['agent_stats']['llm_error']}/{extra['agent_stats']['retry']} | "
            f"canon(meta/cot/role)="
            f"{extra['agent_stats']['canonicalize_meta_ai']}/"
            f"{extra['agent_stats']['canonicalize_cot_leak']}/"
            f"{extra['agent_stats']['canonicalize_role_leak']} | "
            f"ctx(trunc/deg/exc)="
            f"{cw['truncated_speech_events']}/{cw['progressive_degrade_triggered']}/"
            f"{cw['budget_exceeded']} | "
            f"fallback={extra['fallbacks']} | tie={extra['tie_hit']} | "
            f"traces={extra['trace_count']} | "
            f"belief(obs/saves/inject/err)="
            f"{extra['belief_observers']}/{extra['belief_saves']}/"
            f"{extra['belief_injected']}/{extra['belief_update_errors']} "
            f"lane={'shadow' if extra['belief_is_shadow'] else 'real'} "
            f"audit(update/curve)="
            f"{extra['belief_update_batches']}/{extra['belief_curve_points']}",
            flush=True,
        )
        # failed 局立刻透出 error 定位，避免要等整批结束读 report JSON 才能查崩点
        if result.status != "completed":
            print(
                f"         [FAIL] error_type={result.error_type} "
                f"phase={result.error_phase} actor={result.error_actor}\n"
                f"         message={result.error_message}",
                flush=True,
            )

    completed = [r for r in results if r.status == "completed"]
    failed = [r for r in results if r.status != "completed"]
    runtimes = [r.runtime_ms for r in results if r.runtime_ms is not None]
    report = BatchRunReport(
        total=games,
        completed=len(completed),
        failed=len(failed),
        avg_runtime_ms=(statistics.mean(runtimes) if runtimes else None),
        winner_distribution=dict(Counter(r.winner for r in results if r.winner)),
        error_count=sum(1 for r in results if r.error_type),
        failed_game_ids=[r.game_id for r in failed],
        representative_failed_runs=failed[:3],
        run_config_snapshot=snapshot,
    )
    return report, extras


def _print_report(report: BatchRunReport, extras: list[dict]) -> None:
    print("\n" + "=" * 60)
    agent_version = (
        report.run_config_snapshot.agent_version
        if report.run_config_snapshot is not None
        else "v0"
    )
    print(
        f"{agent_version} 批量结果  "
        f"total={report.total} completed={report.completed} failed={report.failed}"
    )
    print("=" * 60)
    print(f"赢家分布: {report.winner_distribution}")
    print(f"平均时长: {report.avg_runtime_ms:.0f}ms" if report.avg_runtime_ms else "平均时长: -")
    if report.failed_game_ids:
        print(f"失败局: {report.failed_game_ids}")
    # 跨局聚合 v0 质量指标（schema 暂无字段，console 透出）
    agent_keys = (
        "ok", "parse_error", "llm_error", "retry",
        "canonicalize_meta_ai", "canonicalize_cot_leak", "canonicalize_role_leak",
    )
    tot = {k: sum(e["agent_stats"][k] for e in extras) for k in agent_keys}
    decisions = tot["ok"] + tot["parse_error"] + tot["llm_error"]
    ok_pct = (tot["ok"] / decisions * 100.0) if decisions else 0.0
    print(
        f"决策合计 ok/parse/llm/retry: "
        f"{tot['ok']}/{tot['parse_error']}/{tot['llm_error']}/{tot['retry']} "
        f"(真实 LLM 占比 {ok_pct:.0f}%)"
    )
    print(
        f"canonicalize 拦截 meta_ai/cot_leak/role_leak: "
        f"{tot['canonicalize_meta_ai']}/{tot['canonicalize_cot_leak']}/{tot['canonicalize_role_leak']} "
        f"(占决策 {(sum(tot[k] for k in ('canonicalize_meta_ai','canonicalize_cot_leak','canonicalize_role_leak')) / decisions * 100.0) if decisions else 0.0:.1f}%)"
    )
    cw_keys = ("truncated_speech_events", "progressive_degrade_triggered", "budget_exceeded")
    cw = {k: sum(e["context_window_stats"][k] for e in extras) for k in cw_keys}
    print(
        f"context 裁剪压力 trunc/degrade/exceed: "
        f"{cw['truncated_speech_events']}/{cw['progressive_degrade_triggered']}/{cw['budget_exceeded']} "
        f"(budget_exceeded > 0 即说明 progressive_degrade step 5/6/7 也没救回来，需要再加 step)"
    )
    print(f"兜底合计: {sum(e['fallbacks'] for e in extras)}")
    print(f"平票命中局数: {sum(1 for e in extras if e['tie_hit'])}/{report.total}")
    print(f"trace 落盘总条数: {sum(e['trace_count'] for e in extras)}")
    if any(e["belief_enabled"] for e in extras):
        print(
            f"belief observers/saves/errors: "
            f"{sum(e['belief_observers'] for e in extras)}/"
            f"{sum(e['belief_saves'] for e in extras)}/"
            f"{sum(e['belief_update_errors'] for e in extras)} "
            f"(inject_to_agent={any(e['belief_injected'] for e in extras)}, "
            f"shadow_lane={any(e['belief_is_shadow'] for e in extras)})"
        )
        print(
            f"belief audit update_batches/curve_points: "
            f"{sum(e['belief_update_batches'] for e in extras)}/"
            f"{sum(e['belief_curve_points'] for e in extras)}"
        )
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="本地 v0 批量运行器")
    parser.add_argument("--games", type=int, default=2)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument(
        "--arm",
        choices=("v0", "v1"),
        default="v0",
        help="v0=纯 LLM；v1=启用 belief updater 并注入 belief_top_suspects",
    )
    parser.add_argument(
        "--belief",
        action="store_true",
        help="为 v0 启用后台 belief 更新但不注入 AgentContext；v1 会自动启用",
    )
    parser.add_argument(
        "--soul",
        default=DEFAULT_SOUL_ID,
        help="全局人格 soul（default_balanced / cautious / aggressive / logical）；"
        f"默认 {DEFAULT_SOUL_ID}（冷静分析中性人格）。传 `none` 跑真·无 soul 基线。"
        "soul 会随 trace 落到 decision_quality_flags。",
    )
    parser.add_argument(
        "--strategy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="静态高级策略库：场景命中时注入专家打法片段。**默认开**（snippet 作为所有 arm 共享的"
        "基础能力，含 v0）；批跑纯基线请显式 --no-strategy。命中片段随 trace 落到 "
        "decision_quality_flags(strategy_snippet_ids/activated_scene_tags)。",
    )
    parser.add_argument(
        "--model-flavor",
        choices=("PRO", "CODE", "DEEPSEEK"),
        default="PRO",
        help="PRO/CODE=火山 Doubao；DEEPSEEK=DeepSeek 官方 API（需 DEEPSEEK_API_KEY）",
    )
    parser.add_argument("--out", default=None, help="把 BatchRunReport JSON 写到此路径")
    parser.add_argument(
        "--trace-dir",
        default=None,
        help=(
            "AgentDecisionTrace 落盘目录（S7 路径，每局 <game_id>.jsonl）。"
            "省略时 trace 完全不落盘（A 原版行为）。"
        ),
    )
    args = parser.parse_args()

    print(
        f"开始批量: {args.games} 局, seed {args.seed_start}..{args.seed_start + args.games - 1} "
        f"arm={args.arm} belief={args.belief or args.arm == 'v1'}"
    )
    trace_dir = Path(args.trace_dir) if args.trace_dir else None
    if trace_dir is not None:
        print(f"  → trace 落盘目录: {trace_dir.resolve()}")
    report, extras = run_batch(
        args.games,
        args.seed_start,
        args.temperature,
        trace_dir=trace_dir,
        arm=args.arm,
        use_belief=args.belief,
        soul_id=_resolve_soul_arg(args.soul),
        use_strategy=args.strategy,
        model_flavor=args.model_flavor,
    )
    _print_report(report, extras)

    if args.out:
        Path(args.out).write_text(report.model_dump_json(indent=2), encoding="utf-8")
        print(f"BatchRunReport 已写入 {args.out}")


if __name__ == "__main__":
    main()
