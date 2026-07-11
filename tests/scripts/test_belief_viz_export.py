"""可视化导出口径回归：契约形状 + 怀疑网过滤（狼队友排除 / 预言家查杀保留）。"""
from __future__ import annotations

import json

from contracts.schemas import BeliefCurvePoint, ReplayPlayer
from scripts.belief_viz_export import export_game


def _lock(reason):
    return {"werewolf": 1.0, "seer": 0.0, "witch": 0.0, "hunter": 0.0,
            "villager": 0.0, "locked": True, "lock_reason": reason}


def _soft(p):
    rest = (1.0 - p) / 4
    return {"werewolf": p, "seer": rest, "witch": rest, "hunter": rest,
            "villager": rest, "locked": False, "lock_reason": None}


def _build(tmp_path):
    gid = "g_viz"
    roles = {"P1": "villager", "P2": "seer", "P3": "villager",
             "P4": "werewolf", "P5": "werewolf"}
    dd = tmp_path / "batch"
    (dd / "events").mkdir(parents=True)
    (dd / "traces").mkdir(parents=True)

    i = [0]
    def ev(et, *, round, phase, actor=None, target=None, payload=None):
        i[0] += 1
        return {"event_id": f"{gid}_evt_{i[0]:04d}", "event_type": et, "round": round,
                "phase": phase, "actor": actor, "target": target, "payload": payload or {}}

    events = [
        ev("night_kill_announced", round=1, phase="NIGHT_WEREWOLF", target="P3"),
        ev("death_confirmed", round=1, phase="DAY_ANNOUNCEMENT", target="P3",
           payload={"death_cause": "night_kill"}),
        ev("phase_started", round=1, phase="DAY_VOTE"),
        ev("vote_cast", round=1, phase="DAY_VOTE", actor="P2", target="P4"),
        ev("exile", round=1, phase="EXILE_RESOLUTION", target="P4"),
        ev("death_confirmed", round=1, phase="EXILE_RESOLUTION", target="P4",
           payload={"death_cause": "exile"}),
        ev("game_over", round=1, phase="GAME_OVER", payload={"winner": "villagers"}),
    ]
    with open(dd / "events" / f"{gid}.jsonl", "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")

    traces = [{"agent_id": p, "role": r, "round": 1, "phase": "DAY_VOTE",
               "agent_version": "test", "prompt_version_id": f"{r}:test",
               "decision_output": {"action_type": "vote", "target": "P4",
                                   "reason_summary": f"{p} reason"}}
              for p, r in roles.items()]
    with open(dd / "traces" / f"{gid}.jsonl", "w", encoding="utf-8") as fh:
        for t in traces:
            fh.write(json.dumps(t) + "\n")

    # P2(seer)：查杀确认 P4 是狼（seer_private_check_result，应保留）
    # P4(wolf)：知道队友 P5（wolf_team_revealed，应从怀疑网剔除）；对好人 P1 有软怀疑
    bel = {
        "P2": {"P1": _soft(0.1), "P3": _soft(0.1), "P4": _lock("seer_private_check_result"),
               "P5": _soft(0.2), "P2": _lock("own_role_known")},
        "P4": {"P1": _soft(0.4), "P2": _soft(0.2), "P3": _soft(0.1),
               "P5": _lock("wolf_team_revealed"), "P4": _lock("own_role_known")},
    }
    for agent, beliefs in bel.items():
        d = dd / "belief_states" / gid / agent
        d.mkdir(parents=True)
        snap = {"round": 1, "phase": "DAY_VOTE", "is_shadow": False,
                "beliefs": beliefs, "last_updated_event_id": f"{gid}_evt_0003"}
        (d / "real.jsonl").write_text(json.dumps(snap) + "\n", encoding="utf-8")
    return dd, gid


def test_export_shapes_and_suspicion_filter(tmp_path):
    dd, gid = _build(tmp_path)
    data = export_game(str(dd), gid)

    # 契约形状
    players = {p["player_id"]: ReplayPlayer.model_validate(p) for p in data["players"]}
    for c in data["belief_curves"]:
        BeliefCurvePoint.model_validate(c)
    assert players["P3"].survived is False and players["P3"].death_cause == "night_kill"
    assert players["P4"].camp.value == "werewolf"
    assert players["P1"].survived is True
    # 凶手归因：夜刀=狼阵营无单一 actor；放逐=集体无凶手
    assert players["P3"].killer_camp.value == "werewolf"
    assert players["P3"].killer_agent_id is None
    assert players["P4"].death_cause == "exile"
    assert players["P4"].killer_agent_id is None and players["P4"].killer_camp is None

    frame = next(f for f in data["suspicion_network_frames"] if f["phase"] == "DAY_VOTE")
    panel = {p["agent"]: p for p in frame["panels"]}

    # 预言家查杀保留：P2 头号嫌疑应是被查杀的真狼 P4
    assert panel["P2"]["top_suspect"] == "P4"
    assert panel["P2"]["top_p_wolf"] == 1.0

    # 狼队友剔除：P4 不应把队友 P5 当头号嫌疑，且无 P4->P5 边
    assert panel["P4"]["top_suspect"] != "P5"
    assert not any(e["from"] == "P4" and e["to"] == "P5" for e in frame["edges"])

    # 关键场景含放逐真狼
    assert any(k["kind"] == "exile_wolf" and "P4" in k["desc"] for k in data["key_scenes"])


def test_active_killer_attribution(tmp_path):
    """女巫毒 / 猎人枪：killer_agent_id=事件 actor，killer_camp=actor 阵营。"""
    gid = "g_kill"
    roles = {"P1": "witch", "P2": "werewolf", "P3": "villager"}
    dd = tmp_path / "b"
    (dd / "events").mkdir(parents=True)
    (dd / "traces").mkdir(parents=True)
    i = [0]

    def ev(et, **kw):
        i[0] += 1
        kw.setdefault("payload", {})
        return {"event_id": f"{gid}_evt_{i[0]:04d}", "event_type": et, **kw}

    events = [
        ev("witch_poison", round=1, phase="NIGHT_WITCH", actor="P1", target="P2"),
        ev("death_confirmed", round=1, phase="DAY_ANNOUNCEMENT", target="P2",
           payload={"death_cause": "witch_poison"}),
        ev("game_over", round=1, phase="GAME_OVER", payload={"winner": "villagers"}),
    ]
    (dd / "events" / f"{gid}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8")
    (dd / "traces" / f"{gid}.jsonl").write_text(
        "\n".join(json.dumps({"agent_id": p, "role": r, "round": 1, "phase": "NIGHT_WITCH",
                              "agent_version": "t", "prompt_version_id": "x",
                              "decision_output": {}}) for p, r in roles.items()),
        encoding="utf-8")

    data = export_game(str(dd), gid)
    dead = next(p for p in data["players"] if p["player_id"] == "P2")
    assert dead["death_cause"] == "witch_poison"
    assert dead["killer_agent_id"] == "P1"
    assert dead["killer_camp"] == "villager"  # 女巫属好人阵营


def test_frame_alive_uses_snapshot_time_not_phase_start(tmp_path):
    """同一 (round,phase) 内：death_confirmed 早于 belief 快照时，该玩家应判死。"""
    gid = "g_frame"
    roles = {"P1": "villager", "P2": "werewolf", "P3": "werewolf"}
    dd = tmp_path / "b"
    (dd / "events").mkdir(parents=True)
    (dd / "traces").mkdir(parents=True)
    # P3 在 DAY_VOTE 内 evt_0002 确认死亡；P1 的 belief 快照 evt_0005 在其后
    events = [
        {"event_id": f"{gid}_evt_0001", "event_type": "phase_started", "round": 1,
         "phase": "DAY_VOTE", "payload": {}},
        {"event_id": f"{gid}_evt_0002", "event_type": "death_confirmed", "round": 1,
         "phase": "DAY_VOTE", "target": "P3", "payload": {"death_cause": "night_kill"}},
        {"event_id": f"{gid}_evt_0006", "event_type": "game_over", "round": 1,
         "phase": "GAME_OVER", "payload": {"winner": "werewolves"}},
    ]
    (dd / "events" / f"{gid}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8")
    (dd / "traces" / f"{gid}.jsonl").write_text(
        "\n".join(json.dumps({"agent_id": p, "role": r, "round": 1, "phase": "DAY_VOTE",
                              "agent_version": "t", "prompt_version_id": "x",
                              "decision_output": {}}) for p, r in roles.items()),
        encoding="utf-8")
    d = dd / "belief_states" / gid / "P1"
    d.mkdir(parents=True)
    snap = {"round": 1, "phase": "DAY_VOTE", "is_shadow": False,
            "beliefs": {"P2": _soft(0.4), "P3": _soft(0.5),
                        "P1": _lock("own_role_known")},
            "last_updated_event_id": f"{gid}_evt_0005"}
    (d / "real.jsonl").write_text(json.dumps(snap) + "\n", encoding="utf-8")

    data = export_game(str(dd), gid)
    frame = next(f for f in data["suspicion_network_frames"] if f["phase"] == "DAY_VOTE")
    nodes = {n["id"]: n for n in frame["nodes"]}
    assert nodes["P3"]["alive"] is False, "P3 在该帧前已死，应判死"
    # 已死的 P3 不应成为怀疑边目标
    assert not any(e["to"] == "P3" for e in frame["edges"])
