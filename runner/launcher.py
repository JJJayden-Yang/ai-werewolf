"""正式开局入口 —— 根据配置造 agent（mock / 真实 LLM）并装配一局游戏。

``runner.build_game`` 刻意 agent-agnostic（只接收造好的 agent）。本模块坐在它上面，
负责"根据 LaunchSpec 选择 agent + 装配"，是 **后端 CLI（scripts/start_game.py）与
前端按钮（POST /games）共享的唯一开局逻辑**。

红线：模型 ID 不硬编码——LLM 凭证只走环境变量 ``ARK_<FLAVOR>_API_KEY`` /
``ARK_<FLAVOR>_ENDPOINT_ID`` / ``ARK_BASE_URL``（见 ``ArkLLMProvider.from_env``），
旧命名 ``ARK_API_KEY`` / ``ARK_ENDPOINT_ID`` 向后兼容。
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from contracts import GameConfig, Role
from runner.builder import BuiltGame, build_game

if TYPE_CHECKING:
    from stores.belief_state_store import BeliefStateStore
    from stores.event_store import EventStore
    from stores.trace_store import TraceStore
    from supervisor.protocols import AgentRuntime

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = _ROOT / "contracts" / "fixtures"
_FIXTURE_BY_COUNT = {
    6: "game_config_6p_debug.json",
    9: "game_config_9p_mvp.json",
}


class MissingCredentialsError(RuntimeError):
    """真实 LLM 模式缺少 ARK 凭证。用普通异常而非 SystemExit，API 进程不能被它干掉。"""


@dataclass
class LaunchSpec:
    """一局游戏的开局配置（核心 + 模型选择）。"""

    player_count: int = 9
    arm: str = "v0"  # "v0" | "v1" | "v2"（v2 = v1 + factorized_v2 kernel + slow_think）
    mode: str = "llm"
    seed: int | None = None
    temperature: float = 0.7
    model_flavor: str = "PRO"
    max_rounds: int | None = None
    game_id: str | None = None
    # phase2 全局人格（None=不注入，向后兼容）；phase3 高级策略库（False=不注入）。
    # 只对 llm mode 生效；mock agent 不吃这两个。
    soul_id: str | None = None
    use_strategy: bool = False
    # 按座位 soul（每个 agent_id 一个 soul_id）；非空时走 SeatSoulAgent，优先于全局 soul_id。
    seat_souls: dict[str, str] | None = None
    human_seat: str | None = None
    human_role: Role | None = None
    human_timeout_seconds: float = 180.0
    # V2 专用：belief kernel 和 slow_think（arm="v2" 时自动设为 factorized_v2 + True）
    belief_kernel: str = "additive_v1"
    slow_think: bool = False


def load_env_file() -> None:
    """把 ``scripts/Yuan_local/.env.local`` 与项目根 ``.env`` 里的 KEY=VALUE 注入环境。

    用 ``os.environ.setdefault`` —— 不覆盖已有环境变量（已显式 export 的优先）。
    """
    for path in (_ROOT / "scripts" / "Yuan_local" / ".env.local", _ROOT / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


# 模型档位：火山引擎 Doubao（PRO/CODE，走 ARK_<FLAVOR>_*）+ DeepSeek 官方 API（走 DEEPSEEK_*）。
# 都用 ArkLLMProvider —— 它本质是通用 OpenAI 兼容客户端（POST {base_url}/chat/completions，
# model 字段 = endpoint_id），只是名字叫 Ark；DeepSeek 把 base_url 指向官方、model 填模型名即可。
_ARK_FLAVORS = {"PRO", "CODE"}
_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
_DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"


def build_provider(model_flavor: str):
    """按模型档位造 provider。``PRO``/``CODE`` → 火山 Doubao；``DEEPSEEK`` → DeepSeek 官方 API。

    凭证只走环境变量；缺失抛 ``MissingCredentialsError``（API 进程不能被 SystemExit 干掉）。
    """
    from agent_runtime import ArkLLMProvider  # 重依赖（httpx）延迟 import

    flavor = model_flavor.upper()

    if flavor == "DEEPSEEK":
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise MissingCredentialsError(
                "缺少 DeepSeek 凭证：请设置 DEEPSEEK_API_KEY（可选 DEEPSEEK_BASE_URL / "
                "DEEPSEEK_MODEL），或放进 .env / scripts/Yuan_local/.env.local。"
            )
        base_url = os.getenv("DEEPSEEK_BASE_URL", _DEEPSEEK_DEFAULT_BASE_URL)
        model = os.getenv("DEEPSEEK_MODEL", _DEEPSEEK_DEFAULT_MODEL)
        return ArkLLMProvider(
            api_key=api_key,
            endpoint_id=model,  # OpenAI 兼容：请求体 model 字段填模型名
            base_url=base_url,
            model_name=model,
            timeout_seconds=60.0,
        )

    # 火山引擎 Doubao（PRO / CODE）
    api_key = os.getenv(f"ARK_{flavor}_API_KEY") or os.getenv("ARK_API_KEY", "")
    endpoint_id = os.getenv(f"ARK_{flavor}_ENDPOINT_ID") or os.getenv("ARK_ENDPOINT_ID", "")
    if not api_key or not endpoint_id:
        raise MissingCredentialsError(
            f"缺少 ARK 凭证：请设置 ARK_{flavor}_API_KEY / ARK_{flavor}_ENDPOINT_ID"
            "（或旧命名 ARK_API_KEY / ARK_ENDPOINT_ID），"
            "或放进 .env / scripts/Yuan_local/.env.local。"
        )
    # 真实 Doubao 偶发慢响应：超时放宽到 60s（默认 30s 易触发 ReadTimeout）。
    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    # doubao-seed-2.0-pro 是思考模型，默认开思考链 → 9p 大 prompt 下 30s+ 延迟、方差大，
    # 并发下频繁突破 60s timeout → fallback。默认关思考（实测 33s→6.7s，且让 v0 回归纯 LLM
    # baseline，不被隐藏 CoT 污染对照）。需保留思考时设 ARK_THINKING=enabled。
    thinking = None if os.getenv("ARK_THINKING", "disabled").lower() == "enabled" else {"type": "disabled"}
    return ArkLLMProvider(
        api_key=api_key,
        endpoint_id=endpoint_id,
        base_url=base_url,
        timeout_seconds=60.0,
        default_thinking=thinking,
    )


def build_agent(
    *,
    mode: str,
    arm: str,
    temperature: float,
    model_flavor: str = "PRO",
    trace_store=None,
    soul_id: str | None = None,
    use_strategy: bool = False,
    seat_souls: dict[str, str] | None = None,
) -> "AgentRuntime":
    """按 mode 造 agent。``mock`` 不消耗 token；``llm`` 走真实 Doubao（ArkLLMProvider）。

    ``soul_id``（phase2 全局人格）/ ``use_strategy``（phase3 高级策略库）/ ``seat_souls``
    （按座位人格）只对 llm 生效；都默认关，不传 = 与基线完全一致（向后兼容，API/mock 不受影响）。
    ``seat_souls`` 非空时走 SeatSoulAgent（每座位独立 soul），并把 strategy_selector 透传给
    它内部的每个 LLMAgent —— 这样"按座位 soul + 策略库"可以同时生效。
    """
    if mode == "mock":
        from agent_policy import RoleStrategyMockAgent

        return RoleStrategyMockAgent()
    if mode != "llm":
        raise ValueError(f"未知 mode: {mode!r}（应为 'mock' 或 'llm'）")

    # 真实 LLM 分支：重依赖（httpx）延迟到此处再 import。
    from agent_runtime import LLMAgent, SeatSoulAgent

    provider = build_provider(model_flavor)
    template_name = "v1_belief_llm" if arm in ("v1", "v2") else "v0_free_llm"
    strategy_selector = None
    if use_strategy:
        from agent_policy.advanced_strategy import StrategySelector

        strategy_selector = StrategySelector()
    kwargs = {
        "model_config": {"temperature": temperature},
        "template_name": template_name,
        "agent_version": arm,
        "trace_store": trace_store,
        "strategy_selector": strategy_selector,
    }
    # 按座位 soul 优先：SeatSoulAgent 内部按 seat 各建一个带对应 soul 的 LLMAgent，
    # 同时吃 strategy_selector（策略库与按座位人格可叠加）。
    if seat_souls:
        return SeatSoulAgent(provider, seat_souls=seat_souls, **kwargs)
    return LLMAgent(provider, soul_id=soul_id, **kwargs)


def assemble_game(
    spec: LaunchSpec,
    *,
    event_store: "EventStore | None" = None,
    trace_store: "TraceStore | None" = None,
    belief_store: "BeliefStateStore | None" = None,
    human_channel=None,
) -> tuple[BuiltGame, str]:
    """根据 LaunchSpec 装配一局游戏，返回 (BuiltGame, game_id)。"""
    game_id = spec.game_id or _new_game_id()
    config = _load_config(spec, game_id)
    if event_store is None:
        event_store = _build_event_store_from_env()
    if trace_store is None:
        trace_store = _build_trace_store_from_env()
    # 只有当启用 Belief（v1）时才初始化 belief_store
    # v2 = v1 内核 + factorized_v2 belief kernel + slow_think
    _internal_arm = "v1" if spec.arm == "v2" else spec.arm
    belief_kernel = "factorized_v2" if spec.arm == "v2" else spec.belief_kernel
    slow_think = True if spec.arm == "v2" else spec.slow_think

    if belief_store is None and _internal_arm == "v1":
        belief_store = _build_belief_store_from_env()

    slow_think_policy = None
    if slow_think and spec.mode == "llm":
        from agent_policy.slow_think_reflector import LLMSlowThinkReflector  # noqa: PLC0415
        provider = build_provider(spec.model_flavor)
        slow_think_policy = LLMSlowThinkReflector(provider)

    agent = build_agent(
        # 记录真实 arm（v2）到 trace.agent_version 供审计标签区分；模板选择对 v2 仍走
        # belief 模板。引擎/belief 内核另用 _internal_arm（见下方 build_game）。
        mode=spec.mode,
        arm=spec.arm,
        temperature=spec.temperature,
        model_flavor=spec.model_flavor,
        trace_store=trace_store,
        soul_id=spec.soul_id,
        use_strategy=spec.use_strategy,
        seat_souls=spec.seat_souls,
    )
    fixed_roles = None
    if spec.human_seat is not None:
        if spec.human_role is None:
            raise ValueError("human_role is required when human_seat is set")
        if human_channel is None:
            from agent_runtime import HumanInputChannel  # noqa: PLC0415

            human_channel = HumanInputChannel()
        from agent_runtime import HumanAgent, PerSeatAgent  # noqa: PLC0415

        agent = PerSeatAgent(
            default_agent=agent,
            overrides={
                spec.human_seat: HumanAgent(
                    spec.human_seat,
                    human_channel,
                    timeout_seconds=spec.human_timeout_seconds,
                )
            },
        )
        fixed_roles = {spec.human_seat: spec.human_role}
    built = build_game(
        config,
        agent,
        arm=_internal_arm,
        seed=spec.seed,
        deliver_witch_kill_info=True,
        event_store=event_store,
        trace_store=trace_store,
        belief_store=belief_store,
        belief_kernel=belief_kernel,
        slow_think_policy=slow_think_policy,
        fixed_roles=fixed_roles,
    )
    return built, game_id


def _build_event_store_from_env() -> "EventStore":
    from api.runtime import build_event_store_from_env  # noqa: PLC0415

    return build_event_store_from_env()


def _build_trace_store_from_env() -> "TraceStore":
    from api.runtime import build_trace_store_from_env  # noqa: PLC0415

    return build_trace_store_from_env()


def _build_belief_store_from_env() -> "BeliefStateStore":
    from api.runtime import build_belief_store_from_env  # noqa: PLC0415

    return build_belief_store_from_env()


def _load_config(spec: LaunchSpec, game_id: str) -> GameConfig:
    try:
        fixture = _FIXTURE_BY_COUNT[spec.player_count]
    except KeyError:
        raise ValueError(f"不支持的 player_count: {spec.player_count}（应为 6 或 9）") from None
    data = json.loads((_FIXTURES / fixture).read_text(encoding="utf-8"))
    data["game_id"] = game_id
    data["agent_version"] = spec.arm
    model_config = dict(data.get("model_config") or {})
    model_config["temperature"] = spec.temperature
    data["model_config"] = model_config
    if spec.max_rounds is not None:
        data["max_rounds"] = spec.max_rounds
    return GameConfig.model_validate(data)


def _new_game_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"g-{stamp}-{secrets.token_hex(3)}"
