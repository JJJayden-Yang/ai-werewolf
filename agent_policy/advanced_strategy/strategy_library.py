"""StrategyLibrary —— 扫 ``snippets/`` 目录，把每个 markdown 片段按 frontmatter 建索引。

每个 snippet 文件的 frontmatter 字段：

- ``id``: 唯一标识（进 trace 用，便于 Phase 4 复盘归因）。
- ``role_scope``: 角色名（``hunter`` / ``witch`` / ...）或 ``generic``（所有角色）。
- ``phase_scope``: Phase 值列表（如 ``[HUNTER_SHOOT]``）；空/缺省表示不限 phase。
- ``scene_tags``: scene tag 列表（与 SceneDetector 输出对齐）。
- ``priority``: int，选择时降序排序用。

库本身只负责"加载 + 暴露全部 snippet"；命中筛选在 StrategySelector。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_policy.advanced_strategy.frontmatter import parse_markdown_frontmatter

_DEFAULT_SNIPPETS_DIR = Path(__file__).resolve().parent / "snippets"
_GENERIC_SCOPE = "generic"


@dataclass(frozen=True)
class StrategySnippet:
    """一段静态策略片段（人工维护）。"""

    id: str
    role_scope: str  # 角色名 或 "generic"
    phase_scope: tuple[str, ...]  # Phase 值；空 = 不限 phase
    scene_tags: frozenset[str]
    priority: int
    text: str
    source_path: str = ""

    def matches_role(self, role_value: str) -> bool:
        return self.role_scope == _GENERIC_SCOPE or self.role_scope == role_value

    def matches_phase(self, phase_value: str) -> bool:
        return not self.phase_scope or phase_value in self.phase_scope


class StrategyLibrary:
    """加载并持有全部 StrategySnippet。构造时扫一次目录，之后只读。"""

    def __init__(self, snippets_dir: Path | str | None = None) -> None:
        self._dir = Path(snippets_dir) if snippets_dir is not None else _DEFAULT_SNIPPETS_DIR
        self._snippets: list[StrategySnippet] = []
        if self._dir.is_dir():
            self._load()

    def _load(self) -> None:
        seen_ids: set[str] = set()
        for md_path in sorted(self._dir.rglob("*.md")):
            meta, body = parse_markdown_frontmatter(md_path)
            if not meta or "id" not in meta:
                # 没 frontmatter / 没 id 的 md（如 README）跳过，不当 snippet。
                continue
            snippet = _build_snippet(meta, body, md_path)
            if snippet.id in seen_ids:
                raise ValueError(f"duplicate strategy snippet id: {snippet.id!r} ({md_path})")
            seen_ids.add(snippet.id)
            self._snippets.append(snippet)

    @property
    def snippets(self) -> list[StrategySnippet]:
        return list(self._snippets)

    def __len__(self) -> int:
        return len(self._snippets)


def _build_snippet(meta: dict, body: str, path: Path) -> StrategySnippet:
    phase_scope = meta.get("phase_scope") or []
    scene_tags = meta.get("scene_tags") or []
    return StrategySnippet(
        id=str(meta["id"]),
        role_scope=str(meta.get("role_scope", _GENERIC_SCOPE)),
        phase_scope=tuple(str(p) for p in phase_scope),
        scene_tags=frozenset(str(t) for t in scene_tags),
        priority=int(meta.get("priority", 0)),
        text=body,
        source_path=str(path),
    )
