"""批量开局脚本 —— 基于 runner.launcher.assemble_game，event/trace/belief 自动落盘。

用法::

    # V0 baseline，10 局并发 5
    python scripts/run_batch.py --arm v0 --games 10 --concurrency 5 --model-flavor DEEPSEEK

    # V1 belief（additive kernel）
    python scripts/run_batch.py --arm v1 --games 10 --concurrency 5 --model-flavor DEEPSEEK

    # V2 belief（factorized kernel + slow_think）
    python scripts/run_batch.py --arm v2 --games 10 --concurrency 5 --model-flavor DEEPSEEK

落盘位置由环境变量控制（同 start_game.py / API）::

    AI_WOLF_STORAGE_BACKEND=jsonl
    AI_WOLF_DATA_DIR=./data          # events/ traces/ belief_states/ 都落在这里

审计平台会自动扫描 events/ 目录，重启后端容器后可见。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.runtime import build_replay_truth_store_from_env  # noqa: E402
from runner.launcher import LaunchSpec, MissingCredentialsError, assemble_game, load_env_file  # noqa: E402
from stores.replay_truth_store import build_player_snapshots  # noqa: E402
from supervisor import GameRunError  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量开局（event/trace/belief 自动落盘）")
    parser.add_argument(
        "--arm",
        choices=("v0", "v1", "v2"),
        required=True,
        help="v0=无 belief；v1=additive belief；v2=factorized belief + slow_think",
    )
    parser.add_argument("--games", type=int, required=True, help="总局数")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="同时并发局数（asyncio semaphore），默认 5",
    )
    parser.add_argument("--seed-start", type=int, default=0, help="起始随机种子，默认 0")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument(
        "--model-flavor",
        choices=("PRO", "CODE", "DEEPSEEK"),
        default="PRO",
        help="PRO/CODE=火山 Doubao；DEEPSEEK=DeepSeek 官方 API",
    )
    parser.add_argument("--player-count", type=int, choices=(6, 9), default=9)
    return parser.parse_args()


async def _run_one(
    *,
    index: int,
    total: int,
    spec: LaunchSpec,
    sem: asyncio.Semaphore,
) -> dict:
    async with sem:
        t0 = time.perf_counter()
        game_id = f"batch_{spec.arm}_{spec.seed:05d}"
        spec = LaunchSpec(
            player_count=spec.player_count,
            arm=spec.arm,
            mode=spec.mode,
            seed=spec.seed,
            temperature=spec.temperature,
            model_flavor=spec.model_flavor,
            game_id=game_id,
        )
        print(f"  [{index + 1}/{total}] 启动 {game_id} ...", flush=True)
        try:
            built, resolved_id = assemble_game(spec)
        except MissingCredentialsError as exc:
            print(f"  [{index + 1}/{total}] {game_id} 凭证缺失: {exc}", flush=True)
            return {"game_id": game_id, "status": "cred_error"}

        crash: GameRunError | None = None
        try:
            await built.supervisor.run_game(resolved_id)
        except GameRunError as exc:
            crash = exc

        elapsed = (time.perf_counter() - t0) * 1000
        events = built.stores.event_store.list_by_game(resolved_id)
        from contracts import EventType  # noqa: PLC0415
        game_over = next((e for e in events if e.event_type == EventType.GAME_OVER), None)
        winner = game_over.payload.get("winner") if game_over else None
        session = built.engine.get_session(resolved_id)

        # 落 replay-only 真相快照（与 API 在线对局写到同一位置 DATA_DIR/replay_truth）；
        # 失败不影响批跑结果统计。崩局也存：能记录到目前为止的身份/死亡状态。
        try:
            players = build_player_snapshots(session.truth_state.players)
            if players:
                build_replay_truth_store_from_env().save_players(resolved_id, players)
        except Exception:  # noqa: BLE001 - snapshot failure must not break batch run
            pass

        status = "completed" if not crash else "failed"
        print(
            f"  [{index + 1}/{total}] {resolved_id} -> {status} | "
            f"winner={winner} | rounds={session.round} | {elapsed:.0f}ms",
            flush=True,
        )
        return {"game_id": resolved_id, "status": status, "winner": winner, "rounds": session.round}


def main() -> None:
    args = _parse_args()
    load_env_file()

    print(
        f"批量开局: arm={args.arm} games={args.games} "
        f"concurrency={args.concurrency} seed={args.seed_start}..{args.seed_start + args.games - 1} "
        f"model={args.model_flavor}",
        flush=True,
    )

    base_spec = LaunchSpec(
        player_count=args.player_count,
        arm=args.arm,
        mode="llm",
        temperature=args.temperature,
        model_flavor=args.model_flavor,
    )

    sem = asyncio.Semaphore(args.concurrency)

    async def _drive() -> list[dict]:
        tasks = []
        for i in range(args.games):
            spec = LaunchSpec(
                player_count=base_spec.player_count,
                arm=base_spec.arm,
                mode=base_spec.mode,
                temperature=base_spec.temperature,
                model_flavor=base_spec.model_flavor,
                seed=args.seed_start + i,
                game_id=f"batch_{args.arm}_{args.seed_start + i:05d}",
            )
            tasks.append(_run_one(index=i, total=args.games, spec=spec, sem=sem))
        return await asyncio.gather(*tasks)

    results = asyncio.run(_drive())

    completed = [r for r in results if r["status"] == "completed"]
    failed = [r for r in results if r["status"] != "completed"]
    from collections import Counter  # noqa: PLC0415
    winners = Counter(r.get("winner") for r in completed if r.get("winner"))

    print("\n" + "=" * 56)
    print(f"批量结束  arm={args.arm}  total={len(results)}  completed={len(completed)}  failed={len(failed)}")
    print(f"胜负分布: {dict(winners)}")
    print("=" * 56)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
