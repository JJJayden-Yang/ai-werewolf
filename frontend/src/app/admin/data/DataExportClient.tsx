"use client";

import { useState } from "react";

import { buildDownloadUrl, type ArmsResponse } from "@/lib/api/dataExportApi";

const ALL_TYPES = ["events", "traces", "belief_states", "replay_truth"] as const;

function armLabel(arm: string): string {
  return /^v\d$/.test(arm) ? arm.toUpperCase() : arm;
}

export function DataExportClient({ initial }: { initial: ArmsResponse }) {
  const arms = initial.arms;
  const [selectedArm, setSelectedArm] = useState<string | null>(arms[0]?.arm ?? null);
  const [types, setTypes] = useState<string[]>([...ALL_TYPES]);
  const [limit, setLimit] = useState<string>("");

  function toggleType(t: string) {
    setTypes((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  }

  const selected = arms.find((a) => a.arm === selectedArm);
  const canDownload = Boolean(selectedArm && types.length > 0 && (selected?.games ?? 0) > 0);
  const href =
    canDownload && selectedArm
      ? buildDownloadUrl(selectedArm, types, limit ? Number(limit) : undefined)
      : "#";

  return (
    <div className="de-root">
      <p className="de-kicker">Data Export</p>
      <h1 className="de-title">按版本下载对局数据</h1>
      <p className="de-lede">
        选版本 → 看各自多少局 → 下载 ZIP（保留 <strong>events / traces / belief_states / replay_truth</strong> 原结构）。
      </p>

      {arms.length === 0 ? (
        <p className="de-faint">暂无数据（后端 AI_WOLF_DATA_DIR 下没有 events）。</p>
      ) : (
        <>
          <div className="de-section-title">版本</div>
          <div className="de-arms">
            {arms.map((a) => (
              <button
                key={a.arm}
                className={`de-arm${selectedArm === a.arm ? " on" : ""}`}
                onClick={() => setSelectedArm(a.arm)}
              >
                <span className="av">{armLabel(a.arm)}</span>
                <span className="ac">{a.games} 局</span>
              </button>
            ))}
          </div>

          <div className="de-section-title">数据类型</div>
          <div className="de-types">
            {ALL_TYPES.map((t) => (
              <label key={t} className="de-check">
                <input type="checkbox" checked={types.includes(t)} onChange={() => toggleType(t)} />
                <span className="tname">{t}</span>
              </label>
            ))}
          </div>

          <hr className="de-divider" />

          <div className="de-limit">
            只取最近 N 局（留空 = 全部）：
            <input
              className="de-input"
              type="number"
              min={1}
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
              placeholder="全部"
              aria-label="最近 N 局"
            />
          </div>

          <a
            className="de-download"
            href={href}
            aria-disabled={!canDownload}
            onClick={(e) => {
              if (!canDownload) e.preventDefault();
            }}
          >
            <svg className="icon" viewBox="0 0 24 24">
              <path d="M12 3v12m0 0l-4-4m4 4l4-4" />
              <path d="M5 21h14" />
            </svg>
            下载 {selectedArm ? armLabel(selectedArm) : ""} 数据
            {selected ? `（${selected.games} 局）` : ""}
          </a>
        </>
      )}
    </div>
  );
}
