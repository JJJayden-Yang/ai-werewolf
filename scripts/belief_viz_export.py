#!/usr/bin/env python
"""① 可视化数据层：把单局对局导出成前端可直接吃的 JSON（心证面板 / 怀疑网 / 上帝视角）。

**只做数据层**——不碰 `frontend/` / `api/`（那是 C 的 owner）。产物字段对齐冻结契约里的
`ReplayPlayer` / `BeliefCurvePoint`，外加两个派生便利结构（C 渲染不必重算）：
  - `belief_curves`            : list[BeliefCurvePoint]，全量时间序列（驱动 belief 曲线）。
  - `suspicion_network_frames`: 每 (round, phase) 一帧 = nodes(存活/角色) + edges(谁最怀疑谁,
                                 每人 top-2 有向边, weight=werewolf 概率) + panels(心证面板:
                                 每人头号嫌疑 + 一句话理由 reason_summary)。
  - `players`                  : list[ReplayPlayer]（上帝视角真身份 + 死亡归因）。
  - `key_scenes`               : 戏剧节点（放逐真狼 / 首夜刀口 / 猎人开枪）。

用法（可从 repo root 直接运行）：
    python scripts/belief_viz_export.py \
        --data-dir data/belief_runs/v1_sample \
        --game-id batch_v1_02000 \
        --out data/belief_exports/batch_v1_02000.json

⚠ 这是**上帝视角**导出（含真身份）：仅供赛后复盘 UI，玩家实时视角不可用此文件。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts.enums import Camp, PlayerStatus, Role  # noqa: E402
from contracts.schemas import BeliefCurvePoint, ReplayPlayer  # noqa: E402

EDGES_PER_SOURCE = 2  # 怀疑网每人只画最强的 top-2 有向边，避免一团乱麻
# 这些 locked 是「已知」非「怀疑」，不进怀疑网：自己 + 狼队友（上帝视角噪声）。
# 注意：seer_private_check_result（预言家查杀）是合法强怀疑，**保留**。
_ALLIANCE_LOCKS = {"own_role_known", "wolf_team_revealed"}

# 帧排序用的阶段先后（belief 按 event_id 定序，但收帧展示按阶段顺序排）
_PHASE_ORDER = [
    "INIT", "ROLE_ASSIGNMENT", "NIGHT_WEREWOLF", "NIGHT_SEER", "NIGHT_WITCH",
    "DAY_ANNOUNCEMENT", "HUNTER_SHOOT", "DAY_DISCUSSION", "DAY_VOTE",
    "DAY_TIE_DISCUSSION", "DAY_TIE_REVOTE", "EXILE_RESOLUTION",
    "NO_EXILE_RESOLUTION", "EXILE_LAST_WORDS", "WIN_CHECK", "GAME_OVER",
]
PHASE_RANK = {p: i for i, p in enumerate(_PHASE_ORDER)}


def read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def seqnum(event_id: str | None) -> int | None:
    if not event_id:
        return None
    try:
        return int(str(event_id).rsplit("_evt_", 1)[1])
    except (IndexError, ValueError):
        return None


def _frame_alive(roles, death_seq, frame_seq) -> set[str]:
    """该帧存活集：death_confirmed 序号 > 帧序号者仍活。"""
    return {p for p in roles if death_seq.get(p, 10**9) > frame_seq}


def export_game(data_dir: str, game_id: str) -> dict:
    ef = os.path.join(data_dir, "events", game_id + ".jsonl")
    tf = os.path.join(data_dir, "traces", game_id + ".jsonl")
    if not (os.path.exists(ef) and os.path.exists(tf)):
        raise FileNotFoundError(f"找不到 {game_id} 的 events/traces：{ef}")
    events = read_jsonl(ef)
    traces = read_jsonl(tf)

    roles = {t["agent_id"]: t["role"] for t in traces}
    wolves = {p for p, r in roles.items() if r == "werewolf"}
    trace_idx = {(t["agent_id"], t["round"], t["phase"]): t for t in traces}

    # ---- 事件扫描：死亡归因 / 凶手 / 胜负 / 轮数 / 关键场景 ----
    death: dict[str, dict] = {}
    killer_by_target: dict[str, str] = {}  # 主动击杀者：女巫毒 / 猎人枪 的 actor
    winner = None
    max_round = 0
    key_scenes: list[dict] = []
    for e in events:
        max_round = max(max_round, e.get("round") or 0)
        et = e["event_type"]
        if et == "death_confirmed" and e.get("target"):
            death[e["target"]] = {
                "round": e.get("round"),
                "phase": e["phase"],
                "cause": e.get("payload", {}).get("death_cause"),
                "event_id": e.get("event_id"),
            }
        elif et == "game_over":
            winner = e["payload"].get("winner")
        if et in ("witch_poison", "hunter_shot") and e.get("target") and e.get("actor"):
            killer_by_target[e["target"]] = e["actor"]
        if et == "night_kill_announced" and e.get("target"):
            key_scenes.append({"round": e.get("round"), "phase": e["phase"],
                               "kind": "night_kill", "desc": f"首夜刀口 {e['target']}"
                               if e.get("round") == 1 else f"夜刀 {e['target']}"})
        elif et == "exile" and e.get("target"):
            tgt = e["target"]
            key_scenes.append({"round": e.get("round"), "phase": e["phase"],
                               "kind": "exile_wolf" if tgt in wolves else "exile_good",
                               "desc": f"放逐{'真狼' if tgt in wolves else '好人'} {tgt}"})
        elif et == "hunter_shot" and not e.get("payload", {}).get("pass"):
            key_scenes.append({"round": e.get("round"), "phase": e["phase"],
                               "kind": "hunter_shot", "desc": f"猎人开枪 {e.get('target')}"})

    death_seq = {p: seqnum(d["event_id"]) or 10**9 for p, d in death.items()}

    def camp_of(pid: str) -> Camp:
        return Camp("werewolf") if roles.get(pid) == "werewolf" else Camp("villager")

    # ---- players（上帝视角，对齐 ReplayPlayer）----
    players = []
    for pid in sorted(roles, key=lambda x: (len(x), x)):
        d = death.get(pid)
        # 凶手归因：夜刀=狼阵营(无单一 actor)；毒/枪=事件 actor；放逐=集体投票(无单一凶手)
        killer_id, killer_camp = None, None
        if d:
            cause = d["cause"]
            if cause == "night_kill":
                killer_camp = Camp("werewolf")
            elif cause in ("witch_poison", "hunter_shot"):
                killer_id = killer_by_target.get(pid)
                if killer_id:
                    killer_camp = camp_of(killer_id)
            # cause == "exile" → 集体放逐，无单一凶手，两字段留空（见 handoff 说明）
        players.append(ReplayPlayer(
            player_id=pid,
            role=Role(roles[pid]),
            camp=camp_of(pid),
            final_status=PlayerStatus("dead") if d else PlayerStatus("alive"),
            survived=d is None,
            death_round=d["round"] if d else None,
            death_phase=d["phase"] if d else None,
            death_cause=d["cause"] if d else None,
            death_source_event_id=d["event_id"] if d else None,
            killer_agent_id=killer_id,
            killer_camp=killer_camp,
        ).model_dump(mode="json"))

    # ---- belief 历史（仅 real，非 shadow）----
    belief: dict[str, list[dict]] = {}
    bdir = os.path.join(data_dir, "belief_states", game_id)
    if os.path.isdir(bdir):
        for pf in glob.glob(os.path.join(bdir, "*", "real.jsonl")):
            belief[os.path.basename(os.path.dirname(pf))] = read_jsonl(pf)

    # ---- belief_curves（对齐 BeliefCurvePoint）+ 按 (round,phase) 收帧 ----
    curves: list[dict] = []
    # frame_key -> agent -> 该帧内最后一条快照
    frames: dict[tuple[int, str], dict[str, dict]] = {}
    for agent, hist in belief.items():
        for snap in hist:
            r = snap.get("round") or 0
            phase = snap.get("phase")
            for tgt, b in snap["beliefs"].items():
                if tgt == agent:
                    continue
                curves.append(BeliefCurvePoint(
                    round=r,
                    phase=phase,
                    agent_id=agent,
                    target_player_id=tgt,
                    werewolf_prob=float(b.get("werewolf", 0.0)),
                    derived_by="rule_realtime",
                ).model_dump(mode="json"))
            frames.setdefault((r, phase), {})[agent] = snap  # 同帧后写覆盖=取最后一条

    # ---- suspicion_network_frames（怀疑网 + 心证面板）----
    network_frames = []
    for (r, phase) in sorted(frames, key=lambda k: (k[0], PHASE_RANK.get(k[1], 99))):
        # 帧时刻 = 该帧内所有快照的最大 last_updated_event_id 序号（同 phase 内已死者会被剔除）
        frame_seq = max(
            (seqnum(s.get("last_updated_event_id")) or 0)
            for s in frames[(r, phase)].values()
        )
        alive = _frame_alive(roles, death_seq, frame_seq)
        nodes = [{"id": p, "alive": p in alive, "role": roles[p],
                  "camp": "werewolf" if p in wolves else "villager"}
                 for p in sorted(roles, key=lambda x: (len(x), x))]
        edges, panels = [], []
        for agent, snap in frames[(r, phase)].items():
            ranked = sorted(
                ((p, b.get("werewolf", 0.0))
                 for p, b in snap["beliefs"].items()
                 if p != agent and p in alive
                 and b.get("lock_reason") not in _ALLIANCE_LOCKS),
                key=lambda x: -x[1],
            )
            if not ranked:
                continue
            for tgt, w in ranked[:EDGES_PER_SOURCE]:
                edges.append({"from": agent, "to": tgt, "weight": round(w, 4)})
            tr = trace_idx.get((agent, r, phase), {})
            panels.append({
                "agent": agent,
                "top_suspect": ranked[0][0],
                "top_p_wolf": round(ranked[0][1], 4),
                "reason": tr.get("decision_output", {}).get("reason_summary"),
            })
        network_frames.append({
            "round": r, "phase": phase,
            "nodes": nodes, "edges": edges, "panels": panels,
        })

    return {
        "schema": "belief_viz_export/v1",
        "god_view": True,
        "game_id": game_id,
        "winner": winner,
        "rounds": max_round,
        "player_count": len(roles),
        "players": players,
        "belief_curves": curves,
        "suspicion_network_frames": network_frames,
        "key_scenes": key_scenes,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, help="某版本批目录（含 events/traces/belief_states）")
    ap.add_argument("--game-id", default=None, help="局 id；省略则导出该批第一局")
    ap.add_argument("--out", default=None, help="输出 JSON 路径；省略则打到 stdout")
    args = ap.parse_args()

    gid = args.game_id
    if not gid:
        first = sorted(glob.glob(os.path.join(args.data_dir, "events", "*.jsonl")))
        if not first:
            raise SystemExit(f"{args.data_dir} 下没有 events")
        gid = os.path.splitext(os.path.basename(first[0]))[0]

    data = export_game(args.data_dir, gid)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        nf = len(data["suspicion_network_frames"])
        print(f"已导出 {gid}: players={len(data['players'])} "
              f"belief_curves={len(data['belief_curves'])} frames={nf} "
              f"key_scenes={len(data['key_scenes'])} → {args.out}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
