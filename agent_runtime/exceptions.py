"""agent_runtime/ 共享异常。

各组件（LLMProvider / ActionParser / RetryPolicy 等）的错误用统一基类
``AgentRuntimeError``，调用方一处 catch 即可粗粒度处理，需要时再按具体子类细化。
"""

from __future__ import annotations


class AgentRuntimeError(Exception):
    """所有 agent_runtime 异常的共同基类。"""


class LLMProviderNotFoundError(AgentRuntimeError):
    """``LLMProviderRegistry.get`` 找不到指定 model_name 时抛出。

    一般表示 startup-time 没把目标 provider 注册进去（漏配置 / 拼写错误）。
    """

    def __init__(self, model_name: str) -> None:
        super().__init__(f"no LLMProvider registered for model_name={model_name!r}")
        self.model_name = model_name


class FakeLLMExhaustedError(AgentRuntimeError):
    """``FakeLLMProvider`` 配的 ``list[str]`` 响应被消耗完后又被调用时抛出。

    显式抛 —— 让测试立即暴露"调用次数超过预期"，比无声循环或返回空串更可靠。
    如果就是要"无限同样回答"，构造时传 ``str`` 而不是 ``list[str]``。
    """

    def __init__(self, called: int, available: int) -> None:
        super().__init__(
            f"FakeLLMProvider responses exhausted: called={called} available={available}"
        )
        self.called = called
        self.available = available


class ParseError(AgentRuntimeError):
    """``ActionParser.parse`` 无法把 LLM 原文解析为合法 ``AgentAction`` 时抛出。

    触发条件：
    - JSON 解析失败（含 markdown 包裹后剥离仍非合法 JSON）；
    - 必需字段缺失（如 ``action_type``）；
    - alias 映射后仍非 8 个标准 ``ActionType`` 之一；
    - 从 ``AgentContext`` 补齐 game_id/agent_id/role/phase 后 pydantic 验证失败。

    调用方应捕获后交给 ``RetryPolicy`` 决定是否重试；重试耗尽 / 不可恢复
    则走 ``FallbackPolicy.apply(context, error)`` 兜底，绝不抛穿透 Engine。
    """

    def __init__(self, message: str, *, raw: str | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw
        self.reason = reason


class CanonicalizationError(AgentRuntimeError):
    """``ActionCanonicalizer.canonicalize`` 命中扫描且无法 sanitize 时抛出。

    典型场景：发言中检测到 META_AI / 角色泄漏 / 思维链泄漏，但当前 phase
    不允许 ``speak`` → 无法 sanitize 成中性发言。

    调用方应走 ``FallbackPolicy`` 兜底。
    """

    def __init__(
        self,
        message: str,
        *,
        triggered: str | None = None,
        original_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.triggered = triggered  # "role_leak" / "meta_ai" / "cot_leak" / "unknown_action"
        self.original_message = original_message


class FallbackError(AgentRuntimeError):
    """``FallbackPolicy.apply`` 自身无法产生合法兜底动作时抛出。

    这是"最终安全网破口"，意味着 AgentContext 数据本身有问题
    （如阶段/角色不匹配、可见玩家全死等）。supervisor 不允许吞掉它。
    """

    def __init__(self, message: str, *, phase: str | None = None, role: str | None = None) -> None:
        super().__init__(message)
        self.phase = phase
        self.role = role


class PromptTemplateNotFoundError(AgentRuntimeError):
    """``PromptTemplateLoader.load`` 找不到对应 prompt 文件时抛出。

    一般表示 ``prompt_version_id`` 拼错、对应角色 prompt 文件缺失、或 ``prompts_dir`` 配错。
    """

    def __init__(self, *, prompt_version_id: str, path: str) -> None:
        super().__init__(f"no prompt template for prompt_version_id={prompt_version_id!r} (path={path})")
        self.prompt_version_id = prompt_version_id
        self.path = path
