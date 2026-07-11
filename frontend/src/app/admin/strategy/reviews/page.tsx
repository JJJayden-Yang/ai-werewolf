import Link from "next/link";

import { getApiBaseUrl } from "@/lib/api/client";
import type { ReviewMeta } from "@/lib/api/strategyReviewApi";

import "./reviews.css";

export const dynamic = "force-dynamic";

async function fetchReviews(): Promise<ReviewMeta[]> {
  try {
    const res = await fetch(`${getApiBaseUrl()}/api/strategy/reviews`, { cache: "no-store" });
    if (!res.ok) return [];
    const data = await res.json();
    return data.reviews ?? [];
  } catch {
    return [];
  }
}

const ARM_CLS: Record<string, string> = { v0: "gold", v1: "blue", v2: "green" };

export default async function StrategyReviewsPage() {
  const reviews = await fetchReviews();
  return (
    <div className="audit-console">
      <div className="sr-root">
        <p className="sr-kicker">Strategy Review</p>
        <h1 className="sr-title">策略复盘</h1>
        <p className="sr-lede">
          每跑 ~50 局产出一批<strong>策略层 prompt</strong> 改进建议（仅 role / advanced 两层），
          逐条人审采纳或驳回；并排展示 belief 命中率，用于对比 v1 / v2 哪个 kernel 更准。
        </p>

        {reviews.length === 0 ? (
          <p className="sr-faint">
            暂无复盘批次。在服务器跑 <code>python scripts/run_strategy_review.py --last 50</code> 生成。
          </p>
        ) : (
          <div className="sr-review-list">
            {reviews.map((r) => (
              <Link key={r.review_id} className="sr-card sr-surface" href={`/admin/strategy/reviews/${r.review_id}`}>
                <div>
                  <div className="sr-rid">{r.review_id}</div>
                  <div className="sr-meta-row">
                    <span>{new Date(r.created_at).toLocaleString()}</span>
                    <span className="sep">·</span>
                    <span>{r.n_games} 局</span>
                    <span className="sep">·</span>
                    <span className="sr-chips">
                      {Object.entries(r.arm_counts ?? {}).map(([a, n]) => (
                        <span key={a} className={`sr-pill ${ARM_CLS[a] ?? ""}`}>
                          {a.toUpperCase()} · {n}
                        </span>
                      ))}
                    </span>
                    <span className="sep">·</span>
                    <span>复盘模型 {r.model_name ?? r.model_flavor ?? "—"}</span>
                  </div>
                </div>
                <div className="sr-count">
                  <div className="n">{r.draft_count}</div>
                  <div className="l">条建议</div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
