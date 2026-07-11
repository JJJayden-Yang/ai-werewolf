"""C(Agent Runtime)自己拥有的内部类型。

这些类型不在 contracts/ 冻结清单内，由 C 定义和维护：
- LLMResponse：LLMProvider.generate 的返回包装。
- PromptTemplate：PromptTemplateLoader 的内部表示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    """LLM 调用的返回包装。

    raw_output 为模型原文，token_usage 为粗粒度计费/预算统计，
    model_name / latency_ms 用于 trace 与监控；不直接进入 EventLog 原文，
    只透出到 AgentDecisionTrace.input_summary / decision_output（摘要级）。
    """

    raw_output: str
    model_name: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptTemplate:
    """PromptTemplateLoader 加载到的模板载体。

    完整模板内容不写入 EventLog —— 只记录 prompt_version_id（见 Interface_v2_1 5.5）。
    渲染逻辑由 PromptTemplateLoader.render 完成，输出为 OpenAI 风格的 messages 列表。
    """

    prompt_version_id: str
    role: str
    system_prompt: str
    user_prompt_template: str
    metadata: dict[str, Any] = field(default_factory=dict)
