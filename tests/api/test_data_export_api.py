"""按版本导出数据端点（/api/data/arms、/api/data/download）的测试。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app


def _seed(root: Path) -> None:
    # 两局 v1、一局 v0，构造 events/traces/belief_states/replay_truth 各一份。
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "traces").mkdir(parents=True, exist_ok=True)
    (root / "replay_truth").mkdir(parents=True, exist_ok=True)
    for gid in ("batch_v1_001", "batch_v1_002", "batch_v0_001"):
        (root / "events" / f"{gid}.jsonl").write_text('{"e":1}\n', encoding="utf-8")
        (root / "traces" / f"{gid}.jsonl").write_text('{"t":1}\n', encoding="utf-8")
        (root / "replay_truth" / f"{gid}.json").write_text('{"players":[]}', encoding="utf-8")
    # belief_states 仅 v1_001 有
    bdir = root / "belief_states" / "batch_v1_001" / "P1"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "real.jsonl").write_text('{"b":1}\n', encoding="utf-8")


def test_arms_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WOLF_DATA_DIR", str(tmp_path))
    _seed(tmp_path)
    with TestClient(app) as client:
        data = client.get("/api/data/arms").json()
        counts = {a["arm"]: a["games"] for a in data["arms"]}
        assert counts == {"v1": 2, "v0": 1}
        assert "belief_states" in data["data_types"]


def test_download_zip_structure(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WOLF_DATA_DIR", str(tmp_path))
    _seed(tmp_path)
    with TestClient(app) as client:
        r = client.get(
            "/api/data/download?arm=v1&types=events,traces,belief_states,replay_truth"
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(z.namelist())
        # 两局 v1 的 events/traces/replay_truth + v1_001 的 belief
        assert "events/batch_v1_001.jsonl" in names
        assert "traces/batch_v1_002.jsonl" in names
        assert "replay_truth/batch_v1_001.json" in names
        assert "belief_states/batch_v1_001/P1/real.jsonl" in names
        # v0 的不该混进来
        assert not any("v0" in n for n in names)


def test_download_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WOLF_DATA_DIR", str(tmp_path))
    _seed(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/data/download?arm=v1&types=foo").status_code == 400
        assert client.get("/api/data/download?arm=v9&types=events").status_code == 404


def test_download_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WOLF_DATA_DIR", str(tmp_path))
    _seed(tmp_path)
    with TestClient(app) as client:
        r = client.get("/api/data/download?arm=v1&types=events&limit=1")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        assert len(z.namelist()) == 1
