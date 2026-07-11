import Link from "next/link";

import { getApiBaseUrl } from "@/lib/api/client";

import { SingleRunAuditClient } from "./SingleRunAuditClient";

type PageProps = {
  params: Promise<{ gameId: string }>;
};

async function fetchAuditRun(gameId: string) {
  try {
    const apiUrl = getApiBaseUrl();
    const response = await fetch(`${apiUrl}/api/audit/runs/${gameId}`, {
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    const data = await response.json();
    return data.audit;
  } catch (error) {
    console.error("Error fetching audit run:", error);
    return null;
  }
}

export default async function AdminRunAuditPage({ params }: PageProps) {
  const { gameId } = await params;
  const audit = await fetchAuditRun(gameId);

  if (!audit) {
    return (
      <section className="audit-console">
        <header className="audit-console-header">
          <div>
            <p className="audit-kicker">Run Audit</p>
            <h1>{gameId}</h1>
            <p>这局的审计数据无法加载。</p>
          </div>
          <Link className="audit-button primary" href="/admin/runs">
            返回选择对局
          </Link>
        </header>
        <section className="audit-surface">
          <div className="audit-empty-state">
            <strong>加载失败</strong>
            <p>对局不存在或审计数据无法生成。请返回列表重新选择。</p>
          </div>
        </section>
      </section>
    );
  }

  return <SingleRunAuditClient audit={audit} />;
}
