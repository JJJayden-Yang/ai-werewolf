"""实验臂（arm）归因 —— 把一局 / 一条 trace 判成 v0 / v1 / v2 / mixed / unknown。

为什么单独成模块、为什么这么判（见 ``docs/strategy_review_loop.md §6.1``）：

- ❌ **不能只看 ``prompt_version_id``**：v1 和 v2 的 ``prompt_version_id`` 都是
  ``<role>:v1_belief_llm``，分不开；混合实验里还会出现 ``agent_version="v0"`` 却
  ``prompt_version_id="...:v1_belief_llm"``。只看单字段会把 v0/mixed/v1/v2 揉错，belief
  报告核心结论直接漂。
- ✅ **优先级**：① ``game_id`` 前缀（``batch_v0_*`` / ``batch_v1_*`` / ``batch_v2_*`` /
  ``mixed_batch_*``）最权威 → ② 回落 ``trace.agent_version`` → ③ 仍不确定标 ``unknown``。

「这局到底有没有 belief 注入」不在这里判——交给 belief_accuracy 用真实 belief lane 是否非空
来定（真值），避免标签误判带偏。
"""

from __future__ import annotations

import re
from typing import Any

ARM_V0 = "v0"
ARM_V1 = "v1"
ARM_V2 = "v2"
ARM_MIXED = "mixed"
ARM_UNKNOWN = "unknown"

# 干净的、可进 v1-vs-v2 对比的 arm（mixed/unknown 单独分桶，不混入）。
CLEAN_ARMS = (ARM_V0, ARM_V1, ARM_V2)

_PREFIX_RE = re.compile(r"^batch_(v[012])_", re.IGNORECASE)
_MIXED_PREFIX_RE = re.compile(r"^mixed_batch", re.IGNORECASE)


def arm_from_game_id(game_id: str) -> str | None:
    """仅凭 game_id 前缀判 arm；判不出返回 None（让调用方回落 agent_version）。"""
    if _MIXED_PREFIX_RE.match(game_id):
        return ARM_MIXED
    m = _PREFIX_RE.match(game_id)
    if m:
        return m.group(1).lower()
    return None


def _arm_from_agent_version(agent_version: str | None) -> str | None:
    if not agent_version:
        return None
    v = agent_version.strip().lower()
    # 既兼容短名 v0/v1/v2，也兼容长名 v0_free_llm / v1_belief_guided / v2_*。
    for arm in CLEAN_ARMS:
        if v == arm or v.startswith(arm + "_"):
            return arm
    return None


def resolve_arm(game_id: str, traces: list[Any] | None = None) -> str:
    """综合判定一局的 arm。

    1. game_id 前缀权威（含 mixed）。
    2. 回落该局 trace 的 ``agent_version``：全部一致才采用，否则 mixed。
    3. 都判不出 → unknown。
    """
    by_id = arm_from_game_id(game_id)
    if by_id is not None:
        return by_id

    versions = {
        _arm_from_agent_version(getattr(t, "agent_version", None)) for t in (traces or [])
    }
    versions.discard(None)
    if len(versions) == 1:
        return next(iter(versions))
    if len(versions) > 1:
        return ARM_MIXED
    return ARM_UNKNOWN


def is_clean_arm(arm: str) -> bool:
    """是否可用于 v1-vs-v2 干净对比（排除 mixed/unknown）。"""
    return arm in CLEAN_ARMS
