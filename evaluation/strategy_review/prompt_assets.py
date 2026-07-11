"""收集喂给复盘分析师的 prompt 资产（区分「可改目标」与「只读背景」）。

§2.1：可改目标只有 **role prompt + 该角色 advanced snippets**；output_contract /
game_knowledge / soul / belief_guidance 是**只读背景**（帮 AI 懂约束，禁止建议改）。
全部经 ``PromptTemplateLoader`` / ``StrategyLibrary`` 取，保留真实 ``source_path``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_policy.advanced_strategy import strategy_library as _sl_mod
from agent_policy.advanced_strategy.strategy_library import StrategyLibrary
from agent_runtime.prompt_template_loader import PromptTemplateLoader

GENERIC_SCOPE = "generic"

# advanced snippets 根目录（新增 snippet 只允许落在这里的相应子目录下）。
SNIPPETS_BASE = Path(_sl_mod.__file__).resolve().parent / "snippets"


@dataclass
class PromptAsset:
    label: str
    target_file: str
    text: str


@dataclass
class RolePromptBundle:
    role: str
    editable: list[PromptAsset] = field(default_factory=list)  # 可建议修改（现有文件）
    read_only: list[PromptAsset] = field(default_factory=list)  # 只读背景
    # 允许**新增** advanced snippet 的目录（绝对路径）。role review→该角色子目录；global→generic。
    new_snippet_dirs: list[str] = field(default_factory=list)


def _read(path: str | Path) -> str:
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _shared_assets(loader: PromptTemplateLoader, *, include_belief: bool) -> list[PromptAsset]:
    base = loader.prompts_dir / "shared"
    assets = [
        PromptAsset("output_contract(只读)", str(base / "output_contract.md"),
                    _read(base / "output_contract.md")),
        PromptAsset("game_knowledge(只读)", str(base / "game_knowledge.md"),
                    _read(base / "game_knowledge.md")),
    ]
    if include_belief:
        bg = base / "v1_belief_guidance.md"
        assets.append(PromptAsset("belief_guidance(只读)", str(bg), _read(bg)))
    return [a for a in assets if a.text]


def build_role_bundle(
    role: str,
    *,
    loader: PromptTemplateLoader | None = None,
    library: StrategyLibrary | None = None,
    include_belief_background: bool = True,
) -> RolePromptBundle:
    """某角色的可改目标 + 只读背景。"""
    loader = loader or PromptTemplateLoader()
    library = library or StrategyLibrary()
    bundle = RolePromptBundle(role=role)

    # 可改：role prompt（真实 source_path）
    tmpl = loader.load_for_role(role)
    src = tmpl.metadata.get("source_path")
    if src:
        bundle.editable.append(PromptAsset(f"{role} role prompt", src, _read(src)))

    # 可改：该角色专属 advanced snippets（generic 留作只读背景，避免角色 review 改到全局片段）
    for s in library.snippets:
        if s.role_scope == role:
            bundle.editable.append(PromptAsset(f"advanced:{s.id}", s.source_path, s.text))

    # 只读：shared 三件 + generic snippets
    bundle.read_only.extend(_shared_assets(loader, include_belief=include_belief_background))
    for s in library.snippets:
        if s.role_scope == GENERIC_SCOPE:
            bundle.read_only.append(PromptAsset(f"generic snippet:{s.id}(只读)", s.source_path, s.text))

    # 可新增：该角色专属 snippet 子目录
    bundle.new_snippet_dirs = [str(SNIPPETS_BASE / role)]
    return bundle


def build_global_bundle(
    *,
    loader: PromptTemplateLoader | None = None,
    library: StrategyLibrary | None = None,
) -> RolePromptBundle:
    """全局 review：generic snippets 可改；shared 只读；各角色 prompt 作只读要点背景。"""
    loader = loader or PromptTemplateLoader()
    library = library or StrategyLibrary()
    bundle = RolePromptBundle(role="global")

    for s in library.snippets:
        if s.role_scope == GENERIC_SCOPE:
            bundle.editable.append(PromptAsset(f"advanced:{s.id}", s.source_path, s.text))

    bundle.read_only.extend(_shared_assets(loader, include_belief=True))
    for role in ("werewolf", "seer", "witch", "hunter", "villager"):
        tmpl = loader.load_for_role(role)
        src = tmpl.metadata.get("source_path")
        if src:
            # 只取前若干行作要点背景，控 token。
            head = "\n".join(_read(src).splitlines()[:20])
            bundle.read_only.append(PromptAsset(f"{role} prompt(只读要点)", src, head))

    # 可新增：generic snippet 目录
    bundle.new_snippet_dirs = [str(SNIPPETS_BASE / GENERIC_SCOPE)]
    return bundle


def editable_target_files(bundle: RolePromptBundle) -> set[str]:
    """该 bundle 允许 draft 落地的**现有**文件集合（reviewer 据此硬校验 target_file）。"""
    return {a.target_file for a in bundle.editable}


def is_allowed_new_snippet(target_file: str, bundle: RolePromptBundle) -> bool:
    """target_file 是否是该 bundle 允许新增的 advanced snippet（在 snippet 子目录下、.md、无穿越）。"""
    if not target_file.endswith(".md") or ".." in target_file:
        return False
    return any(
        target_file.startswith(d.rstrip("/") + "/") for d in bundle.new_snippet_dirs
    )
