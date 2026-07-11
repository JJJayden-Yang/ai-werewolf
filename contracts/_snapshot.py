"""契约快照 —— 把所有冻结模型的 JSON Schema + 枚举值序列化成一份快照。

`test_contracts_frozen.py` 用它和提交进仓库的快照比对：schema 一变
（增/删字段、改名、改类型、改枚举值），测试即红，CI 即拦。

仅在走完 contracts/README.md 第 4 节变更流程后，才更新快照：

    python -m contracts._snapshot --write
"""

from __future__ import annotations

import argparse
import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from contracts import enums, schemas

SNAPSHOT_PATH = Path(__file__).parent / "__snapshots__" / "frozen_contracts.json"


def build_snapshot() -> dict:
    """收集当前 contracts 的全部 schema 与枚举，返回可比对的确定性结构。"""
    model_schemas = {
        name: obj.model_json_schema()
        for name, obj in vars(schemas).items()
        if isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and obj.__module__ == schemas.__name__
    }
    enum_values = {
        name: [member.value for member in obj]
        for name, obj in vars(enums).items()
        if isinstance(obj, type) and issubclass(obj, Enum) and obj.__module__ == enums.__name__
    }
    return {
        "schemas": dict(sorted(model_schemas.items())),
        "enums": dict(sorted(enum_values.items())),
    }


def dumps(snapshot: dict) -> str:
    return json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_snapshot() -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(dumps(build_snapshot()), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="把当前 schema 写入快照文件")
    if parser.parse_args().write:
        write_snapshot()
        print(f"snapshot written: {SNAPSHOT_PATH}")
    else:
        print(dumps(build_snapshot()), end="")


if __name__ == "__main__":
    main()
