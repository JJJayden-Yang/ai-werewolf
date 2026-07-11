import { getApiBaseUrl } from "@/lib/api/client";
import type { ArmsResponse } from "@/lib/api/dataExportApi";

import "./data.css";
import { DataExportClient } from "./DataExportClient";

export const dynamic = "force-dynamic";

async function fetchArms(): Promise<ArmsResponse> {
  try {
    const res = await fetch(`${getApiBaseUrl()}/api/data/arms`, { cache: "no-store" });
    if (!res.ok) return { arms: [], data_types: [] };
    return (await res.json()) as ArmsResponse;
  } catch {
    return { arms: [], data_types: [] };
  }
}

export default async function DataExportPage() {
  const arms = await fetchArms();
  return (
    <div className="audit-console">
      <DataExportClient initial={arms} />
    </div>
  );
}
