"""Phase 6 mixed-belief batch runner.

Runs the four main A-line experiment arms by selecting which role groups receive
belief injection while keeping the underlying contracts unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import warnings
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent_policy import RoleStrategyMockAgent  # noqa: E402
from agent_policy.slow_think_reflector import LLMSlowThinkReflector  # noqa: E402
from agent_runtime import ArkLLMProvider, LLMAgent  # noqa: E402
from contracts import (  # noqa: E402
    CONTRACT_VERSION,
    BatchRunReport,
    EventType,
    GameConfig,
    GameRunResult,
    Phase,
    RunConfigSnapshot,
)
from runner import build_game  # noqa: E402
from runner.arm_filter import make_arm_filter  # noqa: E402
from scripts._mixed_metrics import compute_mixed_metrics  # noqa: E402
from scripts.run_v0_batch import (  # noqa: E402
    _DEFAULT_MAX_TOKENS,
    _DEFAULT_TIMEOUT,
    _LOCAL_ENV,
    _load_local_env,
    _resolve_creds,
)
from stores.belief_state_store import JsonlBeliefStateStore  # noqa: E402
from stores.event_store import InMemoryEventStore, JsonlEventStore  # noqa: E402
from stores.trace_store import InMemoryTraceStore, JsonlTraceStore, TraceStore  # noqa: E402
from supervisor import GameRunError  # noqa: E402

_FIXTURE = _ROOT / "contracts" / "fixtures" / "game_config_9p_mvp.json"

ArmValue = str
InjectFactory = Callable[[object, str], Callable[[str], bool]]
AgentFactory = Callable[[TraceStore, str, float, float], object]
ReflectorFactory = Callable[[], object]

ALL_SCOPES = frozenset({"wolves", "gods", "civilians"})
_VALID_ARMS = frozenset({"v0", "v1"})


@dataclass(frozen=True)
class MixedArmPlan:
    """Resolved mixed-arm settings for one batch run."""

    arm: str
    inject_scopes: frozenset[str]
    belief_inject_filter_factory: InjectFactory | None
    agent_version: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_config(game_id: str) -> GameConfig:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    data["game_id"] = game_id
    return GameConfig.model_validate(data)


def _make_combined_factory(inject_scopes: frozenset[str]) -> InjectFactory:
    """Create a factory that ORs the snapshot filters for each requested scope."""

    def factory(engine, game_id: str) -> Callable[[str], bool]:
        filters = [make_arm_filter(scope, engine, game_id) for scope in inject_scopes]
        return lambda agent_id: any(scope_filter(agent_id) for scope_filter in filters)

    return factory


def encode_agent_version(arm: str, inject_scopes: frozenset[str]) -> str:
    if arm == "v0":
        return "v0"
    if inject_scopes == ALL_SCOPES:
        return "v1"
    sorted_scopes = "+".join(sorted(inject_scopes))
    return f"v1+belief:{sorted_scopes}"


def _validate_arm(name: str, value: str | None) -> None:
    if value is not None and value not in _VALID_ARMS:
        raise ValueError(f"{name} must be 'v0' or 'v1', got {value!r}")


def resolve_mixed_arm(
    *,
    arm_wolves: ArmValue,
    arm_villagers: ArmValue,
    arm_gods: ArmValue | None = None,
    arm_civilians: ArmValue | None = None,
) -> MixedArmPlan:
    """Resolve CLI arm flags into build_game arm + optional injection factory."""

    _validate_arm("arm_wolves", arm_wolves)
    _validate_arm("arm_villagers", arm_villagers)
    _validate_arm("arm_gods", arm_gods)
    _validate_arm("arm_civilians", arm_civilians)

    resolved_gods = arm_gods if arm_gods is not None else arm_villagers
    resolved_civilians = (
        arm_civilians if arm_civilians is not None else arm_villagers
    )

    inject_scopes: set[str] = set()
    if arm_wolves == "v1":
        inject_scopes.add("wolves")
    if resolved_gods == "v1":
        inject_scopes.add("gods")
    if resolved_civilians == "v1":
        inject_scopes.add("civilians")

    frozen_scopes = frozenset(inject_scopes)
    if not frozen_scopes:
        arm = "v0"
        factory = None
    elif frozen_scopes == ALL_SCOPES:
        arm = "v1"
        factory = None
    else:
        arm = "v1"
        factory = _make_combined_factory(frozen_scopes)

    return MixedArmPlan(
        arm=arm,
        inject_scopes=frozen_scopes,
        belief_inject_filter_factory=factory,
        agent_version=encode_agent_version(arm, frozen_scopes),
    )


def _make_llm_agent_factory(provider, template_name: str = "v1_belief_llm") -> AgentFactory:
    def factory(
        trace_store: TraceStore,
        agent_version: str,
        temperature: float,
        retry_backoff: float,
    ) -> LLMAgent:
        return LLMAgent(
            provider,
            model_config={"temperature": temperature},
            retry_backoff_seconds=retry_backoff,
            trace_store=trace_store,
            template_name=template_name,
            agent_version=agent_version,
        )

    return factory


def _make_mock_agent_factory() -> AgentFactory:
    def factory(
        _trace_store: TraceStore,
        _agent_version: str,
        _temperature: float,
        _retry_backoff: float,
    ) -> RoleStrategyMockAgent:
        return RoleStrategyMockAgent()

    return factory


def _build_provider(model_flavor: str = "PRO") -> tuple[ArkLLMProvider, str, str]:
    _load_local_env(_LOCAL_ENV)
    from runner.launcher import build_provider as _launcher_build_provider

    provider = _launcher_build_provider(model_flavor)
    endpoint_id = provider._endpoint_id  # type: ignore[attr-defined]
    base_url = provider._base_url  # type: ignore[attr-defined]
    return provider, endpoint_id, base_url


def _resolve_injected_agents(
    built, game_id: str, factory: InjectFactory | None, arm: str
) -> list[str]:
    """注入 belief 的 agent_id 列表 —— v0 为空；factory=None 的 v1 是全员。"""
    players = built.engine.get_session(game_id).truth_state.players
    if arm == "v0":
        return []
    if factory is None:
        return list(players)
    filt = factory(built.engine, game_id)
    return [player_id for player_id in players if filt(player_id)]


def _trace_count(trace_store: TraceStore, game_id: str) -> int:
    return len(trace_store.list_by_game(game_id))


async def _run_one_game_async(
    *,
    agent_factory: AgentFactory,
    plan: MixedArmPlan,
    seed: int,
    game_id: str,
    temperature: float,
    retry_backoff: float,
    belief_kernel: str = "additive_v1",
    slow_think: str = "off",
    reflect_max: int = 8,
    reflector_factory: ReflectorFactory | None = None,
    trace_dir: Path | None = None,
    belief_dir: Path | None = None,
    event_dir: Path | None = None,
    prompt_template: str = "v1_belief_llm",
) -> tuple[GameRunResult, dict]:
    """Run one isolated game (awaitable) and return its contract result plus extras.

    每局自带独立 engine / stores / agent，互不共享可变状态，因此可被并发调度
    （``run_batch(concurrency>1)`` 用 ``asyncio.Semaphore`` 限并发）。共享的
    ``ArkLLMProvider``（httpx）支持并发请求。
    """

    config = _load_config(game_id)
    if event_dir is not None:
        event_dir.mkdir(parents=True, exist_ok=True)
    event_store = JsonlEventStore(event_dir) if event_dir is not None else InMemoryEventStore()
    trace_store: TraceStore = (
        JsonlTraceStore(trace_dir) if trace_dir is not None else InMemoryTraceStore()
    )
    agent = agent_factory(
        trace_store,
        plan.agent_version,
        temperature,
        retry_backoff,
    )
    # belief_dir → 每局独立 JsonlBeliefStateStore，落 <belief_dir>/<game_id>/<agent>/。
    # 只对启用 belief lane 的 arm（v1）注入：v0 无 belief lane，builder 守卫禁止注 store。
    # 各局 game_id 唯一 → 写不同子目录，并发安全（同 trace_store 的 per-game 隔离）。
    belief_store = (
        JsonlBeliefStateStore(belief_dir)
        if belief_dir is not None and plan.arm == "v1"
        else None
    )
    slow_think_policy = reflector_factory() if reflector_factory is not None else None
    started = time.perf_counter()
    crash: GameRunError | None = None
    built = build_game(
        config=config,
        agent=agent,
        arm=plan.arm,
        seed=seed,
        belief_inject_filter_factory=plan.belief_inject_filter_factory,
        event_observer=None,
        event_store=event_store,
        trace_store=trace_store,
        belief_kernel=belief_kernel,
        slow_think_policy=slow_think_policy,
        belief_store=belief_store,
    )
    try:
        await built.supervisor.run_game(config.game_id)
    except GameRunError as exc:
        crash = exc

    runtime_ms = (time.perf_counter() - started) * 1000.0
    session = built.engine.get_session(config.game_id)
    events = built.stores.event_store.list_by_game(config.game_id)
    game_over = next((e for e in events if e.event_type == EventType.GAME_OVER), None)
    belief_update_errors = len(getattr(built.supervisor, "_belief_update_errors", []))
    completed = (
        session.current_phase == Phase.GAME_OVER
        and not (plan.arm == "v1" and belief_update_errors > 0)
    )
    status = "completed" if completed else "failed"
    result = GameRunResult(
        game_id=config.game_id,
        status=status,
        winner=(game_over.payload.get("winner") if game_over else None),
        rounds=session.round,
        runtime_ms=runtime_ms,
        error_type=(
            "belief_update_failed"
            if plan.arm == "v1" and belief_update_errors > 0
            else (
                type(crash.__cause__).__name__
                if crash and crash.__cause__
                else (
                    crash.reason
                    if crash
                    else (None if session.current_phase == Phase.GAME_OVER else "phase_not_terminal")
                )
            )
        ),
        error_phase=(crash.phase if crash else None),
        error_actor=(crash.actor if crash else None),
        error_message=(
            f"{belief_update_errors} belief updater errors"
            if plan.arm == "v1" and belief_update_errors > 0
            else (str(crash.__cause__ or crash)[:200] if crash else None)
        ),
    )
    injected_agents = _resolve_injected_agents(
        built, config.game_id, plan.belief_inject_filter_factory, plan.arm
    )
    extra = {
        "game_id": config.game_id,
        "seed": seed,
        "agent_version": plan.agent_version,
        "arm": plan.arm,
        "belief_kernel": belief_kernel,
        "slow_think": slow_think,
        "reflect_max": reflect_max,
        "slow_think_stats": _slow_think_stats(slow_think_policy),
        "status": result.status,
        "winner": result.winner,
        "rounds": result.rounds,
        "runtime_ms": result.runtime_ms,
        "prompt_template_name": prompt_template,
        "prompt_profile": (
            f"{prompt_template}:no-belief-fallback"
            if plan.agent_version == "v0"
            else prompt_template
        ),
        "inject_scopes": sorted(plan.inject_scopes),
        "injected_agent_count": len(injected_agents),
        "player_count": len(session.truth_state.players),
        "events": len(events),
        "trace_count": _trace_count(trace_store, config.game_id),
        "belief_update_errors": belief_update_errors,
    }
    # PR-A-2 followup：补强 metrics（工程 / 产品 / 数学 / 算法 四视角），全写 sidecar，
    # 一行不动 contracts。详见 scripts/_mixed_metrics.py。
    extra.update(
        compute_mixed_metrics(
            built=built,
            agent=agent,
            game_id=config.game_id,
            injected_agents=injected_agents,
            arm=plan.arm,
        )
    )
    return result, extra


def run_one_game(**kwargs) -> tuple[GameRunResult, dict]:
    """Sync wrapper around :func:`_run_one_game_async`（保留给单局/外部调用）。"""
    return asyncio.run(_run_one_game_async(**kwargs))


def _slow_think_stats(slow_think_policy) -> dict[str, int]:
    raw = getattr(slow_think_policy, "stats", None) or {}
    return {
        "reflections": int(raw.get("reflections", 0)),
        "reflect_errors": int(raw.get("reflect_errors", 0)),
        "reflect_llm_errors": int(raw.get("reflect_llm_errors", 0)),
        "reflect_parse_errors": int(raw.get("reflect_parse_errors", 0)),
        "applied": int(raw.get("applied", 0)),
    }


def _aggregate(
    results: list[GameRunResult],
    *,
    seed_start: int,
    temperature: float,
    endpoint_id: str,
    agent_version: str,
    prompt_template: str = "v1_belief_llm",
) -> BatchRunReport:
    completed = [r for r in results if r.status == "completed"]
    failed = [r for r in results if r.status != "completed"]
    runtimes = [r.runtime_ms for r in results if r.runtime_ms is not None]
    snapshot = RunConfigSnapshot(
        contract_version=CONTRACT_VERSION,
        game_config_id="game_config_9p_mvp",
        agent_version=agent_version,
        # 用实际模板填，避免 treatment(consume 变体) 报告被误标成 baseline
        strategy_profile_id=(
            f"{prompt_template}:no-belief-fallback"
            if agent_version == "v0"
            else prompt_template
        ),
        model_provider="ark",
        endpoint_id=endpoint_id,
        temperature=temperature,
        max_tokens=_DEFAULT_MAX_TOKENS,
        timeout_seconds=_DEFAULT_TIMEOUT,
        prompt_version_id=f"<role>:{prompt_template}",
        seed=seed_start,
        created_at=_now(),
    )
    return BatchRunReport(
        total=len(results),
        completed=len(completed),
        failed=len(failed),
        avg_runtime_ms=(statistics.mean(runtimes) if runtimes else None),
        winner_distribution=dict(Counter(r.winner for r in results if r.winner)),
        error_count=sum(1 for r in results if r.error_type),
        failed_game_ids=[r.game_id for r in failed],
        representative_failed_runs=failed[:3],
        run_config_snapshot=snapshot,
    )


def run_batch(
    *,
    arm_wolves: str,
    arm_villagers: str,
    games: int,
    seed_start: int = 200,
    temperature: float = 0.6,
    retry_backoff: float = 0.5,
    trace_dir: Path | None = None,
    belief_dir: Path | None = None,
    event_dir: Path | None = None,
    arm_gods: str | None = None,
    arm_civilians: str | None = None,
    provider=None,
    agent_factory: AgentFactory | None = None,
    endpoint_id: str = "",
    concurrency: int = 1,
    belief_kernel: str = "additive_v1",
    slow_think: str = "off",
    reflect_max: int = 8,
    reflector_factory: ReflectorFactory | None = None,
    model_flavor: str = "PRO",
    prompt_template: str = "v1_belief_llm",
) -> tuple[BatchRunReport, list[dict]]:
    if games < 1:
        raise ValueError("games must be >= 1")
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    if slow_think not in {"off", "on"}:
        raise ValueError("slow_think must be 'off' or 'on'")
    if reflect_max < 1:
        raise ValueError("reflect_max must be >= 1")
    concurrency = min(concurrency, games)

    plan = resolve_mixed_arm(
        arm_wolves=arm_wolves,
        arm_villagers=arm_villagers,
        arm_gods=arm_gods,
        arm_civilians=arm_civilians,
    )
    if trace_dir is not None:
        trace_dir.mkdir(parents=True, exist_ok=True)
    if belief_dir is not None:
        belief_dir.mkdir(parents=True, exist_ok=True)
    if belief_dir is not None and plan.arm == "v0":
        warnings.warn(
            "belief_dir set but resolved arm=v0 has no belief lane; nothing will be persisted",
            RuntimeWarning,
            stacklevel=2,
        )

    if agent_factory is None:
        if provider is None:
            provider, endpoint_id, _base_url = _build_provider(model_flavor)
        agent_factory = _make_llm_agent_factory(provider, prompt_template)
    if slow_think == "on" and reflector_factory is None:
        if provider is None:
            provider, endpoint_id, _base_url = _build_provider(model_flavor)
        reflector_factory = lambda: LLMSlowThinkReflector(
            provider, max_reflections=reflect_max
        )
    if slow_think == "on" and plan.arm == "v0":
        warnings.warn(
            "slow_think=on has no belief lane for resolved arm=v0; reflector will no-op",
            RuntimeWarning,
            stacklevel=2,
        )

    print(
        f"开始 mixed 批量: games={games} seed={seed_start}..{seed_start + games - 1} "
        f"arm={plan.arm} inject_scopes={sorted(plan.inject_scopes)} "
        f"agent_version={plan.agent_version} concurrency={concurrency} "
        f"belief_kernel={belief_kernel} slow_think={slow_think} "
        f"reflect_max={reflect_max}",
        flush=True,
    )

    # 按 seed 顺序索引落位，并发完成乱序不影响 results/extras 的稳定顺序。
    results: list[GameRunResult | None] = [None] * games
    extras: list[dict | None] = [None] * games

    def _report_done(i: int, result: GameRunResult, extra: dict) -> None:
        dstats = extra.get("decision_stats", {})
        print(
            f"  [{i + 1}/{games}] {extra['game_id']} -> {result.status} | "
            f"winner={result.winner} | rounds={result.rounds} | "
            f"{result.runtime_ms:.0f}ms | "
            f"injected={extra['injected_agent_count']}/{extra['player_count']} | "
            f"traces={extra['trace_count']} | events={extra['events']} | "
            f"retry={dstats.get('retry', '-')} "
            f"llm_error={dstats.get('llm_error', '-')} | "
            f"belief_update_errors={extra['belief_update_errors']}",
            flush=True,
        )
        if result.status != "completed":
            print(
                f"         [FAIL] error_type={result.error_type} "
                f"phase={result.error_phase} actor={result.error_actor}\n"
                f"         message={result.error_message}",
                flush=True,
            )

    async def _drive() -> None:
        sem = asyncio.Semaphore(concurrency)

        async def _one(i: int) -> None:
            seed = seed_start + i
            game_id = f"mixed_batch_{seed:05d}"
            async with sem:
                print(f"  [{i + 1}/{games}] 启动 {game_id} (seed={seed}) ...", flush=True)
                result, extra = await _run_one_game_async(
                    agent_factory=agent_factory,
                    plan=plan,
                    seed=seed,
                    game_id=game_id,
                    temperature=temperature,
                    retry_backoff=retry_backoff,
                    belief_kernel=belief_kernel,
                    slow_think=slow_think,
                    reflect_max=reflect_max,
                    reflector_factory=(reflector_factory if slow_think == "on" else None),
                    trace_dir=trace_dir,
                    belief_dir=belief_dir,
                    event_dir=event_dir,
                    prompt_template=prompt_template,
                )
            results[i] = result
            extras[i] = extra
            _report_done(i, result, extra)

        await asyncio.gather(*(_one(i) for i in range(games)))

    asyncio.run(_drive())

    final_results = [r for r in results if r is not None]
    final_extras = [e for e in extras if e is not None]
    return (
        _aggregate(
            final_results,
            seed_start=seed_start,
            temperature=temperature,
            endpoint_id=endpoint_id,
            agent_version=plan.agent_version,
            prompt_template=prompt_template,
        ),
        final_extras,
    )


def _print_report(report: BatchRunReport, extras: list[dict]) -> None:
    agent_version = (
        report.run_config_snapshot.agent_version
        if report.run_config_snapshot is not None
        else "unknown"
    )
    print("\n" + "=" * 60)
    print(
        f"mixed belief 批量结果 total={report.total} completed={report.completed} "
        f"failed={report.failed} agent_version={agent_version} "
        f"belief_kernel={_report_belief_kernel(extras)} "
        f"slow_think={_report_slow_think(extras)}"
    )
    print("=" * 60)
    print(f"赢家分布: {report.winner_distribution}")
    print(f"平均时长: {report.avg_runtime_ms:.0f}ms" if report.avg_runtime_ms else "平均时长: -")
    print(
        "belief 注入人数: "
        + ", ".join(
            f"{e['injected_agent_count']}/{e['player_count']}" for e in extras
        )
    )
    print(f"trace 落盘总条数: {sum(e['trace_count'] for e in extras)}")
    print(f"belief update errors: {sum(e['belief_update_errors'] for e in extras)}")
    if report.failed_game_ids:
        print(f"失败局: {report.failed_game_ids}")
    print("=" * 60)


def _report_belief_kernel(extras: list[dict]) -> str:
    kernels = sorted({str(e.get("belief_kernel", "unknown")) for e in extras})
    return ",".join(kernels) if kernels else "unknown"


def _report_slow_think(extras: list[dict]) -> str:
    modes = sorted({str(e.get("slow_think", "unknown")) for e in extras})
    return ",".join(modes) if modes else "unknown"


def _default_extras_path(out_path: Path) -> Path:
    suffix = out_path.suffix or ".json"
    stem = out_path.stem if out_path.suffix else out_path.name
    return out_path.with_name(f"{stem}.extras{suffix}")


def _batch_dir(batch_id: str) -> Path:
    """``--batch-id`` 的落盘目录：``<AI_WOLF_BATCH_DIR>/<batch-id>/``。

    与 ``api/audit_batch_service._batch_root`` 同口径（默认 ``<AI_WOLF_DATA_DIR>/batches``）；
    scripts 不依赖 api，故在此各自解析同一组环境变量。

    ``batch_id`` 落文件系统 + 作为端点的 run_id，写入侧用与 ``get_batch_report`` 同一套
    安全目录名规则（拒路径分隔符 / 保留段），防写到根外或建出端点扫不到的目录。
    """
    if "/" in batch_id or "\\" in batch_id or batch_id in ("", ".", ".."):
        raise ValueError(f"invalid --batch-id for filesystem: {batch_id!r}")
    root = os.getenv("AI_WOLF_BATCH_DIR")
    base = Path(root) if root else Path(os.getenv("AI_WOLF_DATA_DIR", "./data")) / "batches"
    return base / batch_id


def _write_extras(path: Path, extras: list[dict]) -> None:
    path.write_text(json.dumps(extras, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 6 mixed belief 批量运行器")
    parser.add_argument("--arm-wolves", choices=("v0", "v1"), required=True)
    parser.add_argument("--arm-villagers", choices=("v0", "v1"), required=True)
    parser.add_argument("--arm-gods", choices=("v0", "v1"), default=None)
    parser.add_argument("--arm-civilians", choices=("v0", "v1"), default=None)
    parser.add_argument("--games", type=int, required=True)
    parser.add_argument("--seed-start", type=int, default=200)
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--retry-backoff", type=float, default=0.5)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help=(
            "同时并发的对局数（asyncio semaphore 限流）。默认 1=串行。并发只增加对 LLM "
            "endpoint 的并发请求数，受其 RPM/TPM 配额约束；先小（如 4）爬坡观察每局 "
            "decision_stats.retry / llm_error，无飙升再加。"
        ),
    )
    parser.add_argument("--out", default="mixed_batch_report.json")
    parser.add_argument(
        "--extras-out",
        default=None,
        help=(
            "把每局 mixed 审计 sidecar 写到此路径；默认随 --out 生成 "
            "<stem>.extras<suffix>。"
        ),
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        help=(
            "可选：把 report.json + report.extras.json 落到 "
            "<AI_WOLF_BATCH_DIR>/<batch-id>/（默认 <AI_WOLF_DATA_DIR>/batches），"
            "让 /api/audit/batches 端点直接扫到。覆盖 --out / --extras-out。"
        ),
    )
    parser.add_argument("--trace-dir", default=None)
    parser.add_argument(
        "--event-dir",
        default=None,
        help=(
            "把每局 events 落到此目录（<dir>/<game_id>.jsonl），"
            "供 /api/audit/runs 端点扫到并展示在审计平台。"
            "指向 <AI_WOLF_DATA_DIR>/events 即与 API 的 event store 同根。"
        ),
    )
    parser.add_argument(
        "--belief-dir",
        default=None,
        help=(
            "把每局 belief 历史落到此目录（<dir>/<game_id>/<agent>/{real,shadow}.jsonl），"
            "供 /api/audit/runs/{id}/belief_curve 端点读取。指向 "
            "<AI_WOLF_DATA_DIR>/belief_states 即与 API 的 belief store 同根；端点在 "
            "AI_WOLF_STORAGE_BACKEND=jsonl 下按请求读盘（写完即可读，无需重启 API）。"
            "仅 v1 arm 有 belief lane；v0 不落。"
        ),
    )
    parser.add_argument(
        "--belief-kernel",
        choices=("additive_v1", "factorized_v2"),
        default="additive_v1",
        help="belief updater 内核；默认 additive_v1 保持现有批跑行为。",
    )
    parser.add_argument(
        "--slow-think",
        choices=("off", "on"),
        default="off",
        help="是否启用 M4 System2 慢思反思；默认 off 保持现有批跑行为。",
    )
    parser.add_argument(
        "--reflect-max",
        type=int,
        default=8,
        help="slow-think 每局最多反思次数；默认 8。",
    )
    parser.add_argument(
        "--model-flavor",
        choices=("PRO", "CODE", "DEEPSEEK"),
        default="PRO",
        help="PRO/CODE=火山 Doubao；DEEPSEEK=DeepSeek 官方 API（需 DEEPSEEK_API_KEY）",
    )
    parser.add_argument(
        "--prompt-template",
        choices=("v1_belief_llm", "v1_belief_consume_llm"),
        default="v1_belief_llm",
        help="LLM agent 的 belief 模板。默认 v1_belief_llm(基线)；"
        "v1_belief_consume_llm=段2 消费纪律变体(强制投票对齐头号嫌疑+override_reason)。"
        "变体进 prompt_version_id → trace 可区分，便于离线分析。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    trace_dir = Path(args.trace_dir) if args.trace_dir else None
    belief_dir = Path(args.belief_dir) if args.belief_dir else None
    event_dir = Path(args.event_dir) if args.event_dir else None
    if args.batch_id:
        # 端点扫描 <root>/<run_id>/report.json 配对 report.extras.json，故固定文件名。
        out_path = _batch_dir(args.batch_id) / "report.json"
        extras_path = _default_extras_path(out_path)
    else:
        out_path = Path(args.out)
        extras_path = (
            Path(args.extras_out) if args.extras_out else _default_extras_path(out_path)
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    extras_path.parent.mkdir(parents=True, exist_ok=True)
    report, extras = run_batch(
        arm_wolves=args.arm_wolves,
        arm_villagers=args.arm_villagers,
        arm_gods=args.arm_gods,
        arm_civilians=args.arm_civilians,
        games=args.games,
        seed_start=args.seed_start,
        temperature=args.temperature,
        retry_backoff=args.retry_backoff,
        trace_dir=trace_dir,
        belief_dir=belief_dir,
        event_dir=event_dir,
        concurrency=args.concurrency,
        belief_kernel=args.belief_kernel,
        slow_think=args.slow_think,
        reflect_max=args.reflect_max,
        model_flavor=args.model_flavor,
        prompt_template=args.prompt_template,
    )
    _print_report(report, extras)
    out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    _write_extras(extras_path, extras)
    print(f"BatchRunReport 已写入 {out_path}")
    print(f"mixed extras 已写入 {extras_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
