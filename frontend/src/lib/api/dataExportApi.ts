import { getApiBaseUrl } from "./client";

export type ArmCount = { arm: string; games: number };
export type ArmsResponse = { arms: ArmCount[]; data_types: string[] };

export async function fetchArms(): Promise<ArmsResponse> {
  const res = await fetch(`${getApiBaseUrl()}/api/data/arms`, { cache: "no-store" });
  if (!res.ok) return { arms: [], data_types: [] };
  return (await res.json()) as ArmsResponse;
}

export function buildDownloadUrl(arm: string, types: string[], limit?: number): string {
  const params = new URLSearchParams({ arm, types: types.join(",") });
  if (limit && limit > 0) params.set("limit", String(limit));
  return `${getApiBaseUrl()}/api/data/download?${params.toString()}`;
}
