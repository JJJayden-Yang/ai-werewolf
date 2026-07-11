import Link from "next/link";

import { getApiBaseUrl } from "@/lib/api/client";

import { SuspicionNetworkClient, type NetworkExport } from "./SuspicionNetworkClient";

type PageProps = {
  params: Promise<{ gameId: string }>;
};

async function fetchNetwork(gameId: string): Promise<NetworkExport | null> {
  try {
    const res = await fetch(
      `${getApiBaseUrl()}/api/audit/runs/${encodeURIComponent(gameId)}/suspicion-network`,
      { cache: "no-store" }
    );
    if (!res.ok) return null;
    return (await res.json()) as NetworkExport;
  } catch (error) {
    console.error("Error fetching suspicion network:", error);
    return null;
  }
}

export default async function AdminRunNetworkPage({ params }: PageProps) {
  const { gameId } = await params;
  const data = await fetchNetwork(gameId);
  const encoded = encodeURIComponent(gameId);

  return (
    <section className="audit-console">
      <header className="audit-console-header">
        <div>
          <p className="audit-kicker">Suspicion Network</p>
          <h1>{gameId}</h1>
          <p>怀疑网 / 心证面板（上帝视角赛后复盘）：每帧谁最怀疑谁 + 各 Agent 的一句话理由。</p>
        </div>
        <div className="audit-actions">
          <Link className="audit-button" href="/admin/runs">
            选择对局
          </Link>
          <Link className="audit-button" href={`/admin/runs/${encoded}/audit`}>
            审计视图
          </Link>
          <span className="audit-button primary" aria-current="page">
            怀疑网
          </span>
        </div>
      </header>

      {data ? (
        <>
          <section className="audit-metrics" aria-label="本局概览">
            <div className="audit-metric">
              <span>胜方</span>
              <strong>{data.winner ?? "-"}</strong>
              <small>game_over.payload.winner</small>
            </div>
            <div className="audit-metric">
              <span>人数</span>
              <strong>{data.player_count}</strong>
              <small>player_count</small>
            </div>
            <div className="audit-metric">
              <span>轮数</span>
              <strong>{data.rounds}</strong>
              <small>session.round</small>
            </div>
            <div className="audit-metric">
              <span>帧数</span>
              <strong>{data.suspicion_network_frames.length}</strong>
              <small>round × phase 快照</small>
            </div>
            <div className="audit-metric">
              <span>戏剧节点</span>
              <strong>{data.key_scenes.length}</strong>
              <small>放逐 / 夜刀 / 开枪</small>
            </div>
          </section>

          <section className="audit-surface" style={{ padding: "16px" }}>
            <SuspicionNetworkClient data={data} />
          </section>
        </>
      ) : (
        <section className="audit-surface">
          <div className="audit-empty-state">
            <strong>怀疑网数据无法加载</strong>
            <p>需后端 belief_states + AI_WOLF_DATA_DIR 指向落盘目录（v0 纯 LLM 局无可审计 belief）。</p>
          </div>
        </section>
      )}
    </section>
  );
}
