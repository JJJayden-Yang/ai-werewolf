"""ArkLLMProvider —— 真实 Volcengine Ark / Doubao 接入（OpenAI-compatible）。

A 5/26 18:41 @我 拍板"V0需要接真实的 / 用doubao pro"。本 provider 实装 OpenAI 风格
``POST /chat/completions``，配合 ``LLMProviderRegistry`` 路由后 Supervisor 直接用。

环境变量（与 B 5/26 18:48 推的 ``.env.example`` 升级版对齐，Phase4/Jiangyi:150eaf4）：

- ``ARK_BASE_URL``      —— 通常 ``https://ark.cn-beijing.volces.com/api/v3``
- ``ARK_PRO_API_KEY`` / ``ARK_PRO_ENDPOINT_ID``  —— Doubao Pro
- ``ARK_CODE_API_KEY`` / ``ARK_CODE_ENDPOINT_ID``  —— 备用 Doubao Code（更便宜，大通量压测用）

Ark 协议关键点（参考 B 的 scripts/test_ark_llms.py smoke）：

- ``model`` 字段填 endpoint_id（``ep-...``），不是模型名。
- ``Authorization: Bearer <api_key>`` + ``Content-Type: application/json``。
- 返回结构 OpenAI 兼容：``{choices: [{message: {content: "..."}}], usage: {...}}``。

红线：
- 完整 prompt / 完整 response 不写 EventLog；只透出摘要级 ``LLMResponse``。
- API key 不进 LLMResponse.metadata，只透出 ``provider_kind`` + ``endpoint_id``。
- 重试 / 退避不内嵌，由外层 ``RetryPolicy`` 负责，保持 provider 职责单一。

接 LLMProviderRegistry 的典型 startup：

    registry = LLMProviderRegistry()
    registry.register("doubao-pro", ArkLLMProvider.from_env("PRO"))
    registry.register("doubao-code", ArkLLMProvider.from_env("CODE"))
    # 调用方：provider = registry.get(model_config["model_name"])
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from agent_runtime.exceptions import AgentRuntimeError
from agent_runtime.llm_provider import LLMProvider
from agent_runtime.types import LLMResponse


_DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_TEMPERATURE = 0.6
_DEFAULT_MAX_TOKENS = 1024
_PROVIDER_KIND = "ark"


class ArkLLMError(AgentRuntimeError):
    """Ark API 调用失败（HTTP/JSON/auth/timeout/解析等）。"""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.response_body = response_body


class ArkLLMProvider(LLMProvider):
    """火山引擎 Ark（Doubao 系列）真实接入。

    OpenAI 兼容协议：``POST {base_url}/chat/completions``。
    ``model`` 字段填 endpoint_id（``ep-...``），Bearer auth。
    ``temperature`` / ``max_tokens`` 默认 0.6 / 1024，可被 ``model_config`` 覆盖。
    """

    def __init__(
        self,
        *,
        api_key: str,
        endpoint_id: str,
        base_url: str = _DEFAULT_BASE_URL,
        model_name: str = "doubao-pro",
        default_temperature: float = _DEFAULT_TEMPERATURE,
        default_max_tokens: int = _DEFAULT_MAX_TOKENS,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        default_thinking: dict[str, Any] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be non-empty")
        if not endpoint_id:
            raise ValueError("endpoint_id must be non-empty")
        self._api_key = api_key
        self._endpoint_id = endpoint_id
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._default_temperature = default_temperature
        self._default_max_tokens = default_max_tokens
        self._timeout = timeout_seconds
        # 思考模型（doubao-seed 系列）默认开思考链：9p 大 prompt 下 30s+ 延迟、方差大，
        # 并发下频繁突破 timeout → ReadTimeout → fallback。默认 None=不带该字段（沿用旧行为）；
        # 传 {"type": "disabled"} 可关闭思考（实测 33s→6.7s）。可被 model_config["thinking"] 覆盖。
        self._default_thinking = default_thinking
        # 测试通过 http_client= 注入 ``httpx.MockTransport``；prod 内部按需新建并自动关闭
        self._http_client = http_client

    @classmethod
    def from_env(
        cls,
        flavor: str = "PRO",
        *,
        model_name: str | None = None,
        **kwargs: Any,
    ) -> "ArkLLMProvider":
        """从环境变量读 ``ARK_<FLAVOR>_API_KEY`` / ``ARK_<FLAVOR>_ENDPOINT_ID`` + ``ARK_BASE_URL``。

        flavor: ``"PRO"`` (doubao-pro) | ``"CODE"`` (doubao-code)，呼应 B 5/26 18:48
        推的 env key naming。其它 kwargs 透传给构造函数。
        """
        flavor_upper = flavor.upper()
        api_key = os.getenv(f"ARK_{flavor_upper}_API_KEY", "")
        endpoint_id = os.getenv(f"ARK_{flavor_upper}_ENDPOINT_ID", "")
        base_url = os.getenv("ARK_BASE_URL", _DEFAULT_BASE_URL)
        default_model_name = f"doubao-{flavor.lower()}"
        return cls(
            api_key=api_key,
            endpoint_id=endpoint_id,
            base_url=base_url,
            model_name=model_name or default_model_name,
            **kwargs,
        )

    async def generate(
        self,
        messages: list[dict],
        model_config: dict,
    ) -> LLMResponse:
        """打 Ark ``chat/completions`` 拿一次完成。

        ``model_config`` 可含：
        - ``temperature`` / ``max_tokens`` / ``top_p`` / ``stop`` —— 覆盖默认采样参数
        - ``endpoint_id`` —— 覆盖构造时的 endpoint（同一 provider 切换 endpoint 用）
        - ``model_name`` —— 写到返回 ``LLMResponse.model_name``，便于 trace 追溯

        Raises:
            ArkLLMError: HTTP 4xx/5xx / 网络异常 / JSON 解析失败 / 返回结构异常。
        """
        endpoint_id = model_config.get("endpoint_id", self._endpoint_id)
        payload: dict[str, Any] = {
            "model": endpoint_id,
            "messages": messages,
            "temperature": model_config.get("temperature", self._default_temperature),
            "max_tokens": model_config.get("max_tokens", self._default_max_tokens),
        }
        if "top_p" in model_config:
            payload["top_p"] = model_config["top_p"]
        if "stop" in model_config:
            payload["stop"] = model_config["stop"]
        thinking = model_config.get("thinking", self._default_thinking)
        if thinking is not None:
            payload["thinking"] = thinking

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        started = time.perf_counter()
        client = self._http_client
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise ArkLLMError(
                    f"Ark HTTP transport error: {type(exc).__name__}: {exc}",
                ) from exc

            latency_ms = (time.perf_counter() - started) * 1000.0

            if response.status_code >= 400:
                raise ArkLLMError(
                    f"Ark HTTP {response.status_code}",
                    status=response.status_code,
                    response_body=response.text[:500],
                )

            try:
                data: dict = response.json()
            except ValueError as exc:
                raise ArkLLMError(
                    f"Ark response is not JSON: {exc}",
                    response_body=response.text[:500],
                ) from exc

            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ArkLLMError(
                    f"unexpected Ark response shape: {exc}",
                    response_body=str(data)[:500],
                ) from exc

            usage = data.get("usage") or {}
            token_usage = {
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
                "total_tokens": int(usage.get("total_tokens", 0)),
            }

            return LLMResponse(
                raw_output=content,
                model_name=model_config.get("model_name", self._model_name),
                token_usage=token_usage,
                latency_ms=latency_ms,
                metadata={
                    "provider_kind": _PROVIDER_KIND,
                    "endpoint_id": endpoint_id,
                },
            )
        finally:
            if owns_client:
                await client.aclose()
