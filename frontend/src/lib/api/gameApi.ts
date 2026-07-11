import type { GameEvent, Phase, PlayerStatus, Role } from "@/lib/types/contracts";

import { fetchJson } from "./client";

export type CreateGameRequest = {
  player_count: 6 | 9;
  arm: "v0" | "v1" | "v2";
  seed: number;
  temperature: number;
  mode: "mock" | "llm";
  model_flavor: "PRO" | "CODE" | "DEEPSEEK";
  max_rounds?: number;
  seat_souls?: Record<string, string>;
  human_seat?: string;
  human_role?: "werewolf" | "seer" | "witch" | "hunter" | "villager";
};

export type CreateGameResponse = {
  game_id: string;
  status: "running";
};

export type GameSummary = {
  game_id: string;
  status: LiveGameStatus;
  player_count: 6 | 9;
  arm: "v0" | "v1" | "v2";
  created_at: string;
  current_round: number;
  current_phase: Phase;
};

export type ListGamesResponse = {
  games: GameSummary[];
};

export type LiveGameStatus = "pending" | "running" | "finished" | "error";

export type GameStatusResponse = {
  game_id: string;
  status: LiveGameStatus;
  current_round: number;
  current_phase: Phase;
  current_actor: string | null;
  winner: string | null;
  error: string | null;
  // 上帝视角真实身份 {player_id: role}；普通视角不使用。
  role_map: Record<string, string> | null;
};

export type GameEventsResponse = {
  events: GameEvent[];
  next_cursor: number;
  status: "running" | "finished";
};

export type HumanPendingResponse =
  | { pending: false }
  | { pending: true; context: Record<string, unknown> };

export type PlayerPrivateEvent = {
  event_type: string;
  round?: number | null;
  target?: string | null;
  result?: string | null;
  teammates?: string[] | null;
  visibility?: string | null;
};

export type PlayerVisiblePlayer = {
  player_id: string;
  status: PlayerStatus;
  public_claim?: string | null;
};

export type PlayerEventsResponse = {
  game_id: string;
  player_id: string;
  role: Role | null;
  events: GameEvent[];
  private_events: PlayerPrivateEvent[];
  visible_players: PlayerVisiblePlayer[];
  next_cursor: number;
};

export type SubmitHumanActionRequest = {
  player_id: string;
  action_type: string;
  target?: string;
  public_message?: string;
  role_claim?: "werewolf" | "seer" | "witch" | "hunter" | "villager";
};

export const gameApi = {
  async createGame(request: CreateGameRequest): Promise<CreateGameResponse> {
    return fetchJson<CreateGameResponse>("/games", {
      method: "POST",
      body: request
    });
  },

  async runGame(request: CreateGameRequest): Promise<CreateGameResponse> {
    return this.createGame(request);
  },

  async getRunStatus(gameId: string): Promise<GameStatusResponse> {
    return this.getStatus(gameId);
  },

  async listGames(): Promise<ListGamesResponse> {
    return fetchJson<ListGamesResponse>("/games");
  },

  async getStatus(gameId: string): Promise<GameStatusResponse> {
    return fetchJson<GameStatusResponse>(`/games/${encodeURIComponent(gameId)}/status`);
  },

  async getEvents(gameId: string, since: number): Promise<GameEventsResponse> {
    const encodedGameId = encodeURIComponent(gameId);
    return fetchJson<GameEventsResponse>(
      `/games/${encodedGameId}/events?since=${encodeURIComponent(String(since))}`
    );
  },

  async getPending(gameId: string, playerId: string): Promise<HumanPendingResponse> {
    return fetchJson<HumanPendingResponse>(
      `/games/${encodeURIComponent(gameId)}/pending?player_id=${encodeURIComponent(playerId)}`
    );
  },

  async getPlayerEvents(gameId: string, playerId: string, since: number): Promise<PlayerEventsResponse> {
    return fetchJson<PlayerEventsResponse>(
      `/games/${encodeURIComponent(gameId)}/player-events?player_id=${encodeURIComponent(playerId)}&since=${encodeURIComponent(String(since))}`
    );
  },

  async submitAction(gameId: string, request: SubmitHumanActionRequest): Promise<{ accepted: boolean }> {
    return fetchJson<{ accepted: boolean }>(`/games/${encodeURIComponent(gameId)}/action`, {
      method: "POST",
      body: request
    });
  }
};
