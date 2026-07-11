"""RetryPolicy —— Task C4 配套。

LLM 调用 / 解析 / Canonicalize / Validate 各环节的可恢复错误重试策略。
不可恢复或重试耗尽时由 FallbackPolicy 兜底。
"""

from __future__ import annotations


class RetryPolicy:
    def should_retry(self, error: Exception, retry_count: int) -> bool:
        raise NotImplementedError
