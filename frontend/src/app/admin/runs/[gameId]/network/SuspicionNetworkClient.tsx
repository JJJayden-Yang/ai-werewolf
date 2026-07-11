"use client";

import { useMemo, useState } from "react";

// A 的 god-view 怀疑网导出（/api/audit/runs/{id}/suspicion-network）
type Node = { id: string; alive: boolean; role: string; camp: string };
type Edge = { from: string; to: string; weight: number };
type Panel = { agent: string; top_suspect: string; top_p_wolf: number; reason: string | null };
type Frame = { round: number; phase: string; nodes: Node[]; edges: Edge[]; panels: Panel[] };
type KeyScene = { round: number; phase: string; kind: string; desc: string };
export type NetworkExport = {
  game_id: string;
  winner: string | null;
  rounds: number;
  player_count: number;
  suspicion_network_frames: Frame[];
  key_scenes: KeyScene[];
};

const CAMP_COLOR: Record<string, string> = { werewolf: "#a33d3d", villager: "#255f99" };
const SCENE_ICON: Record<string, string> = {
  night_kill: "🌙",
  exile_wolf: "⚖️✅",
  exile_good: "⚖️❌",
  hunter_shot: "🔫",
};

function nodePositions(ids: string[], cx: number, cy: number, r: number) {
  const pos: Record<string, { x: number; y: number }> = {};
  ids.forEach((id, i) => {
    const a = (2 * Math.PI * i) / ids.length - Math.PI / 2;
    pos[id] = { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
  });
  return pos;
}

const SIZE = 440;
const NODE_R = 21;

export function SuspicionNetworkClient({ data }: { data: NetworkExport }) {
  const frames = data.suspicion_network_frames;
  const [i, setI] = useState(0);

  // hooks 必须无条件执行，故空帧时也算（safeFrame=null）
  const safeFrame = frames.length ? frames[Math.min(i, frames.length - 1)] : null;
  const ids = useMemo(() => (safeFrame ? safeFrame.nodes.map((n) => n.id) : []), [safeFrame]);
  const pos = useMemo(() => nodePositions(ids, SIZE / 2, SIZE / 2, 168), [ids]);

  if (!safeFrame) {
    return (
      <p style={{ color: "var(--audit-muted)" }}>
        本局无 belief 数据（v0 纯 LLM 无可审计 belief），怀疑网不可用。
      </p>
    );
  }

  const frame = safeFrame;
  const nodeR = NODE_R;

  const frameKey = (r: number, p: string) =>
    frames.findIndex((f) => f.round === r && f.phase === p);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
        <span
          style={{
            background: "rgba(123, 31, 162, 0.1)",
            border: "1px solid rgba(123, 31, 162, 0.3)",
            color: "#7b1fa2",
            padding: "3px 9px",
            borderRadius: 6,
            fontSize: 12,
            fontWeight: 650,
          }}
        >
          👁 上帝视角（赛后复盘）
        </span>
        <span style={{ color: "var(--audit-muted)", fontSize: 13 }}>
          {data.player_count} 人 · {data.rounds} 轮 · 胜方 {data.winner ?? "未知"}
        </span>
      </div>

      {/* 戏剧节点条 */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {data.key_scenes.map((s, k) => {
          const fi = frameKey(s.round, s.phase);
          return (
            <button
              key={k}
              className="audit-button compact"
              onClick={() => fi >= 0 && setI(fi)}
              title={`跳到 R${s.round} ${s.phase}`}
              disabled={fi < 0}
              type="button"
            >
              {SCENE_ICON[s.kind] ?? "•"} R{s.round} {s.desc}
            </button>
          );
        })}
      </div>

      {/* 帧滚动 */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button
          className="audit-button compact"
          onClick={() => setI((v) => Math.max(0, v - 1))}
          disabled={i === 0}
          type="button"
        >
          ◀
        </button>
        <input
          type="range"
          min={0}
          max={frames.length - 1}
          value={i}
          onChange={(e) => setI(Number(e.target.value))}
          style={{ flex: 1, accentColor: "var(--audit-blue)" }}
        />
        <button
          className="audit-button compact"
          onClick={() => setI((v) => Math.min(frames.length - 1, v + 1))}
          disabled={i >= frames.length - 1}
          type="button"
        >
          ▶
        </button>
        <span style={{ minWidth: 220, fontSize: 13, color: "var(--audit-muted)" }}>
          R{frame.round} · {frame.phase} ({i + 1}/{frames.length})
        </span>
      </div>

      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
        {/* 怀疑网 SVG */}
        <svg
          width={SIZE}
          height={SIZE}
          style={{
            background: "var(--audit-panel-soft)",
            border: "1px solid var(--audit-line)",
            borderRadius: 10,
            flex: "0 0 auto",
          }}
        >
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto" markerUnits="userSpaceOnUse">
              <path d="M0,0 L7,3 L0,6 Z" fill="#64748b" />
            </marker>
          </defs>
          {frame.edges.map((e, k) => {
            const a = pos[e.from], b = pos[e.to];
            if (!a || !b) return null;
            const dx = b.x - a.x, dy = b.y - a.y;
            const len = Math.hypot(dx, dy) || 1;
            const ux = dx / len, uy = dy / len;
            const x1 = a.x + ux * nodeR, y1 = a.y + uy * nodeR;
            const x2 = b.x - ux * (nodeR + 6), y2 = b.y - uy * (nodeR + 6);
            return (
              <line
                key={k} x1={x1} y1={y1} x2={x2} y2={y2}
                stroke="#64748b" strokeWidth={1.2 + e.weight * 5}
                strokeOpacity={0.22 + e.weight * 0.55} markerEnd="url(#arrow)"
              />
            );
          })}
          {frame.nodes.map((n) => {
            const p = pos[n.id];
            const fill = CAMP_COLOR[n.camp] ?? "#737373";
            return (
              <g key={n.id} opacity={n.alive ? 1 : 0.32}>
                <circle
                  cx={p.x} cy={p.y} r={nodeR} fill={fill}
                  stroke={n.alive ? "#fff" : "#bbb"}
                  strokeWidth={1.5} strokeDasharray={n.alive ? undefined : "3 3"}
                />
                <text x={p.x} y={p.y - 1} textAnchor="middle" fontSize="12" fontWeight={700} fill="#fff">{n.id}</text>
                <text x={p.x} y={p.y + 11} textAnchor="middle" fontSize="8" fill="#ffffffd8">{n.role}</text>
              </g>
            );
          })}
        </svg>

        {/* 心证面板 */}
        <div style={{ flex: 1, minWidth: 280 }}>
          <h3 style={{ margin: "0 0 8px", fontSize: 14, color: "var(--audit-ink)" }}>
            心证面板（本帧每人最怀疑谁）
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {frame.panels.length === 0 && (
              <span style={{ color: "var(--audit-muted)" }}>本帧无存活玩家的怀疑数据。</span>
            )}
            {frame.panels.map((p) => (
              <div
                key={p.agent}
                style={{
                  border: "1px solid var(--audit-line)",
                  borderRadius: 8,
                  padding: "8px 11px",
                  background: "var(--audit-panel)",
                }}
              >
                <div style={{ fontSize: 13, color: "var(--audit-ink)" }}>
                  <b style={{ color: CAMP_COLOR[frame.nodes.find((n) => n.id === p.agent)?.camp ?? ""] ?? "var(--audit-ink)" }}>
                    {p.agent}
                  </b>
                  {" 最怀疑 "}
                  <b>{p.top_suspect}</b>
                  <span style={{ color: "var(--audit-muted)" }}> (P={p.top_p_wolf.toFixed(2)})</span>
                </div>
                {p.reason && (
                  <div style={{ fontSize: 12, color: "var(--audit-muted)", marginTop: 3, lineHeight: 1.5 }}>
                    {p.reason}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      <p style={{ fontSize: 12, color: "var(--audit-muted)", margin: 0 }}>
        边 = "谁最怀疑谁"（每人最多 2 条，粗细/深浅 ∝ 怀疑度）；红=狼阵营、蓝=好人阵营（上帝视角真身份）；
        虚线半透明 = 本帧已死。狼对队友的"已知"不画进怀疑网，预言家查杀保留。
      </p>
    </div>
  );
}
