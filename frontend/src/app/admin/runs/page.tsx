import { getApiBaseUrl } from "@/lib/api/client";

import { AuditRunSelectorClient, type AuditRunSummary } from "./AuditRunSelectorClient";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 100;

async function fetchFirstPage(): Promise<{ runs: AuditRunSummary[]; total: number }> {
  try {
    const apiUrl = getApiBaseUrl();
    // 只 SSR 渲染第一页：后端按 offset/limit 分页读盘，前端任一时刻只持有一页，防 OOM。
    // 后续页由 AuditRunSelectorClient 在浏览器里按需拉取。
    const response = await fetch(`${apiUrl}/api/audit/runs?limit=${PAGE_SIZE}&offset=0`, {
      cache: "no-store",
    });
    if (!response.ok) {
      console.error("Failed to fetch audit runs:", response.status);
      return { runs: [], total: 0 };
    }
    const data = await response.json();
    const runs = data.audit_runs || [];
    return { runs, total: data.total ?? runs.length };
  } catch (error) {
    console.error("Error fetching audit runs:", error);
    return { runs: [], total: 0 };
  }
}

export default async function AdminRunsPage() {
  const { runs, total } = await fetchFirstPage();
  return <AuditRunSelectorClient initialRuns={runs} total={total} />;
}
