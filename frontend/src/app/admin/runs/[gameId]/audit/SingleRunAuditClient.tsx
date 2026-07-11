"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

type JsonObject = Record<string, unknown>;

type GameEvent = {
  event_id: string;
  game_id: string;
  round: number;
  phase: string;
  event_type: string;
  actor: string | null;
  target: string | null;
  visibility: string;
  payload: JsonObject;
  created_at?: string;
};

type AgentTrace = {
  trace_id: string;
  game_id: string;
  round: number;
  phase: string;
  agent_id: string;
  role: string;
  agent_version: string;
  prompt_version_id: string | null;
  model_name: string | null;
  input_summary: JsonObject;
  decision_output: JsonObject;
  decision_quality_flags: JsonObject;
};

type BeliefSnapshot = {
  game_id: string;
  agent_id: string;
  round: number;
  phase: string;
  is_shadow: boolean;
  beliefs: Record<string, any>;
  last_updated_event_id: string;
};

type BeliefData = {
  agent_id: string;
  is_shadow: boolean;
  history: BeliefSnapshot[];
  update_count: number;
};

type RunAuditData = {
  summary: JsonObject & {
    game_id: string;
    winner?: string;
    rounds?: number;
    agent_stats?: { ok?: number };
  };
  events: GameEvent[];
  traces: AgentTrace[];
  beliefs?: Record<string, BeliefData>;
  phaseOrder: string[];
  phaseCounts: Record<
    string,
    {
      events: number;
      traces: number;
      actors?: string[];
      event_types?: string[];
    }
  >;
};

type SingleRunAuditClientProps = {
  audit: RunAuditData;
};

type TimelineRow = {
  event: GameEvent;
  eventIndex: number;
  trace: AgentTrace | null;
  traceIndex: number;
};

export function SingleRunAuditClient({ audit }: SingleRunAuditClientProps) {
  const { events, traces, summary, beliefs = {} } = audit;
  const traceByEventIndex = useMemo(() => buildTraceMatches(events, traces), [events, traces]);
  const timelineRows = useMemo<TimelineRow[]>(
    () =>
      events.map((event, eventIndex) => ({
        event,
        eventIndex,
        ...(traceByEventIndex.get(eventIndex) ?? { trace: null, traceIndex: -1 })
      })),
    [events, traceByEventIndex]
  );

  const [activeSection, setActiveSection] = useState<"audit" | "raw">("audit");
  const [selectedPhase, setSelectedPhase] = useState("");
  const [query, setQuery] = useState("");
  const [eventType, setEventType] = useState("");
  const [traceFilter, setTraceFilter] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  const eventTypes = useMemo(
    () => Array.from(new Set(events.map((event) => event.event_type))).sort(),
    [events]
  );

  const filteredRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return timelineRows.filter((row) => {
      const haystack = JSON.stringify(row).toLowerCase();
      return (
        (!selectedPhase || phaseKey(row.event) === selectedPhase) &&
        (!eventType || row.event.event_type === eventType) &&
        (traceFilter !== "with_trace" || row.trace) &&
        (traceFilter !== "event_only" || !row.trace) &&
        (!needle || haystack.includes(needle))
      );
    });
  }, [eventType, query, selectedPhase, timelineRows, traceFilter]);

  const selectedRow = filteredRows[selectedIndex] ?? filteredRows[0] ?? timelineRows[0];
  const matchedTraceCount = traceByEventIndex.size;
  const eventOnlyCount = filteredRows.length - filteredRows.filter((row) => row.trace).length;
  const retryTotal = filteredRows.reduce(
    (sum, row) => sum + numberFrom(row.trace?.decision_quality_flags.retry_count),
    0
  );

  function selectRow(index: number) {
    setSelectedIndex(index);
  }

  function refreshList() {
    setSelectedIndex(0);
  }

  return (
    <section className="audit-console">
      <header className="audit-console-header">
        <div>
          <p className="audit-kicker">Run Audit</p>
          <h1>{summary.game_id}</h1>
          <p>统一事件时间线：有 Trace 就合并展示，没有 Trace 就只展示 Event。</p>
        </div>
        <div className="audit-actions">
          <Link className="audit-button" href="/admin/runs">
            选择对局
          </Link>
          <Link
            className="audit-button"
            href={`/admin/runs/${encodeURIComponent(summary.game_id)}/network`}
          >
            怀疑网
          </Link>
          <button
            className={`audit-button ${activeSection === "audit" ? "primary" : ""}`}
            onClick={() => setActiveSection("audit")}
            type="button"
          >
            审计视图
          </button>
          <button
            className={`audit-button ${activeSection === "raw" ? "primary" : ""}`}
            onClick={() => setActiveSection("raw")}
            type="button"
          >
            原始 JSON
          </button>
        </div>
      </header>

      <section className="audit-metrics" aria-label="本局指标">
        <Metric label="胜方" note="game_over.payload.winner" value={String(summary.winner ?? "-")} />
        <Metric label="轮数" note="session.round" value={String(summary.rounds ?? "-")} />
        <Metric label="事件行数" note="events/*.jsonl" value={String(events.length)} />
        <Metric label="Trace 行数" note="traces/*.jsonl" value={String(traces.length)} />
        <Metric label="已合并" note="Trace → Event" value={`${matchedTraceCount} / ${traces.length}`} />
        <Metric
          label="LLM ok"
          note="真实 v1 决策"
          value={String(summary.agent_stats?.ok ?? "-")}
        />
      </section>

      {activeSection === "audit" ? (
        <div className="audit-run-grid audit-run-grid-three">
          <aside className="audit-surface">
            <div className="audit-surface-head">
              <div>
                <h2>阶段索引</h2>
                <span>{audit.phaseOrder.length} 个阶段节点</span>
              </div>
            </div>
            <div className="audit-phase-list">
              {audit.phaseOrder.map((key) => {
                const count = audit.phaseCounts[key];
                const actors = count?.actors ?? [];
                const eventTypes = count?.event_types ?? [];
                return (
                  <button
                    className={`audit-phase-item ${selectedPhase === key ? "active" : ""}`}
                    key={key}
                    onClick={() => {
                      setSelectedPhase(selectedPhase === key ? "" : key);
                      setSelectedIndex(0);
                    }}
                    type="button"
                  >
                    <strong>{key}</strong>
                    <span className="phase-stats">
                      {count?.events ?? 0} events / {count?.traces ?? 0} traces
                    </span>
                    {actors.length > 0 && (
                      <span className="phase-actors">
                        角色: {actors.slice(0, 3).join(", ")}
                        {actors.length > 3 ? `+${actors.length - 3}` : ""}
                      </span>
                    )}
                    {eventTypes.length > 0 && (
                      <span className="phase-event-types">
                        {eventTypes.slice(0, 2).join(" / ")}
                        {eventTypes.length > 2 ? ` +${eventTypes.length - 2}` : ""}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </aside>

          <section className="audit-surface">
            <div className="audit-surface-head">
              <div>
                <h2>统一事件时间线</h2>
                <span>
                  {filteredRows.length} / {timelineRows.length} 行
                </span>
              </div>
            </div>

            <div className="audit-timeline-stats">
              <Stat label="当前行" note="按阶段/筛选过滤" value={filteredRows.length} />
              <Stat
                label="Event + Trace"
                note="Agent 决策产出"
                value={filteredRows.filter((row) => row.trace).length}
              />
              <Stat label="仅 Event" note="系统/结算/公开事实" value={eventOnlyCount} />
              <Stat label="retry 合计" note="decision_quality_flags" value={retryTotal} />
            </div>

            <div className="audit-filters audit-filters-three">
              <input
                aria-label="搜索时间线"
                onChange={(event) => {
                  setQuery(event.target.value);
                  refreshList();
                }}
                placeholder="搜索 event / trace / actor / target / payload / prompt"
                value={query}
              />
              <select
                aria-label="筛选事件类型"
                onChange={(event) => {
                  setEventType(event.target.value);
                  refreshList();
                }}
                value={eventType}
              >
                <option value="">全部事件类型</option>
                {eventTypes.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
              <select
                aria-label="筛选 Trace"
                onChange={(event) => {
                  setTraceFilter(event.target.value);
                  refreshList();
                }}
                value={traceFilter}
              >
                <option value="">全部行</option>
                <option value="with_trace">有 Trace</option>
                <option value="event_only">仅 Event</option>
              </select>
            </div>

            <div className="audit-table-wrap">
              <table className="audit-table audit-timeline-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>阶段</th>
                    <th>Event</th>
                    <th>行动者</th>
                    <th>可见性</th>
                    <th>Event 内容</th>
                    <th>Trace 决策</th>
                    <th>质量 / token</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((row, index) => (
                    <TimelineTableRow
                      index={index}
                      key={row.event.event_id}
                      onSelect={() => selectRow(index)}
                      row={row}
                      selected={selectedRow?.event.event_id === row.event.event_id}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <DetailPane row={selectedRow} beliefs={beliefs} />
        </div>
      ) : (
        <section className="audit-surface">
          <div className="audit-surface-head">
            <div>
              <h2>原始数据</h2>
              <span>嵌入自 JSONL demo 数据</span>
            </div>
          </div>
          <pre className="audit-raw-json">
            {JSON.stringify(
              {
                summary,
                first_event: events[0],
                first_trace: traces[0],
                matched_traces: matchedTraceCount
              },
              null,
              2
            )}
          </pre>
        </section>
      )}
    </section>
  );
}

function TimelineTableRow({
  index,
  onSelect,
  row,
  selected
}: {
  index: number;
  onSelect: () => void;
  row: TimelineRow;
  selected: boolean;
}) {
  const { event, trace } = row;
  const input = trace?.input_summary ?? {};
  const output = trace?.decision_output ?? {};
  const quality = trace?.decision_quality_flags ?? {};
  const eventText = `${event.event_type}${event.target ? ` → ${event.target}` : ""}`;
  const traceText = trace
    ? `${stringFrom(output.action_type, "-")}${output.target ? ` → ${String(output.target)}` : ""}`
    : "无 Trace";

  return (
    <tr className={selected ? "selected" : ""} onClick={onSelect}>
      <td>{index + 1}</td>
      <td>
        R{event.round}
        <small>{event.phase}</small>
      </td>
      <td>
        <span className="audit-pill">{eventText}</span>
        <small>{event.event_id}</small>
      </td>
      <td>
        {event.actor ?? "-"}
        <small>{event.target ?? ""}</small>
      </td>
      <td>
        <span className={`audit-pill ${event.visibility.includes("private") ? "event" : "trace"}`}>
          {event.visibility}
        </span>
      </td>
      <td>{compactJson(event.payload)}</td>
      <td>
        <span className={`audit-pill ${trace ? "ready" : "muted-pill"}`}>{traceText}</span>
        <small>
          {trace
            ? `${trace.agent_id} / ${trace.role} / public:${stringFrom(input.recent_public_events_count, "-")} / private:${stringFrom(input.private_events_count, "-")}`
            : "系统事件、结算事件或非 Agent 产出"}
        </small>
      </td>
      <td>
        {trace
          ? `${stringFrom(quality.outcome, "-")} / retry:${stringFrom(quality.retry_count, 0)} / ${formatMs(quality.llm_latency_ms)} / tok:${stringFrom(getTokenTotal(quality), "-")}`
          : "-"}
        <small>{trace?.prompt_version_id ?? ""}</small>
      </td>
    </tr>
  );
}

function DetailPane({ row, beliefs }: { row?: TimelineRow; beliefs?: Record<string, BeliefData> }) {
  const [jsonOpen, setJsonOpen] = useState(true);

  if (!row) {
    return (
      <aside className="audit-surface audit-detail-pane">
        <p className="audit-empty">没有匹配的行。</p>
      </aside>
    );
  }

  const { event, trace } = row;
  const input = trace?.input_summary ?? {};
  const output = trace?.decision_output ?? {};
  const quality = trace?.decision_quality_flags ?? {};

  let beliefSnapshot: BeliefSnapshot | undefined;
  if (trace && beliefs) {
    const beliefData = beliefs[`${trace.agent_id}_real`] || beliefs[`${trace.agent_id}_shadow`];
    if (beliefData?.history) {
      beliefSnapshot = findBeliefsForPhase(beliefData.history, event.round, event.phase);
    }
  }

  return (
    <aside className="audit-surface audit-detail-pane">
      <div className="audit-surface-head">
        <div>
          <h2>选中明细</h2>
          <span>{trace ? "Event + Trace" : "Event"}{beliefSnapshot ? " + Belief" : ""}</span>
        </div>
      </div>

      {/* 区块一：Event + Trace */}
      <div className="audit-detail-section">
        <div className="audit-kv">
          <Row label="event" value={event.event_id} />
          <Row label="trace" value={trace?.trace_id ?? "无"} />
          <Row label="阶段" value={`R${event.round} / ${event.phase}`} />
          <Row label="事件" value={`${event.event_type}${event.target ? ` → ${event.target}` : ""}`} />
          <Row label="actor" value={event.actor ?? "-"} />
          <Row label="visibility" value={event.visibility} />
          <Row label="决策" value={`${stringFrom(output.action_type, "无 Trace")}${output.target ? ` → ${String(output.target)}` : ""}`} />
          <Row label="context" value={`round:${stringFrom(input.current_round_events_count, "-")} / public:${stringFrom(input.recent_public_events_count, "-")} / private:${stringFrom(input.private_events_count, "-")}`} />
          <Row label="quality" value={`${stringFrom(quality.outcome, "-")} / retry ${stringFrom(quality.retry_count, "-")} / ${formatMs(quality.llm_latency_ms)}`} />
          <Row label="token" value={`${stringFrom(getTokenPart(quality, "prompt_tokens"), "-")} + ${stringFrom(getTokenPart(quality, "completion_tokens"), "-")} = ${stringFrom(getTokenTotal(quality), "-")}`} />
        </div>
      </div>

      {/* 区块二：Belief 快照（有数据才显示） */}
      {beliefSnapshot && (
        <div className="audit-detail-section">
          <div className="audit-detail-section-head">
            <span>信念快照</span>
            <small>R{beliefSnapshot.round} / {beliefSnapshot.phase} · {beliefSnapshot.is_shadow ? "shadow" : "real"}</small>
          </div>
          <div className="audit-belief-preview">
            {formatBeliefSnapshot(beliefSnapshot)}
          </div>
        </div>
      )}

      {/* 区块三：原始 JSON（折叠） */}
      <div className="audit-detail-section">
        <button
          className="audit-detail-section-toggle"
          onClick={() => setJsonOpen((v) => !v)}
          type="button"
        >
          <span>{jsonOpen ? "▼" : "▶"} 原始 JSON</span>
        </button>
        {jsonOpen && (
          <pre className="audit-raw-json compact">
            {JSON.stringify({ event, trace, belief: beliefSnapshot }, null, 2)}
          </pre>
        )}
      </div>
    </aside>
  );
}

function Metric({ label, note, value }: { label: string; note: string; value: string }) {
  return (
    <div className="audit-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}

function Stat({ label, note, value }: { label: string; note: string; value: number }) {
  return (
    <div className="audit-stat">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function buildTraceMatches(events: GameEvent[], traces: AgentTrace[]) {
  const used = new Set<number>();
  const byEventIndex = new Map<number, { trace: AgentTrace; traceIndex: number }>();

  traces.forEach((trace, traceIndex) => {
    const expected = eventTypeForTrace(trace);
    const output = trace.decision_output ?? {};
    const eventIndex = events.findIndex((event, index) => {
      return (
        !used.has(index) &&
        event.round === trace.round &&
        event.phase === trace.phase &&
        event.actor === trace.agent_id &&
        event.event_type === expected &&
        (output.target == null || event.target === output.target) &&
        (expected !== "agent_action" || event.payload?.action_type === output.action_type)
      );
    });

    if (eventIndex >= 0) {
      used.add(eventIndex);
      byEventIndex.set(eventIndex, { trace, traceIndex });
    }
  });

  return byEventIndex;
}

function eventTypeForTrace(trace: AgentTrace) {
  const action = trace.decision_output?.action_type;
  if (action === "night_kill_nominate") return "wolf_nomination";
  if (action === "check") return "seer_check_result";
  if (action === "save") return "witch_save";
  if (action === "poison") return "witch_poison";
  if (action === "vote") return "vote_cast";
  if (action === "speak") return trace.phase === "EXILE_LAST_WORDS" ? "last_words" : "speech";
  if (action === "skip") return "agent_action";
  return "";
}

function phaseKey(item: { phase: string; round: number }) {
  // 只返回 phase，与 audit.phaseOrder 和 selectedPhase 匹配
  // （phaseOrder 中已经按出现顺序排序，round 信息用处不大）
  return item.phase;
}

function compactJson(value: unknown, max = 150) {
  const raw = JSON.stringify(value ?? {}).replace(/[{}"]/g, "") || "{}";
  return raw.length > max ? `${raw.slice(0, max - 1)}…` : raw;
}

function stringFrom(value: unknown, fallback: string | number) {
  if (value == null || value === "") return String(fallback);
  return String(value);
}

function numberFrom(value: unknown) {
  return typeof value === "number" ? value : 0;
}

function getTokenPart(quality: JsonObject, key: string) {
  const tokenUsage = quality.token_usage;
  if (!tokenUsage || typeof tokenUsage !== "object") return undefined;
  return (tokenUsage as JsonObject)[key];
}

function getTokenTotal(quality: JsonObject) {
  return getTokenPart(quality, "total_tokens");
}

function formatMs(value: unknown) {
  return typeof value === "number" ? `${Math.round(value).toLocaleString("zh-CN")}ms` : "-";
}

function findBeliefsForPhase(
  history: BeliefSnapshot[],
  round: number,
  phase: string
): BeliefSnapshot | undefined {
  // 按时间顺序查找：
  // 1. 精确匹配 round/phase
  // 2. 如果没有，返回该 round 之前最晚的 belief

  let exactMatch: BeliefSnapshot | undefined;
  let beforeRound: BeliefSnapshot | undefined;

  for (const snapshot of history) {
    if (snapshot.round === round && snapshot.phase === phase) {
      exactMatch = snapshot;
      break;
    }
    if (snapshot.round < round) {
      beforeRound = snapshot;
    } else if (snapshot.round === round && snapshot.phase < phase) {
      // 同一轮但阶段更早
      beforeRound = snapshot;
    }
  }

  return exactMatch || beforeRound;
}

function formatBeliefSnapshot(snapshot: BeliefSnapshot) {
  const beliefs = snapshot.beliefs || {};
  const entries = Object.entries(beliefs);

  if (entries.length === 0) {
    return <p style={{ fontSize: "12px", color: "#999" }}>暂无信念数据</p>;
  }

  // 为每个 player 找最高概率的角色
  const suspicions = entries.map(([playerId, roleBelief]) => {
    const rb = roleBelief as Record<string, number>;
    const roles = ["werewolf", "seer", "witch", "hunter", "villager"];
    let maxRole = "?";
    let maxProb = 0;

    for (const role of roles) {
      const prob = rb[role] || 0;
      if (prob > maxProb) {
        maxProb = prob;
        maxRole = role;
      }
    }

    const roleLabel: Record<string, string> = {
      werewolf: "狼",
      seer: "预",
      witch: "巫",
      hunter: "猎",
      villager: "民"
    };

    return {
      playerId,
      role: roleLabel[maxRole] || maxRole,
      confidence: (maxProb * 100).toFixed(0)
    };
  }).sort((a, b) => parseFloat(b.confidence) - parseFloat(a.confidence));

  return (
    <div style={{ fontSize: "12px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
      {suspicions.map(({ playerId, role, confidence }) => (
        <div key={playerId} style={{ padding: "4px 8px", backgroundColor: "#f5f5f5", borderRadius: "3px" }}>
          <strong>{playerId}</strong>
          <div style={{ color: "#666", fontSize: "11px" }}>
            {role} {confidence}%
          </div>
        </div>
      ))}
    </div>
  );
}
