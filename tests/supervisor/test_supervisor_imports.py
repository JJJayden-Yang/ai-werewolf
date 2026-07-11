"""A1.5：supervisor/ 可导入，且只依赖抽象（contracts + game_core），不硬依赖 B/C 具体实现。"""

import ast
from pathlib import Path

import supervisor

# supervisor 调度 Engine，故允许依赖 game_core / contracts；
# 但跨边界（context / agent_runtime / stores 等）必须靠注入 + Protocol，不能硬 import。
FORBIDDEN_MODULES = {
    "agent_policy",
    "agent_runtime",
    "context",
    "stores",
    "evaluation",
    "api",
    "frontend",
    "llm",
    "openai",
    "volcengine",
}


def test_supervisor_importable():
    assert hasattr(supervisor, "Supervisor")


def test_supervisor_depends_on_abstractions_only():
    pkg_dir = Path(supervisor.__file__).parent
    offenders = []
    for py in pkg_dir.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                roots = [node.module.split(".")[0]] if node.module else []
            else:
                continue
            offenders += [f"{py.name} -> {r}" for r in roots if r in FORBIDDEN_MODULES]
    assert not offenders, f"supervisor 出现禁止的硬依赖: {offenders}"
