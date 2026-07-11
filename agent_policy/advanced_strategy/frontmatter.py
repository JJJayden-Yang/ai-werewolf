"""Markdown + YAML frontmatter 解析（静态高级策略库用）。

策略 snippet 文件形如：

    ---
    id: hunter_shoot
    role_scope: hunter
    phase_scope: [HUNTER_SHOOT]
    scene_tags: [hunter_shoot]
    priority: 80
    ---
    # 参考打法：猎人开枪
    ...正文...

``parse_markdown_frontmatter`` 把它拆成 ``(metadata_dict, body_text)``。
仓库里只有 ``realtime_belief_updater._load_rules`` 用过 yaml；这里沿用 ``yaml.safe_load``，
不引新依赖。
"""

from __future__ import annotations

from pathlib import Path

import yaml

_FENCE = "---"


def parse_markdown_frontmatter(path: str | Path) -> tuple[dict, str]:
    """读 markdown 文件，返回 ``(frontmatter dict, 正文 str)``。

    - 文件以 ``---`` 开头时，第一对 ``---`` 之间按 YAML 解析为 metadata，其后为正文。
    - 没有 frontmatter 时返回 ``({}, 全文)``。
    - frontmatter 不是 mapping → 抛 ``ValueError``（防写错文件结构静默吞掉）。
    """
    text = Path(path).read_text(encoding="utf-8")
    if not text.lstrip().startswith(_FENCE):
        return {}, text.strip()

    # 去掉开头可能的空白后，按前两个 fence 切三段：['', meta, body]
    stripped = text.lstrip()
    parts = stripped.split(_FENCE, 2)
    if len(parts) < 3:
        # 只有一个 fence，结构不完整 → 当作无 frontmatter
        return {}, text.strip()

    _, meta_block, body = parts
    meta = yaml.safe_load(meta_block) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"strategy snippet frontmatter must be a mapping: {path}")
    return meta, body.strip()
