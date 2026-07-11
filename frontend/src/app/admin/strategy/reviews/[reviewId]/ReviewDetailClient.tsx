"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import {
  decideDraft,
  type BeliefAccuracyRow,
  type ReviewDetail,
  type StrategyInsightDraft,
} from "@/lib/api/strategyReviewApi";

const ROLE_CN: Record<string, string> = {
  seer: "预言家", villager: "村民", werewolf: "狼人", witch: "女巫", hunter: "猎人",
  all: "全局", global: "全局",
};
const ARM_CLS: Record<string, string> = { v0: "gold", v1: "blue", v2: "green" };
const STATUS_CN: Record<string, string> = { pending: "待审", approved: "已采纳", rejected: "已驳回" };

const pct = (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(1)}%`);
const accColor = (v: number) => (v >= 0.6 ? "var(--audit-green)" : v >= 0.4 ? "var(--audit-gold)" : "var(--audit-red)");

function AccBar({ v }: { v: number | null }) {
  if (v === null) return <span className="sr-acc"><span className="v">—</span></span>;
  return (
    <span className="sr-acc">
      <span className="bar"><i style={{ width: `${(v * 100).toFixed(0)}%`, background: accColor(v) }} /></span>
      <span className="v">{pct(v)}</span>
    </span>
  );
}

function BeliefPanel({ rows }: { rows: BeliefAccuracyRow[] }) {
  if (!rows.length) {
    return <p className="sr-faint">本批次无 belief 注入对局（v0 / 未注入）。</p>;
  }
  return (
    <div className="sr-surface">
      <table className="sr-belief">
        <thead>
          <tr>
            <th>角色</th><th>arm</th><th>局数</th><th>决策</th>
            <th>top1 命中</th><th>top2 命中</th><th>一致率</th><th>Brier</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.role}-${r.arm}-${i}`} className={r.role === "all" ? "role-all" : ""}>
              <td>{ROLE_CN[r.role] ?? r.role}</td>
              <td><span className={`sr-pill ${ARM_CLS[r.arm] ?? ""}`}>{r.arm}</span></td>
              <td className="num">{r.n_games}</td>
              <td className="num">{r.decisions}</td>
              <td><AccBar v={r.top1_accuracy} /></td>
              <td><AccBar v={r.top2_accuracy} /></td>
              <td className="num">{pct(r.consistency_rate)}</td>
              <td className="num">{r.avg_brier === null ? "—" : r.avg_brier.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DraftCard({ reviewId, draft }: { reviewId: string; draft: StrategyInsightDraft }) {
  const [status, setStatus] = useState(draft.review_status);
  const [note, setNote] = useState(draft.review_note ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function decide(next: "approved" | "rejected") {
    setBusy(true);
    setErr(null);
    try {
      const updated = await decideDraft(reviewId, draft.draft_id, next, note || undefined);
      setStatus(updated.review_status);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sr-draft" data-status={status}>
      <div className="sr-draft-top">
        <span className={`sr-layer-chip ${draft.target_layer}`}>{draft.target_layer}</span>
        <span className="sr-file" title={draft.target_file}>{draft.target_file}</span>
        <span className={`sr-status ${status}`}>{STATUS_CN[status] ?? status}</span>
      </div>

      <div className="sr-field"><div className="lab">观察到的问题</div><div className="val">{draft.observed_issue}</div></div>
      <div className="sr-field"><div className="lab">改进建议</div><div className="val">{draft.proposed_change}</div></div>

      <div className="sr-field">
        <div className="lab">当前片段</div>
        {draft.current_excerpt
          ? <pre className="sr-excerpt">{draft.current_excerpt}</pre>
          : <div className="sr-faint">（新增内容，无现有片段）</div>}
      </div>

      {draft.potential_risk ? (
        <div className="sr-field risk"><div className="lab">潜在风险</div><div className="val">{draft.potential_risk}</div></div>
      ) : null}

      {draft.supporting_evidence.length ? (
        <div className="sr-evidence">
          <span className="elab">证据</span>
          {draft.supporting_evidence.map((e, i) => (
            <Link
              key={i}
              className="sr-ev-link"
              href={`/admin/runs/${encodeURIComponent(e.game_id)}/audit`}
              title={`打开 ${e.game_id} 审计页`}
            >
              {e.game_id}{e.round ? ` · R${e.round}` : ""}{e.phase ? ` · ${e.phase}` : ""}
            </Link>
          ))}
        </div>
      ) : null}

      <div className="sr-foot">
        <input
          className="sr-note"
          placeholder="审核备注（可选）"
          aria-label="审核备注"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <button className="sr-btn approve" disabled={busy} onClick={() => decide("approved")}>采纳</button>
        <button className="sr-btn reject" disabled={busy} onClick={() => decide("rejected")}>驳回</button>
      </div>
      {err ? <p className="sr-err">{err}</p> : null}
    </div>
  );
}

export function ReviewDetailClient({ detail }: { detail: ReviewDetail }) {
  const { meta, drafts_by_role } = detail;
  const [layerFilter, setLayerFilter] = useState<"all" | "role" | "advanced">("all");
  const roles = useMemo(() => Object.keys(drafts_by_role).sort(), [drafts_by_role]);

  const armStr = Object.entries(meta.arm_counts ?? {})
    .map(([a, n]) => `${a.toUpperCase()}:${n}`).join(" · ");
  const shownCount = roles.reduce(
    (acc, role) => acc + drafts_by_role[role].filter((d) => layerFilter === "all" || d.target_layer === layerFilter).length,
    0
  );

  return (
    <div className="audit-console">
      <div className="sr-root">
        <Link className="sr-back" href="/admin/strategy/reviews">← 返回列表</Link>
        <p className="sr-kicker">Review</p>
        <h1 className="sr-title mono">{meta.review_id}</h1>
        <p className="sr-lede">{new Date(meta.created_at).toLocaleString()}{armStr ? ` · arm 分布 ${armStr}` : ""}</p>

        <div className="sr-stats">
          <div className="sr-metric"><span>分析对局</span><strong>{meta.n_games}</strong></div>
          <div className="sr-metric"><span>候选建议</span><strong className="gold">{meta.draft_count}</strong></div>
          <div className="sr-metric"><span>越界/泄漏丢弃</span><strong className="red">{meta.dropped_out_of_scope}</strong></div>
          <div
            className="sr-metric has-help"
            title="复盘分析用的 LLM —— 读对局摘要 + 当前 prompt 产出建议。与对局里 agent 所用的模型无关（那些局可能用别的模型）。由 --model-flavor 选定。"
          >
            <span>
              复盘模型 · 分析用
              <svg className="info" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 7.5v.5" strokeLinecap="round" />
              </svg>
            </span>
            <strong className="s">{meta.model_name ?? meta.model_flavor ?? "—"}</strong>
            <small>{meta.model_flavor ? `flavor ${meta.model_flavor} · ` : ""}≠ 对局模型</small>
          </div>
        </div>

        <div className="sr-section-head">
          <h2>belief 命中率</h2>
          <span className="hint">按角色 × arm（top 嫌疑是否命中真凶）</span>
          <span className="ro">只读 · 不可审</span>
        </div>
        <BeliefPanel rows={meta.belief_accuracy?.rows ?? []} />

        <div className="sr-toolbar">
          <div className="sr-section-head" style={{ margin: 0 }}>
            <h2>候选改进建议</h2>
            <span className="hint">共 {shownCount} 条</span>
          </div>
          <div className="sr-seg">
            {(["all", "role", "advanced"] as const).map((f) => (
              <button key={f} className={layerFilter === f ? "on" : ""} onClick={() => setLayerFilter(f)}>
                {f === "all" ? "全部" : f}
              </button>
            ))}
          </div>
        </div>

        {roles.length === 0 ? (
          <div className="sr-empty">本批次没有产出建议。</div>
        ) : (
          roles.map((role) => {
            const drafts = drafts_by_role[role].filter(
              (d) => layerFilter === "all" || d.target_layer === layerFilter
            );
            if (!drafts.length) return null;
            return (
              <div key={role} className="sr-role-group">
                <div className="sr-role-head">
                  <span className="rname">{ROLE_CN[role] ?? role}</span>
                  <span className="sr-pill">{drafts.length} 条</span>
                </div>
                {drafts.map((d) => (
                  <DraftCard key={d.draft_id} reviewId={meta.review_id} draft={d} />
                ))}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
