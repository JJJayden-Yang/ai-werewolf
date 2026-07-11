"""正式开局入口（CLI）—— 一条命令开一局或批量跑多局，与前端按钮共享 runner.launcher。

用法::

    # mock（不消耗 token，验证链路）
    python scripts/start_game.py --mode mock --player-count 9 --arm v0 --seed 0
    python scripts/start_game.py --mode mock --player-count 9 --games 10 --seed 0

    # 真实 LLM（消耗 token，需 .env / scripts/Yuan_local/.env.local 有 ARK 凭证）
    python scripts/start_game.py --mode llm --arm v0 --player-count 9
    python scripts/start_game.py --mode llm --arm v1 --model-flavor PRO --temperature 0.7

凭证（按优先级读环境变量）::

    ARK_PRO_API_KEY / ARK_PRO_ENDPOINT_ID    # flavor=PRO（默认）
    ARK_CODE_API_KEY / ARK_CODE_ENDPOINT_ID  # flavor=CODE
    ARK_API_KEY / ARK_ENDPOINT_ID            # 旧命名（向后兼容）
    ARK_BASE_URL                             # 默认北京区 v3

红线：真实 API 消耗 token，按需跑。本脚本只组合现有模块，不改契约 / 引擎。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent_runtime import DEFAULT_SOUL_ID  # noqa: E402
from contracts import EventType  # noqa: E402
from runner.launcher import (  # noqa: E402
    LaunchSpec,
    MissingCredentialsError,
    assemble_game,
    load_env_file,
)
from supervisor import GameRunError  # noqa: E402


def _resolve_soul_arg(value: str | None) -> str | None:
    """把 --soul 的字符串值解析成 soul_id；``none/off`` → None（真·无 soul 基线）。"""
    if value is None or str(value).strip().lower() in {"none", "off", ""}:
        return None
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="正式开局入口（mock / 真实 LLM）")
    parser.add_argument("--player-count", type=int, choices=(6, 9), default=9)
    parser.add_argument("--arm", choices=("v0", "v1"), default="v0")
    parser.add_argument(
        "--mode",
        choices=("mock", "llm"),
        default="llm",
        help="mock=不消耗 token（RoleStrategyMockAgent）；llm=真实 Doubao",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument(
        "--model-flavor",
        choices=("PRO", "CODE", "DEEPSEEK"),
        default="PRO",
        help="PRO/CODE=火山 Doubao；DEEPSEEK=DeepSeek 官方 API（需 DEEPSEEK_API_KEY）",
    )
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument("--game-id", default=None)
    parser.add_argument("--games", type=int, default=1, help="批量跑局数；默认 1")
    parser.add_argument(
        "--soul",
        default=DEFAULT_SOUL_ID,
        help="全局人格 soul（default_balanced / cautious / aggressive / logical）；"
        f"默认 {DEFAULT_SOUL_ID}（冷静分析中性人格）。传 `none` 跑真·无 soul 基线。仅 llm mode 生效。",
    )
    parser.add_argument(
        "--strategy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="静态高级策略库：场景命中时注入专家打法片段（对跳/女巫救毒/平票/残局/被指认等）。"
        "**默认开**；纯基线请显式 --no-strategy。仅 llm mode 生效。",
    )
    return parser.parse_args()


def _print_summary(game_id: str, events: list, *, mode: str, arm: str) -> None:
    by_type = Counter(e.event_type for e in events)
    game_over = next((e for e in events if e.event_type == EventType.GAME_OVER), None)
    print("\n" + "=" * 56)
    print(f"开局结果  game_id={game_id}  mode={mode}  arm={arm}")
    print("=" * 56)
    if game_over is not None:
        print(f"赢家: {game_over.payload.get('winner')}  原因: {game_over.payload.get('reason')}")
    else:
        print("赢家: 未产生 GAME_OVER（可能中断）")
    print(f"事件总数: {len(events)}")
    print(f"发言事件: {by_type.get(EventType.SPEECH, 0)}")
    print("=" * 56)


def main() -> None:
    args = _parse_args()
    load_env_file()

    if args.games < 1:
        raise SystemExit("--games must be >= 1")
    if args.games > 1:
        failed = _run_batch(args)
        if failed:
            raise SystemExit(1)
        return

    crash = _run_one(args, seed=args.seed, game_id=args.game_id)
    if crash is not None:
        raise SystemExit(1)


def _run_batch(args: argparse.Namespace) -> int:
    failed = 0
    print(
        f"开始批量: {args.games} 局, seed {args.seed}..{args.seed + args.games - 1} "
        f"mode={args.mode} arm={args.arm}"
    )
    for offset in range(args.games):
        game_id = f"{args.game_id}_{offset:03d}" if args.game_id else None
        crash = _run_one(args, seed=args.seed + offset, game_id=game_id)
        if crash is not None:
            failed += 1
    print("\n" + "=" * 56)
    print(f"批量结束  total={args.games}  failed={failed}")
    print("=" * 56)
    return failed


def _run_one(args: argparse.Namespace, *, seed: int, game_id: str | None) -> GameRunError | None:
    spec = LaunchSpec(
        player_count=args.player_count,
        arm=args.arm,
        mode=args.mode,
        seed=seed,
        temperature=args.temperature,
        model_flavor=args.model_flavor,
        max_rounds=args.max_rounds,
        game_id=game_id,
        soul_id=_resolve_soul_arg(args.soul),
        use_strategy=args.strategy,
    )

    try:
        built, resolved_game_id = assemble_game(spec)
    except MissingCredentialsError as exc:
        raise SystemExit(str(exc)) from exc

    crash: GameRunError | None = None
    try:
        asyncio.run(built.supervisor.run_game(resolved_game_id))
    except GameRunError as exc:
        crash = exc  # 仍打印已积累的事件流

    events = built.stores.event_store.list_by_game(resolved_game_id)
    _print_summary(resolved_game_id, events, mode=args.mode, arm=args.arm)

    if crash is not None:
        print(f"\n[中断] 本局未跑完 —— GameRunError: phase={crash.phase} actor={crash.actor}")
    return crash


if __name__ == "__main__":
    main()
