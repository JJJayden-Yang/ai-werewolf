"""契约冻结门禁 —— 硬保证层，与工具无关。

任何对 contracts/ 的 schema/枚举改动若未同步更新快照，本测试即失败，CI 即拦截，
合并前必被发现。详见 contracts/README.md 第 4 节变更流程。
"""

import json

import pytest
from pydantic import ValidationError

from contracts import GameConfig
from contracts._snapshot import SNAPSHOT_PATH, build_snapshot, dumps


def test_contracts_match_frozen_snapshot():
    assert SNAPSHOT_PATH.exists(), (
        f"快照不存在: {SNAPSHOT_PATH}\n"
        "首次生成请运行: python -m contracts._snapshot --write"
    )
    committed = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert dumps(build_snapshot()) == dumps(committed), (
        "\n❌ 契约(schema / 枚举)已变更，与冻结快照不一致。\n"
        "若为有意变更：先走 contracts/README.md 第 4 节流程(三人确认)，\n"
        "再运行 `python -m contracts._snapshot --write` 更新快照，并在同一个 MR 提交。"
    )


def test_extra_fields_forbidden():
    """extra='forbid' 必须生效：禁止未声明字段偷偷混入。"""
    with pytest.raises(ValidationError):
        GameConfig.model_validate(
            {"game_id": "g", "player_count": 9, "roles": {}, "undeclared_field": 1}
        )
