"""runner.launcher 正式开局装配测试。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from runner.launcher import LaunchSpec, assemble_game


def test_assemble_game_uses_jsonl_stores_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WOLF_STORAGE_BACKEND", "jsonl")
    monkeypatch.setenv("AI_WOLF_DATA_DIR", str(tmp_path))

    spec = LaunchSpec(
        player_count=6,
        arm="v0",
        mode="mock",
        seed=21,
        temperature=0.6,
        game_id="launcher_jsonl",
    )

    built, game_id = assemble_game(spec)
    asyncio.run(built.supervisor.run_game(game_id))

    event_log = tmp_path / "events" / "launcher_jsonl.jsonl"
    assert event_log.exists()
    assert event_log.read_text(encoding="utf-8").strip()


def test_start_game_cli_supports_batch_jsonl(tmp_path):
    env = {
        **os.environ,
        "AI_WOLF_STORAGE_BACKEND": "jsonl",
        "AI_WOLF_DATA_DIR": str(tmp_path),
    }

    result = subprocess.run(
        [
            sys.executable,
            "scripts/start_game.py",
            "--mode",
            "mock",
            "--player-count",
            "6",
            "--arm",
            "v0",
            "--seed",
            "30",
            "--games",
            "2",
            "--game-id",
            "batch_cli",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "events" / "batch_cli_000.jsonl").exists()
    assert (tmp_path / "events" / "batch_cli_001.jsonl").exists()
