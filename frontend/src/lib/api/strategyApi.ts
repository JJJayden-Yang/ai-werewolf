import { PendingBackendApiError } from "./errors";

export const strategyApi = {
  async listPromptVersions(): Promise<never> {
    throw new PendingBackendApiError("strategyApi.listPromptVersions");
  },

  async listBeliefRules(): Promise<never> {
    throw new PendingBackendApiError("strategyApi.listBeliefRules");
  },

  async createStrategyDraft(): Promise<never> {
    throw new PendingBackendApiError("strategyApi.createStrategyDraft");
  }
};
