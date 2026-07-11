import type { GameEvent, ReplayData } from "@/lib/types/contracts";

import { fetchJson } from "./client";
import { PendingBackendApiError } from "./errors";
import { replayApi } from "./replayApi";

export const auditApi = {
  async getEvents(gameId: string): Promise<GameEvent[]> {
    const replay = await replayApi.getReplay(gameId);
    return replay.events;
  },

  async getRawReplay(gameId: string): Promise<ReplayData> {
    return fetchJson<ReplayData>(`/replay/${encodeURIComponent(gameId)}`);
  },

  async getDecisionTraces(_gameId: string): Promise<never> {
    throw new PendingBackendApiError("auditApi.getDecisionTraces");
  },

  async getContextSnapshots(_gameId: string): Promise<never> {
    throw new PendingBackendApiError("auditApi.getContextSnapshots");
  },

  async getBeliefUpdates(_gameId: string): Promise<never> {
    throw new PendingBackendApiError("auditApi.getBeliefUpdates");
  },

  async getBeliefCurves(_gameId: string): Promise<never> {
    throw new PendingBackendApiError("auditApi.getBeliefCurves");
  },

  async getBatchReport(_runId: string): Promise<never> {
    throw new PendingBackendApiError("auditApi.getBatchReport");
  }
};
