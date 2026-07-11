import { projectReplay } from "@/lib/replay";
import { demoReplaySummaries, getDemoReplayData } from "@/lib/prototype/demoReplay";
import type { ReplayData, ReplaySummary, ReplayViewModel } from "@/lib/types/contracts";

import { fetchJson } from "./client";

export const replayApi = {
  async listReplays(): Promise<ReplaySummary[]> {
    const configuredGameIds =
      process.env.NEXT_PUBLIC_AI_WOLF_SAMPLE_GAME_IDS?.split(",")
        .map((gameId) => gameId.trim())
        .filter(Boolean) ?? [];

    const configured = configuredGameIds.map((gameId) => ({
      gameId,
      tags: []
    }));

    try {
      const persisted = await fetchJson<{ replays: ReplaySummary[] }>("/replays");
      return [...demoReplaySummaries, ...configured, ...persisted.replays];
    } catch {
      return [...demoReplaySummaries, ...configured];
    }
  },

  async getReplay(gameId: string): Promise<ReplayViewModel> {
    const demo = getDemoReplayData(gameId);
    if (demo) return projectReplay(demo);

    const data = await fetchJson<ReplayData>(
      `/replay/${encodeURIComponent(gameId)}`
    );
    return projectReplay(data);
  }
};
