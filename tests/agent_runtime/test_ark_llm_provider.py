"""ArkLLMProvider 单元测试 —— mock httpx.MockTransport, 不打真实网络。

覆盖：
- 构造校验（空 api_key / 空 endpoint_id）
- generate happy path：请求 body / headers 正确，response 解析正确
- model_config 覆盖（temperature/max_tokens/top_p/stop/endpoint_id/model_name）
- 错误：HTTP 4xx/5xx / 非 JSON / 缺 choices / 网络异常
- from_env 读 ARK_<FLAVOR>_API_KEY/ENDPOINT_ID + ARK_BASE_URL
- 注入式 http_client 复用同一 mock transport（不 own / 不关）
- token_usage 解析（含缺失 usage 时全 0）

不跑真实 Ark：smoke 集成测试见 scripts/test_ark_llms.py（B 5/26 18:48）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from agent_runtime.ark_llm_provider import (
    ArkLLMError,
    ArkLLMProvider,
)


# ---------------------------------------------------------------------------
# Helpers: build mock httpx clients
# ---------------------------------------------------------------------------


def _make_mock_response(
    *,
    content: str = "hello",
    status: int = 200,
    body: dict | None = None,
    raw_text: str | None = None,
    usage: dict | None = None,
) -> httpx.Response:
    """造一个 chat/completions 风格的 mock httpx.Response。"""
    if raw_text is not None:
        return httpx.Response(status, content=raw_text.encode("utf-8"))
    if body is None:
        body = {
            "id": "chatcmpl-xxx",
            "model": "ep-mock-pro",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": usage or {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46},
        }
    return httpx.Response(status, json=body)


def _make_client(
    handler,
    captured: list[httpx.Request] | None = None,
) -> httpx.AsyncClient:
    def _transport_handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return handler(request)
    transport = httpx.MockTransport(_transport_handler)
    return httpx.AsyncClient(transport=transport, timeout=5.0)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key"):
            ArkLLMProvider(api_key="", endpoint_id="ep-1")

    def test_requires_endpoint_id(self):
        with pytest.raises(ValueError, match="endpoint_id"):
            ArkLLMProvider(api_key="k", endpoint_id="")

    def test_defaults(self):
        p = ArkLLMProvider(api_key="k", endpoint_id="ep-1")
        assert p._base_url == "https://ark.cn-beijing.volces.com/api/v3"
        assert p._default_temperature == 0.6
        assert p._default_max_tokens == 1024


# ---------------------------------------------------------------------------
# from_env
# ---------------------------------------------------------------------------


class TestFromEnv:
    def test_reads_pro_keys(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARK_PRO_API_KEY", "my-pro-key")
        monkeypatch.setenv("ARK_PRO_ENDPOINT_ID", "ep-pro-123")
        monkeypatch.setenv("ARK_BASE_URL", "https://custom-base/v3")
        p = ArkLLMProvider.from_env("PRO")
        assert p._api_key == "my-pro-key"
        assert p._endpoint_id == "ep-pro-123"
        assert p._base_url == "https://custom-base/v3"
        assert p._model_name == "doubao-pro"

    def test_reads_code_keys(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARK_CODE_API_KEY", "my-code-key")
        monkeypatch.setenv("ARK_CODE_ENDPOINT_ID", "ep-code-999")
        monkeypatch.delenv("ARK_BASE_URL", raising=False)
        p = ArkLLMProvider.from_env("CODE")
        assert p._api_key == "my-code-key"
        assert p._endpoint_id == "ep-code-999"
        assert p._model_name == "doubao-code"
        # 缺 ARK_BASE_URL 时退回默认
        assert p._base_url == "https://ark.cn-beijing.volces.com/api/v3"

    def test_missing_env_raises_value_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_PRO_API_KEY", raising=False)
        monkeypatch.delenv("ARK_PRO_ENDPOINT_ID", raising=False)
        with pytest.raises(ValueError):
            ArkLLMProvider.from_env("PRO")


# ---------------------------------------------------------------------------
# generate happy path + request shape
# ---------------------------------------------------------------------------


class TestGenerateHappyPath:
    def test_returns_llm_response_with_content(self):
        client = _make_client(lambda r: _make_mock_response(content="刀 P3"))
        provider = ArkLLMProvider(
            api_key="key-1", endpoint_id="ep-prod", http_client=client
        )
        resp = asyncio.run(
            provider.generate(
                [{"role": "user", "content": "hi"}],
                {"model_name": "doubao-pro"},
            )
        )
        assert resp.raw_output == "刀 P3"
        assert resp.model_name == "doubao-pro"
        assert resp.token_usage["prompt_tokens"] == 12
        assert resp.token_usage["completion_tokens"] == 34
        assert resp.token_usage["total_tokens"] == 46
        assert resp.metadata["provider_kind"] == "ark"
        assert resp.metadata["endpoint_id"] == "ep-prod"
        assert resp.latency_ms is not None and resp.latency_ms > 0

    def test_request_uses_endpoint_id_as_model(self):
        captured: list[httpx.Request] = []
        client = _make_client(lambda r: _make_mock_response(), captured=captured)
        provider = ArkLLMProvider(
            api_key="key-1", endpoint_id="ep-prod-pro", http_client=client
        )
        asyncio.run(
            provider.generate([{"role": "user", "content": "x"}], {})
        )
        assert len(captured) == 1
        body = json.loads(captured[0].content)
        # model 字段 = endpoint_id（Ark 协议特点）
        assert body["model"] == "ep-prod-pro"
        assert body["messages"] == [{"role": "user", "content": "x"}]
        # 默认采样
        assert body["temperature"] == 0.6
        assert body["max_tokens"] == 1024

    def test_request_carries_bearer_auth_and_json_content_type(self):
        captured: list[httpx.Request] = []
        client = _make_client(lambda r: _make_mock_response(), captured=captured)
        provider = ArkLLMProvider(
            api_key="test-credential", endpoint_id="ep-1", http_client=client
        )
        asyncio.run(provider.generate([{"role": "user", "content": "x"}], {}))
        req = captured[0]
        assert req.headers["Authorization"] == "Bearer test-credential"
        assert req.headers["Content-Type"] == "application/json"

    def test_request_targets_chat_completions_url(self):
        captured: list[httpx.Request] = []
        client = _make_client(lambda r: _make_mock_response(), captured=captured)
        provider = ArkLLMProvider(
            api_key="k", endpoint_id="ep-1",
            base_url="https://custom-base/v3", http_client=client,
        )
        asyncio.run(provider.generate([{"role": "user", "content": "x"}], {}))
        assert str(captured[0].url) == "https://custom-base/v3/chat/completions"

    def test_base_url_trailing_slash_stripped(self):
        captured: list[httpx.Request] = []
        client = _make_client(lambda r: _make_mock_response(), captured=captured)
        provider = ArkLLMProvider(
            api_key="k", endpoint_id="ep-1",
            base_url="https://x/v3/", http_client=client,
        )
        asyncio.run(provider.generate([{"role": "user", "content": "x"}], {}))
        assert str(captured[0].url) == "https://x/v3/chat/completions"


# ---------------------------------------------------------------------------
# model_config overrides
# ---------------------------------------------------------------------------


class TestModelConfigOverrides:
    def setup_method(self) -> None:
        self.captured: list[httpx.Request] = []
        self.client = _make_client(lambda r: _make_mock_response(), captured=self.captured)
        self.provider = ArkLLMProvider(
            api_key="k", endpoint_id="ep-default", http_client=self.client
        )

    def _body(self) -> dict:
        return json.loads(self.captured[0].content)

    def test_temperature_override(self):
        asyncio.run(self.provider.generate([{"role": "user", "content": "x"}], {"temperature": 0.1}))
        assert self._body()["temperature"] == 0.1

    def test_max_tokens_override(self):
        asyncio.run(self.provider.generate([{"role": "user", "content": "x"}], {"max_tokens": 64}))
        assert self._body()["max_tokens"] == 64

    def test_top_p_optional_field(self):
        asyncio.run(self.provider.generate([{"role": "user", "content": "x"}], {"top_p": 0.9}))
        assert self._body()["top_p"] == 0.9

    def test_top_p_omitted_when_not_in_config(self):
        asyncio.run(self.provider.generate([{"role": "user", "content": "x"}], {}))
        assert "top_p" not in self._body()

    def test_stop_optional_field(self):
        asyncio.run(self.provider.generate([{"role": "user", "content": "x"}], {"stop": ["END"]}))
        assert self._body()["stop"] == ["END"]

    def test_endpoint_id_override(self):
        asyncio.run(
            self.provider.generate(
                [{"role": "user", "content": "x"}],
                {"endpoint_id": "ep-override"},
            )
        )
        assert self._body()["model"] == "ep-override"

    def test_model_name_override_appears_in_response(self):
        resp = asyncio.run(
            self.provider.generate(
                [{"role": "user", "content": "x"}],
                {"model_name": "custom-name"},
            )
        )
        assert resp.model_name == "custom-name"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_http_4xx_raises_ark_llm_error_with_status(self):
        client = _make_client(
            lambda r: _make_mock_response(status=401, body={"error": "unauthorized"})
        )
        provider = ArkLLMProvider(api_key="k", endpoint_id="ep-1", http_client=client)
        with pytest.raises(ArkLLMError) as exc_info:
            asyncio.run(provider.generate([{"role": "user", "content": "x"}], {}))
        assert exc_info.value.status == 401
        assert "unauthorized" in (exc_info.value.response_body or "")

    def test_http_5xx_raises_ark_llm_error(self):
        client = _make_client(
            lambda r: _make_mock_response(status=503, body={"error": "service unavailable"})
        )
        provider = ArkLLMProvider(api_key="k", endpoint_id="ep-1", http_client=client)
        with pytest.raises(ArkLLMError) as exc_info:
            asyncio.run(provider.generate([{"role": "user", "content": "x"}], {}))
        assert exc_info.value.status == 503

    def test_non_json_response_raises_ark_llm_error(self):
        client = _make_client(
            lambda r: _make_mock_response(raw_text="this is not json", status=200)
        )
        provider = ArkLLMProvider(api_key="k", endpoint_id="ep-1", http_client=client)
        with pytest.raises(ArkLLMError, match="not JSON"):
            asyncio.run(provider.generate([{"role": "user", "content": "x"}], {}))

    def test_missing_choices_raises_ark_llm_error(self):
        client = _make_client(lambda r: _make_mock_response(body={"id": "x", "choices": []}))
        provider = ArkLLMProvider(api_key="k", endpoint_id="ep-1", http_client=client)
        with pytest.raises(ArkLLMError, match="unexpected"):
            asyncio.run(provider.generate([{"role": "user", "content": "x"}], {}))

    def test_missing_content_in_message_raises(self):
        body = {"choices": [{"index": 0, "message": {"role": "assistant"}}]}
        client = _make_client(lambda r: _make_mock_response(body=body))
        provider = ArkLLMProvider(api_key="k", endpoint_id="ep-1", http_client=client)
        with pytest.raises(ArkLLMError):
            asyncio.run(provider.generate([{"role": "user", "content": "x"}], {}))

    def test_network_error_wrapped_in_ark_llm_error(self):
        def _raise(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")
        client = _make_client(_raise)
        provider = ArkLLMProvider(api_key="k", endpoint_id="ep-1", http_client=client)
        with pytest.raises(ArkLLMError, match="transport error"):
            asyncio.run(provider.generate([{"role": "user", "content": "x"}], {}))


# ---------------------------------------------------------------------------
# Token usage parsing
# ---------------------------------------------------------------------------


class TestTokenUsage:
    def test_missing_usage_returns_all_zero(self):
        body = {
            "choices": [{"message": {"content": "ok"}}],
            # no usage key
        }
        client = _make_client(lambda r: _make_mock_response(body=body))
        provider = ArkLLMProvider(api_key="k", endpoint_id="ep-1", http_client=client)
        resp = asyncio.run(provider.generate([{"role": "user", "content": "x"}], {}))
        assert resp.token_usage == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def test_usage_parsed_correctly(self):
        body = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        client = _make_client(lambda r: _make_mock_response(body=body))
        provider = ArkLLMProvider(api_key="k", endpoint_id="ep-1", http_client=client)
        resp = asyncio.run(provider.generate([{"role": "user", "content": "x"}], {}))
        assert resp.token_usage == {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }


# ---------------------------------------------------------------------------
# Integration: registered into LLMProviderRegistry
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    """ArkLLMProvider 应能直接注册进 LLMProviderRegistry，让 Supervisor 路由消费。"""

    def test_registers_and_retrieves(self):
        from agent_runtime.llm_provider import LLMProviderRegistry

        client = _make_client(lambda r: _make_mock_response(content="ok"))
        provider = ArkLLMProvider(api_key="k", endpoint_id="ep-1", http_client=client)

        registry = LLMProviderRegistry()
        registry.register("doubao-pro", provider)

        got = registry.get("doubao-pro")
        assert got is provider

        resp = asyncio.run(got.generate([{"role": "user", "content": "x"}], {"model_name": "doubao-pro"}))
        assert resp.raw_output == "ok"
        assert resp.metadata["provider_kind"] == "ark"
