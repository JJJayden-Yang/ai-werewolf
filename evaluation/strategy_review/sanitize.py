"""真相态脱敏 —— 双防线的公共工具。

§2.3 红线：复盘可读真相，但喂给 LLM 的 digest 与 LLM 产出的 draft **都不得**出现
「Px 是狼 / Px 的真实身份是预言家」式身份归属，否则会泄漏进下一局 runtime。

- 输入端：digest 用相对位置（「中后置位玩家」）与派生标签（命中真凶?/致本方损失），
  本模块提供 ``position_label`` 等工具，让 digest 构造方避免写真实身份。
- 输出端：``contains_truth_leak`` / ``assert_no_leak`` 对 LLM 产出的 draft 文本做硬校验。
"""

from __future__ import annotations

import re

_ROLE_WORDS_EN = "werewolf|wolf|seer|witch|hunter|villager"
_ROLE_WORDS_ZH = "狼人|狼|预言家|女巫|猎人|村民|平民|好人|神民"
# 中文断言后面也可能直接跟英文角色词（如「P5 实际是 werewolf」），合并两套词表。
_ROLE_WORDS_ANY = f"{_ROLE_WORDS_ZH}|{_ROLE_WORDS_EN}"

# 「P3 是狼」「P3的真实身份是预言家」「P3 为狼人」「P3 实际是 werewolf」
_LEAK_PATTERNS = [
    re.compile(rf"P\d+\s*(?:的真实身份)?\s*(?:是|为|实为|实际是|乃)\s*(?:{_ROLE_WORDS_ANY})", re.IGNORECASE),
    re.compile(rf"P\d+\s*(?:真实身份|身份)\s*[:：]\s*(?:{_ROLE_WORDS_ANY})", re.IGNORECASE),
    re.compile(rf"P\d+\s+(?:is|was|=)\s+(?:a\s+|the\s+)?(?:{_ROLE_WORDS_EN})", re.IGNORECASE),
    # 「真凶 P3」「狼是 P3/P3和P5」
    re.compile(rf"(?:真凶|真狼|狼(?:人)?)\s*(?:是|为|：|:)?\s*P\d+"),
]


def contains_truth_leak(text: str) -> bool:
    """文本是否出现「Px 是<角色>」式真相归属。"""
    if not text:
        return False
    return any(p.search(text) for p in _LEAK_PATTERNS)


def find_truth_leaks(text: str) -> list[str]:
    """返回所有命中的泄漏片段（给报错/测试用）。"""
    hits: list[str] = []
    for p in _LEAK_PATTERNS:
        hits.extend(m.group(0) for m in p.finditer(text or ""))
    return hits


class TruthLeakError(ValueError):
    """draft 文本含真相态泄漏，拒收。"""


def assert_no_leak(text: str, *, where: str = "draft") -> None:
    leaks = find_truth_leaks(text)
    if leaks:
        raise TruthLeakError(f"{where} 含真相态泄漏: {leaks[:3]}")


def position_label(player_id: str, ordered_ids: list[str]) -> str:
    """把玩家映射成相对座位标签（前置位/中置位/后置位），不暴露真实身份。"""
    if player_id not in ordered_ids:
        return "某玩家"
    idx = ordered_ids.index(player_id)
    n = len(ordered_ids)
    if n <= 1:
        return "某玩家"
    third = n / 3
    if idx < third:
        return "前置位玩家"
    if idx < 2 * third:
        return "中置位玩家"
    return "后置位玩家"
