"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { getApiBaseUrl } from "@/lib/api/client";

const PAGE_SIZE = 100;

export type AuditRunSummary = {
  gameId: string;
  createdAt: string;
  playerCount: number;
  strategy: string;
  winner: string | null;
  rounds: number;
  tags: string[];
  eventCount: number;
  traceCount: number;
  hasAuditPage: boolean;
  eventPath: string;
  tracePath: string | null;
};

type AuditRunSelectorClientProps = {
  /** 第一页（SSR 已加载）的对局摘要。 */
  initialRuns: AuditRunSummary[];
  /** 盘上全量对局数（只数文件名，不受 limit 截断）。 */
  total: number;
};

export function AuditRunSelectorClient({ initialRuns, total }: AuditRunSelectorClientProps) {
  const [query, setQuery] = useState("");
  const [playerCount, setPlayerCount] = useState("");
  const [keyEvent, setKeyEvent] = useState("");
  const [dataShape, setDataShape] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  // runs 只保存「当前页」的行：第 1 页用 SSR 初始数据，翻到第 N 页才去后端拉第 N 页。
  // 这样无论盘上多少局，前端任一时刻只持有 PAGE_SIZE 条，后端也只读一页文件，防 OOM。
  const [runs, setRuns] = useState<AuditRunSummary[]>(initialRuns);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedGameId, setSelectedGameId] = useState(initialRuns[0]?.gameId ?? "");

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const clampedPage = Math.min(page, totalPages);

  useEffect(() => {
    if (clampedPage === 1) {
      setRuns(initialRuns);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    const offset = (clampedPage - 1) * PAGE_SIZE;
    fetch(`${getApiBaseUrl()}/api/audit/runs?limit=${PAGE_SIZE}&offset=${offset}`, {
      cache: "no-store",
    })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => {
        if (cancelled) return;
        setRuns(data.audit_runs ?? []);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(String(err));
        setRuns([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [clampedPage, initialRuns]);

  const playerOptions = useMemo(
    () => Array.from(new Set(runs.map((run) => run.playerCount).filter(Boolean))).sort(),
    [runs]
  );

  const tagOptions = useMemo(
    () => Array.from(new Set(runs.flatMap((run) => run.tags ?? []))).sort(),
    [runs]
  );

  // 筛选/搜索只作用于「当前已加载的这一页」——真分页下不可能在不读全部对局的前提下全局搜索。
  const filteredRuns = useMemo(() => {
    const needle = query.trim().toLowerCase();

    return runs.filter((run) => {
      const haystack = JSON.stringify(run).toLowerCase();
      return (
        (!needle || haystack.includes(needle)) &&
        (!playerCount || String(run.playerCount) === playerCount) &&
        (!keyEvent || (run.tags ?? []).includes(keyEvent)) &&
        (dataShape !== "with_trace" || run.traceCount > 0) &&
        (dataShape !== "event_only" || run.traceCount === 0) &&
        (status !== "ready" || run.hasAuditPage) &&
        (status !== "trace_ready" || (!run.hasAuditPage && run.traceCount > 0)) &&
        (status !== "event_only" || run.traceCount === 0)
      );
    });
  }, [dataShape, keyEvent, playerCount, query, runs, status]);

  const selectedRun =
    filteredRuns.find((run) => run.gameId === selectedGameId) ?? filteredRuns[0] ?? runs[0];

  return (
    <section className="audit-console">
      <header className="audit-console-header">
        <div>
          <p className="audit-kicker">Run Audit</p>
          <h1>选择对局</h1>
          <p>从 events / traces 数据里选择一局进入单局审计。</p>
        </div>
        <Link className="audit-button primary" href="/admin">
          Admin
        </Link>
      </header>

      <section className="audit-metrics" aria-label="数据概览">
        <Metric label="对局数" value={total} note="" />
      </section>

      <div className="audit-run-grid">
        <section className="audit-surface">
          <div className="audit-surface-head">
            <div>
              <h2>对局列表</h2>
              <span>
                第 {clampedPage} / {totalPages} 页 · 本页 {filteredRuns.length} 局 · 共 {total} 局
              </span>
            </div>
          </div>

          <div className="audit-filters">
            <input
              aria-label="搜索对局"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索 game_id / winner / strategy / path"
              value={query}
            />
            <select
              aria-label="筛选人数"
              onChange={(event) => setPlayerCount(event.target.value)}
              value={playerCount}
            >
              <option value="">全部人数</option>
              {playerOptions.map((count) => (
                <option key={count} value={count}>
                  {count} 人
                </option>
              ))}
            </select>
            <select
              aria-label="筛选关键事件"
              onChange={(event) => setKeyEvent(event.target.value)}
              value={keyEvent}
            >
              <option value="">全部关键事件</option>
              {tagOptions.map((tag) => (
                <option key={tag} value={tag}>
                  {tag}
                </option>
              ))}
            </select>
            <select
              aria-label="筛选数据形态"
              onChange={(event) => setDataShape(event.target.value)}
              value={dataShape}
            >
              <option value="">全部数据形态</option>
              <option value="with_trace">Event + Trace</option>
              <option value="event_only">仅 Event</option>
            </select>
            <select
              aria-label="筛选状态"
              onChange={(event) => setStatus(event.target.value)}
              value={status}
            >
              <option value="">全部状态</option>
              <option value="ready">可打开</option>
              <option value="trace_ready">有 Trace 待生成</option>
              <option value="event_only">仅 Event</option>
            </select>
          </div>

          <div className="audit-table-wrap">
            <table className="audit-table audit-run-table">
              <thead>
                <tr>
                  <th>Game ID</th>
                  <th>人数</th>
                  <th>策略</th>
                  <th>胜方</th>
                  <th>轮数</th>
                  <th>关键事件</th>
                  <th>数据</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr>
                    <td colSpan={9} className="audit-empty">
                      加载中…
                    </td>
                  </tr>
                )}
                {!loading && error && (
                  <tr>
                    <td colSpan={9} className="audit-empty">
                      加载失败：{error}
                    </td>
                  </tr>
                )}
                {!loading && !error && filteredRuns.length === 0 && (
                  <tr>
                    <td colSpan={9} className="audit-empty">
                      本页没有匹配的对局。
                    </td>
                  </tr>
                )}
                {!loading &&
                  !error &&
                  filteredRuns.map((run) => (
                  <tr
                    className={run.gameId === selectedRun?.gameId ? "selected" : ""}
                    key={run.gameId}
                    onClick={() => setSelectedGameId(run.gameId)}
                  >
                    <td>
                      <strong>{run.gameId}</strong>
                      <small>{formatDate(run.createdAt)}</small>
                    </td>
                    <td>{run.playerCount || "-"} 人</td>
                    <td>{run.strategy}</td>
                    <td>{run.winner ?? "未结束/未记录"}</td>
                    <td>R{run.rounds}</td>
                    <td>
                      {(run.tags ?? []).length ? (
                        <span className="audit-tags">
                          {(run.tags ?? []).map((tag) => (
                            <span className="audit-tag" key={tag}>
                              {tag}
                            </span>
                          ))}
                        </span>
                      ) : (
                        <span className="audit-tag-empty">—</span>
                      )}
                    </td>
                    <td>
                      {run.eventCount} events
                      <small>{run.traceCount} traces</small>
                    </td>
                    <td>
                      <StatusPill run={run} />
                    </td>
                    <td>
                      {run.hasAuditPage ? (
                        <Link
                          className="audit-button compact primary"
                          href={`/admin/runs/${encodeURIComponent(run.gameId)}/audit`}
                          onClick={(event) => event.stopPropagation()}
                        >
                          打开审计
                        </Link>
                      ) : (
                        <span className="audit-button compact disabled">待生成</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="audit-pagination">
              <button
                className="audit-button compact"
                disabled={loading || clampedPage <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                type="button"
              >
                上一页
              </button>
              <span>
                第 {clampedPage} / {totalPages} 页
              </span>
              <button
                className="audit-button compact"
                disabled={loading || clampedPage >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                type="button"
              >
                下一页
              </button>
              <small>共 {total} 局</small>
            </div>
          )}
        </section>

        <aside className="audit-surface audit-detail-pane">
          <div className="audit-surface-head">
            <div>
              <h2>选中对局</h2>
              {selectedRun ? <StatusPill run={selectedRun} /> : null}
            </div>
          </div>
          {selectedRun ? (
            <>
              <div className="audit-kv">
                <Row label="game_id" value={selectedRun.gameId} />
                <Row label="创建时间" value={formatDate(selectedRun.createdAt)} />
                <Row
                  label="配置"
                  value={`${selectedRun.playerCount} 人 / ${selectedRun.strategy}`}
                />
                <Row
                  label="结果"
                  value={`${selectedRun.winner ?? "未结束/未记录"} / R${selectedRun.rounds}`}
                />
                <Row
                  label="关键事件"
                  value={(selectedRun.tags ?? []).join(" · ") || "无"}
                />
                <Row
                  label="数据量"
                  value={`${selectedRun.eventCount} events / ${selectedRun.traceCount} traces`}
                />
                <Row label="event" value={selectedRun.eventPath} mono />
                <Row label="trace" value={selectedRun.tracePath ?? "无"} mono />
              </div>
              <div className="audit-note">
                <strong>下一步</strong>
                <p>{nextStepText(selectedRun)}</p>
              </div>
            </>
          ) : (
            <p className="audit-empty">没有匹配的对局。</p>
          )}
        </aside>
      </div>
    </section>
  );
}

function Metric({ label, note, value }: { label: string; note: string; value: number }) {
  return (
    <div className="audit-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}

function Row({ label, mono = false, value }: { label: string; mono?: boolean; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong className={mono ? "audit-mono" : undefined}>{value}</strong>
    </div>
  );
}

function StatusPill({ run }: { run: AuditRunSummary }) {
  if (run.hasAuditPage) return <span className="audit-pill ready">可打开</span>;
  if (run.traceCount > 0) return <span className="audit-pill trace">有 Trace</span>;
  return <span className="audit-pill event">仅 Event</span>;
}

function nextStepText(run: AuditRunSummary) {
  if (run.hasAuditPage) {
    return "这局已经有单局审计页，可以直接打开查看统一事件时间线。";
  }
  if (run.traceCount > 0) {
    return "这局已有 event + trace，下一步可以生成同样的单局审计页。";
  }
  return "这局目前只有 event，适合先做 event-only 审计或 replay 对照。";
}

function formatDate(value: string) {
  if (!value) return "-";
  return value.replace("T", " ").replace(/\+00:00$/, " UTC").slice(0, 23);
}
