import { fetchJson } from "./client";

export type BeliefAccuracyRow = {
  role: string;
  arm: string;
  n_games: number;
  decisions: number;
  top1_accuracy: number | null;
  top2_accuracy: number | null;
  consistency_rate: number | null;
  avg_brier: number | null;
};

export type BeliefAccuracy = {
  games_total: number;
  games_with_belief: number;
  rows: BeliefAccuracyRow[];
};

export type ReviewMeta = {
  review_id: string;
  created_at: string;
  source_game_ids: string[];
  n_games: number;
  arm_counts: Record<string, number>;
  model_flavor: string | null;
  model_name: string | null;
  draft_count: number;
  drafts_by_role: Record<string, number>;
  dropped_out_of_scope: number;
  belief_accuracy: BeliefAccuracy;
};

export type EvidenceRef = {
  game_id: string;
  round: number | null;
  phase: string | null;
  trace_id: string | null;
};

export type StrategyInsightDraft = {
  draft_id: string;
  role: string;
  arm: string | null;
  target_layer: "role" | "advanced";
  target_file: string;
  current_excerpt: string | null;
  observed_issue: string;
  proposed_change: string;
  supporting_evidence: EvidenceRef[];
  potential_risk: string | null;
  review_status: "pending" | "approved" | "rejected";
  review_note: string | null;
};

export type ReviewDetail = {
  meta: ReviewMeta;
  drafts_by_role: Record<string, StrategyInsightDraft[]>;
};

export function listReviews(): Promise<{ reviews: ReviewMeta[] }> {
  return fetchJson("/api/strategy/reviews");
}

export function getReview(reviewId: string): Promise<ReviewDetail> {
  return fetchJson(`/api/strategy/reviews/${encodeURIComponent(reviewId)}`);
}

export function decideDraft(
  reviewId: string,
  draftId: string,
  status: "approved" | "rejected" | "pending",
  note?: string
): Promise<StrategyInsightDraft> {
  return fetchJson(
    `/api/strategy/reviews/${encodeURIComponent(reviewId)}/drafts/${encodeURIComponent(draftId)}/decision`,
    { method: "POST", body: { status, note } }
  );
}
