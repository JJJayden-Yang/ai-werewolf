import Link from "next/link";

import { getApiBaseUrl } from "@/lib/api/client";
import type { ReviewDetail } from "@/lib/api/strategyReviewApi";

import "../reviews.css";
import { ReviewDetailClient } from "./ReviewDetailClient";

export const dynamic = "force-dynamic";

async function fetchDetail(reviewId: string): Promise<ReviewDetail | null> {
  try {
    const res = await fetch(
      `${getApiBaseUrl()}/api/strategy/reviews/${encodeURIComponent(reviewId)}`,
      { cache: "no-store" }
    );
    if (!res.ok) return null;
    return (await res.json()) as ReviewDetail;
  } catch {
    return null;
  }
}

export default async function ReviewDetailPage({
  params,
}: {
  params: Promise<{ reviewId: string }>;
}) {
  const { reviewId } = await params;
  const detail = await fetchDetail(reviewId);

  if (!detail) {
    return (
      <div className="audit-console">
        <div className="sr-root">
          <Link className="sr-back" href="/admin/strategy/reviews">← 返回列表</Link>
          <h1 className="sr-title">未找到复盘批次：{reviewId}</h1>
        </div>
      </div>
    );
  }

  return <ReviewDetailClient detail={detail} />;
}
