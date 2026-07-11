import { fetchJson } from "./client";

export type SoulTemplate = {
  id: string;
  name: string;
  source: "builtin" | "custom";
  summary: string;
};

export type ListSoulsResponse = {
  souls: SoulTemplate[];
};

export type CreateSoulRequest = {
  soul_id?: string;
  name: string;
  content: string;
};

export type SoulResponse = {
  soul: SoulTemplate;
};

export const soulApi = {
  async listSouls(): Promise<ListSoulsResponse> {
    return fetchJson<ListSoulsResponse>("/souls");
  },

  async createSoul(request: CreateSoulRequest): Promise<SoulResponse> {
    return fetchJson<SoulResponse>("/souls", {
      method: "POST",
      body: request
    });
  },

  async deleteSoul(soulId: string): Promise<{ status: string }> {
    return fetchJson<{ status: string }>(`/souls/${encodeURIComponent(soulId)}`, {
      method: "DELETE"
    });
  }
};
