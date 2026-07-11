"""A0：game_core 骨架可导入，且不依赖 Agent / API / Frontend。"""

import ast
from pathlib import Path

import game_core

SKELETON_CLASSES = [
    "GameEngine",
    "GameSessionManager",
    "PhaseController",
    "RuleValidator",
    "ActionResolver",
    "HunterShootResolver",
    "WinChecker",
    "TruthStateStore",
    "EventEmitter",
]

FORBIDDEN_MODULES = {
    # 上层 / 同层模块：Engine 不依赖它们
    "agent_runtime",
    "agent_policy",
    "api",
    "frontend",
    "context",
    "stores",
    "evaluation",
    # LLM 客户端：Engine 绝不调用 LLM
    "llm",
    "openai",
    "volcengine",
}


def test_all_skeleton_classes_importable():
    for name in SKELETON_CLASSES:
        assert hasattr(game_core, name), f"game_core 缺少 {name}"


def test_game_engine_composes_subsystems():
    engine = game_core.GameEngine()
    for attr in ("sessions", "phases", "rules", "resolver", "hunter", "win", "events", "truth"):
        assert hasattr(engine, attr), f"GameEngine 缺少子系统 {attr}"


def test_game_core_has_no_forbidden_imports():
    """红线：game_core 不得 import agent_runtime / agent_policy / api / frontend。"""
    pkg_dir = Path(game_core.__file__).parent
    offenders = []
    for py in pkg_dir.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                roots = [node.module.split(".")[0]] if node.module else []
            else:
                continue
            for root in roots:
                if root in FORBIDDEN_MODULES:
                    offenders.append(f"{py.name} -> {root}")
    assert not offenders, f"game_core 出现禁止的依赖: {offenders}"
