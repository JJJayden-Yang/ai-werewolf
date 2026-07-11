"""LLMProvider / FakeLLMProvider / LLMProviderRegistry —— Task C1。

接入 LLM 的统一抽象。Day 1 必须可跑 ``FakeLLMProvider``，让 A/B/C 不依赖真实
doubao 也能跑通薄切片测试；真实 Volcengine Ark / Doubao 接入不阻塞 6 人 Debug MVP，
后续以新增 ``ArkLLMProvider`` 注册进 Registry 的方式接入，对调用方零变更。

模块结构（与 ``finalPlan/Interface_v2_1.md`` §5.4 一致）：

- ``LLMProvider``  对外接口（ABC）；任何具体 provider 必须实现 ``async generate``。
- ``FakeLLMProvider``  确定性 Fake，支持 ``str`` / ``list[str]`` / ``Callable`` 三种响应规格。
- ``LLMProviderRegistry``  按 ``model_name`` 路由到具体 provider 实例。
- 异常见 ``agent_runtime.exceptions``。

红线（与 ``finalPlan/Interface_v2_1.md`` §5.4 / CLAUDE.md 红线对齐）：

- **不硬编码模型 ID**：真实 provider 用环境变量 ARK_API_KEY / ARK_MODEL_ID / ARK_ENDPOINT_ID
  （`.env` 已 gitignore，模板见 `.env.example`）。
- **完整 prompt 不写 EventLog**：只记录 ``prompt_version_id`` 与摘要级 ``input_summary``；
  本模块只产出 ``LLMResponse``，写日志由 Supervisor / Trace 层负责。
- **接口稳定**：``generate`` 的入参形态 (``list[dict] messages`` / ``dict model_config``) 是
  OpenAI-style，所有真实 provider 自己内部转换；调用方（Agent）写一份代码兼容多家。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable

from agent_runtime.exceptions import (
    FakeLLMExhaustedError,
    LLMProviderNotFoundError,
)
from agent_runtime.types import LLMResponse


# FakeLLMProvider 的响应规格：
#   - str         → 每次调用都返回同一段文本（无限）
#   - list[str]   → 按顺序消费；用尽抛 FakeLLMExhaustedError
#   - Callable    → 每次调用即时计算；签名 (messages, model_config) → str | LLMResponse
FakeResponder = Callable[[list[dict], dict], "str | LLMResponse"]
FakeResponses = "str | list[str] | FakeResponder"


# ---------- 接口 ----------


class LLMProvider(ABC):
    """统一的 LLM 接口。

    支持的具体 provider（按文档 §5.4）：

    - Volcengine Ark / Doubao（真实）
    - OpenAI-compatible（真实）
    - ``FakeLLMProvider``（本地确定性）

    具体实现选择由 ``LLMProviderRegistry`` 按 ``model_name`` 路由。
    """

    @abstractmethod
    async def generate(
        self,
        messages: list[dict],
        model_config: dict,
    ) -> LLMResponse:
        """生成一次完成。

        Args:
            messages: OpenAI 风格 ``[{"role": "...", "content": "..."}]``，由
                ``PromptTemplateLoader.render`` 产出。
            model_config: 模型参数与路由信息，至少包含 ``model_name`` 用于上游路由。
                其它键（temperature 等）由具体 provider 取用。

        Returns:
            LLMResponse: 原文 + 模型名 + token 用量 + 延迟 + 自由 metadata。
        """


# ---------- 实现 1：本地 Fake ----------


class FakeLLMProvider(LLMProvider):
    """本地确定性 Fake，不调用任何外部服务。

    用途：
    - A/B/C 互不阻塞地跑测试与"MockAgent 跑通 6 人局"薄切片；
    - ActionParser / Agent 层单元测试时注入可控的 LLM 输出；
    - CI 流水线不依赖 ARK_API_KEY 也能跑全套测试。

    响应规格 ``responses``（由构造时决定，generate 时按规格分派）：

    - ``str``：每次 ``generate`` 都返回同一段文本（无限次）。
    - ``list[str]``：按顺序消费每个元素；超出后抛 ``FakeLLMExhaustedError``，
      暴露"调用次数超过预期"。
    - ``Callable[[messages, model_config], str | LLMResponse]``：
      每次即时计算；返回 ``str`` 自动包成 ``LLMResponse``，返回 ``LLMResponse`` 原样透出。

    其它字段：
    - ``model_name``：写到返回的 ``LLMResponse.model_name``；可被 ``model_config['model_name']``
      覆盖（便于按模型名分桶 trace）。
    - ``latency_ms``：写到返回的 ``LLMResponse.latency_ms``；默认 0.0，不真正 sleep。
    """

    def __init__(
        self,
        responses: FakeResponses,
        *,
        model_name: str = "fake-llm",
        latency_ms: float = 0.0,
    ) -> None:
        # fail fast：构造时就拒掉非法 responses 类型，避免运行到 generate 才崩
        if not (
            isinstance(responses, (str, list)) or callable(responses)  # noqa: UP038
        ):
            raise TypeError(
                f"responses must be str | list[str] | Callable, got {type(responses).__name__}"
            )
        if isinstance(responses, list) and not all(isinstance(x, str) for x in responses):
            raise TypeError("responses list must contain only str")

        self._responses: FakeResponses = responses
        self._call_count: int = 0
        self._model_name = model_name
        self._latency_ms = latency_ms

    @property
    def call_count(self) -> int:
        """已被调用的次数；用于测试断言"FakeLLM 被调了 N 次"。"""
        return self._call_count

    async def generate(
        self,
        messages: list[dict],
        model_config: dict,
    ) -> LLMResponse:
        result = self._pick_response(messages, model_config)
        self._call_count += 1

        if isinstance(result, LLMResponse):
            # 调用方通过 callable 自己构造了完整 LLMResponse —— 原样透出，不二次包装
            return result

        return LLMResponse(
            raw_output=result,
            model_name=model_config.get("model_name", self._model_name),
            token_usage={},  # Fake 不算 token；真实 provider 会填。
            latency_ms=self._latency_ms,
            metadata={"provider_kind": "fake"},
        )

    def _pick_response(
        self,
        messages: list[dict],
        model_config: dict,
    ) -> "str | LLMResponse":
        responses = self._responses
        if isinstance(responses, str):
            return responses
        if callable(responses):
            return responses(messages, model_config)
        # list[str]
        if self._call_count >= len(responses):
            raise FakeLLMExhaustedError(
                called=self._call_count + 1,
                available=len(responses),
            )
        return responses[self._call_count]


# ---------- Registry ----------


class LLMProviderRegistry:
    """按 ``model_name`` 路由到具体 ``LLMProvider`` 实例。

    典型用法（startup 一次注册，之后所有 Agent 路径走同一个 registry）：

    ```python
    registry = LLMProviderRegistry()
    registry.register("fake-llm", FakeLLMProvider(responses="..."))
    registry.register("doubao-pro", ArkLLMProvider(...))  # 未来真实接入

    provider = registry.get(model_config["model_name"])
    resp = await provider.generate(messages, model_config)
    ```

    设计取舍：
    - ``register`` 同名重复**抛错**而非静默覆盖 —— 启动期 typo / 重复 init 是常见 bug，
      静默吞掉会让 Agent 路由到错误 provider；让它早炸更好排查。
      需要替换 provider 的，显式 ``unregister`` 再 ``register``。
    - ``get`` 找不到抛 ``LLMProviderNotFoundError`` 而非 ``KeyError``：
      上游可以一处 catch ``AgentRuntimeError``。
    """

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}

    def register(self, model_name: str, provider: LLMProvider) -> None:
        """注册一个 provider；同名重复抛 ``ValueError``。"""
        if model_name in self._providers:
            raise ValueError(
                f"LLMProvider already registered for model_name={model_name!r};"
                " unregister first if you intend to replace it"
            )
        self._providers[model_name] = provider

    def unregister(self, model_name: str) -> None:
        """移除一个已注册的 provider；未注册的 model_name 也安静通过（幂等）。"""
        self._providers.pop(model_name, None)

    def get(self, model_name: str) -> LLMProvider:
        """按 ``model_name`` 取 provider；未注册抛 ``LLMProviderNotFoundError``。"""
        try:
            return self._providers[model_name]
        except KeyError as exc:
            raise LLMProviderNotFoundError(model_name) from exc

    def __contains__(self, model_name: str) -> bool:
        return model_name in self._providers

    def __len__(self) -> int:
        return len(self._providers)


# ---------- 同步包装（便利方法） ----------


def generate_sync(
    provider: LLMProvider,
    messages: list[dict],
    model_config: dict,
) -> LLMResponse:
    """同步调用 ``provider.generate``。

    给非 async 的脚本 / 一次性命令行小工具 / 简单 smoke 用。
    生产路径走 ``await provider.generate(...)``，不要用这个 —— 它会启一个新 event loop，
    在已有 loop 的 web 框架里会崩。
    """
    return asyncio.run(provider.generate(messages, model_config))
