import { formatRole } from "@/lib/formatters";
import type { Camp, EventType, GameEvent, JsonValue, Phase, Role, ReplayPlayer } from "@/lib/types/contracts";

const PLAYER_REPLAY_EVENT_TYPES = new Set<EventType>([
  "phase_started",
  "day_announcement",
  "speech",
  "vote_cast",
  "tie_detected",
  "no_exile_due_to_second_tie",
  "exile",
  "last_words",
  "hunter_shot",
  "death_confirmed",
  "game_over"
]);

const GOD_VIEW_NIGHT_EVENT_TYPES = new Set<EventType>([
  "wolf_nomination",
  "night_kill_announced",
  "seer_check_result",
  "witch_save",
  "witch_poison"
]);

export interface PlayerReplayEvent {
  key: string;
  round: number;
  phase: Phase;
  actor?: string | null;
  target?: string | null;
  eventType: EventType;
  title: string;
  body: string;
  tone: "speech" | "vote" | "system" | "death" | "result";
  createdAt?: string | null;
}

export interface PlayerSeatView {
  playerId: string;
  role?: Role | null;
  camp?: Camp | null;
}

export interface PlayerSeatState extends PlayerSeatView {
  isAlive: boolean;
  publicClaim?: string | null;
  voteTarget?: string | null;
}

export function getPlayerReplayEvents(
  events: GameEvent[],
  options: { includeNightActions?: boolean } = {}
): PlayerReplayEvent[] {
  return events
    .filter((event) => {
      if (PLAYER_REPLAY_EVENT_TYPES.has(event.event_type)) {
        return event.visibility === "public";
      }
      // 上帝视角额外放出夜间真实动作；普通视角只看阶段流程提示。
      if (!options.includeNightActions || !GOD_VIEW_NIGHT_EVENT_TYPES.has(event.event_type)) {
        return false;
      }
      if (event.event_type === "wolf_nomination") {
        return Boolean(event.actor && event.target);
      }
      return Boolean(event.target);
    })
    .map((event, index) => ({
      key: `${index}:${event.round}:${event.phase}:${event.event_type}`,
      round: event.round,
      phase: event.phase,
      actor: event.actor,
      target: event.target,
      eventType: event.event_type,
      title: getPlayerEventTitle(event),
      body: getPlayerEventBody(event),
      tone: getPlayerEventTone(event),
      createdAt: event.created_at
    }));
}

export function getReplayWinner(events: GameEvent[]): string | null {
  const gameOver = [...events]
    .reverse()
    .find((event) => event.event_type === "game_over");
  return stringPayload(gameOver, "winner");
}

export function getSeatStates(
  players: PlayerSeatView[],
  visibleEvents: PlayerReplayEvent[]
): PlayerSeatState[] {
  const dead = new Set<string>();
  const claims = new Map<string, string | null>();
  const votes = new Map<string, string | null>();

  for (const event of visibleEvents) {
    if (event.eventType === "death_confirmed" && event.target) {
      dead.add(event.target);
    }
    if ((event.eventType === "speech" || event.eventType === "last_words") && event.actor) {
      const claim = extractRoleClaim(event.body);
      if (claim) claims.set(event.actor, claim);
    }
    if (event.eventType === "vote_cast" && event.actor) {
      votes.set(event.actor, event.target ?? null);
    }
  }

  return players.map((player) => ({
    ...player,
    isAlive: !dead.has(player.playerId),
    publicClaim: claims.get(player.playerId),
    voteTarget: votes.get(player.playerId)
  }));
}

export function getPlayerSeatViews(players: ReplayPlayer[]): PlayerSeatView[] {
  return players.map((player) => ({
    playerId: player.player_id,
    role: player.role,
    camp: player.camp
  }));
}

export function getLivePlayerSeatViews(
  events: GameEvent[],
  fallbackPlayerCount: 6 | 9 = 9,
  roleMap?: Record<string, string> | null
): PlayerSeatView[] {
  const roleAssigned = events.find((event) => event.event_type === "role_assigned");
  const playerCount = numberPayload(roleAssigned, "player_count") ?? fallbackPlayerCount;
  return Array.from({ length: playerCount }, (_, index) => {
    const playerId = `P${index + 1}`;
    const role = (roleMap?.[playerId] as Role | undefined) ?? null;
    return {
      playerId,
      role,
      // 阵营由真实身份推导：狼人 → werewolf，其余 → villager。无身份时留空。
      camp: role ? (role === "werewolf" ? "werewolf" : "villager") : null
    };
  });
}

function getPlayerEventTitle(event: GameEvent): string {
  switch (event.event_type) {
    case "phase_started":
      return getPhaseStartedTitle(event.phase);
    case "wolf_nomination":
      return "狼人提名";
    case "night_kill_announced":
      return "狼队刀口";
    case "seer_check_result":
      return "预言家查验";
    case "witch_save":
      return "女巫救人";
    case "witch_poison":
      return "女巫毒人";
    case "speech":
      return `${formatPlayerId(event.actor)}发言`;
    case "last_words":
      return `${formatPlayerId(event.actor)}遗言`;
    case "vote_cast":
      return "投票记录";
    case "death_confirmed":
      return "死亡确认";
    case "day_announcement":
      return "天亮公告";
    case "tie_detected":
      return "出现平票";
    case "no_exile_due_to_second_tie":
      return "无人出局";
    case "exile":
      return "放逐结果";
    case "hunter_shot":
      return "猎人开枪";
    case "game_over":
      return "对局结束";
    default:
      return "事件";
  }
}

function getPlayerEventBody(event: GameEvent): string {
  switch (event.event_type) {
    case "phase_started":
      return getPhaseStartedBody(event.phase);
    case "wolf_nomination":
      return event.actor && event.target
        ? `${formatPlayerId(event.actor)}提名刀${formatPlayerId(event.target)}。`
        : "狼人提名中。";
    case "night_kill_announced":
      return event.target
        ? `狼队最终选择刀${formatPlayerId(event.target)}。`
        : "狼队未达成刀口。";
    case "seer_check_result":
      return event.target
        ? `预言家查验了${formatPlayerId(event.target)}，结果为${formatSeerResult(event.payload.result)}。`
        : `预言家完成查验，结果为${formatSeerResult(event.payload.result)}。`;
    case "witch_save":
      return event.target
        ? `女巫使用解药救了${formatPlayerId(event.target)}。`
        : "女巫使用了解药。";
    case "witch_poison":
      return event.target
        ? `女巫使用毒药毒了${formatPlayerId(event.target)}。`
        : "女巫使用了毒药。";
    case "speech":
    case "last_words":
      return withClaim(
        stringPayload(event, "public_message") ?? "",
        stringPayload(event, "role_claim")
      );
    case "vote_cast":
      return event.actor && event.target
        ? `${formatPlayerId(event.actor)}投给${formatPlayerId(event.target)}。`
        : "投票已记录。";
    case "death_confirmed": {
      // 观战展示不暴露死因（死因本就不进 Agent；观众也只看谁出局即可）。
      return event.target ? `${formatPlayerId(event.target)}出局。` : "死亡已确认。";
    }
    case "day_announcement":
      return formatDeaths(event.payload.deaths);
    case "tie_detected":
      return `平票候选：${formatStringList(event.payload.tie_candidates)}。`;
    case "no_exile_due_to_second_tie":
      return `二次投票仍平票：${formatStringList(event.payload.tie_candidates)}。`;
    case "exile":
      return event.target ? `${formatPlayerId(event.target)}被投票出局。` : "放逐已结算。";
    case "hunter_shot":
      if (event.actor && event.target) return `${formatPlayerId(event.actor)}发动技能，开枪带走${formatPlayerId(event.target)}。`;
      if (event.actor) return `${formatPlayerId(event.actor)}放弃开枪。`;
      return "猎人开枪阶段已结算。";
    case "game_over": {
      const winner = stringPayload(event, "winner");
      const reason = stringPayload(event, "reason");
      return [winner ? `${formatWinner(winner)}胜利。` : "未产生胜方。", reason].filter(Boolean).join(" ");
    }
    default:
      return "";
  }
}

function getPlayerEventTone(event: GameEvent): PlayerReplayEvent["tone"] {
  switch (event.event_type) {
    case "phase_started":
      return event.phase === "DAY_ANNOUNCEMENT" || event.phase === "HUNTER_SHOOT"
        ? "death"
        : "system";
    case "wolf_nomination":
    case "night_kill_announced":
    case "witch_poison":
      return "death";
    case "seer_check_result":
    case "witch_save":
      return "system";
    case "speech":
    case "last_words":
      return "speech";
    case "vote_cast":
    case "tie_detected":
    case "no_exile_due_to_second_tie":
      return "vote";
    case "death_confirmed":
    case "exile":
    case "hunter_shot":
      return "death";
    case "game_over":
      return "result";
    default:
      return "system";
  }
}

function getPhaseStartedTitle(phase: Phase): string {
  switch (phase) {
    case "NIGHT_WEREWOLF":
      return "狼人阶段";
    case "NIGHT_SEER":
      return "预言家阶段";
    case "NIGHT_WITCH":
      return "女巫阶段";
    case "HUNTER_SHOOT":
      return "猎人阶段";
    case "DAY_ANNOUNCEMENT":
      return "死亡公告";
    case "WIN_CHECK":
      return "胜负判断";
    case "GAME_OVER":
      return "对局结束";
    default:
      return "阶段开始";
  }
}

function getPhaseStartedBody(phase: Phase): string {
  switch (phase) {
    case "NIGHT_WEREWOLF":
      return "狼人请睁眼，狼人请选择今晚要杀害的玩家。";
    case "NIGHT_SEER":
      return "预言家开始行动。";
    case "NIGHT_WITCH":
      return "女巫开始行动。";
    case "HUNTER_SHOOT":
      return "猎人可以选择是否开枪。";
    case "DAY_ANNOUNCEMENT":
      return "昨夜结束，开始公布夜间结果。";
    case "WIN_CHECK":
      return "正在判断胜负。";
    case "GAME_OVER":
      return "对局已经结束。";
    default:
      return "新阶段开始。";
  }
}

function formatSeerResult(value: JsonValue | undefined): string {
  if (value === "werewolf" || value === "werewolves") return "狼人";
  if (value === "villager" || value === "villagers") return "好人";
  return "未知";
}

function formatDeaths(value: JsonValue | undefined): string {
  if (!Array.isArray(value) || value.length === 0) return "昨晚是平安夜。";
  // 只报谁出局，不报死因（死因不暴露给观众，也从不进 Agent）。
  const deaths = value
    .map((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return null;
      const death = item as Record<string, JsonValue>;
      const playerId = death.player_id;
      if (typeof playerId !== "string") return null;
      return formatPlayerId(playerId);
    })
    .filter(Boolean);
  return deaths.length > 0 ? `${deaths.join("、")}出局。` : "昨晚无人出局。";
}

function formatStringList(value: JsonValue | undefined): string {
  if (!Array.isArray(value)) return "candidates";
  const items = value.filter((item): item is string => typeof item === "string");
  return items.length > 0 ? items.map(formatPlayerId).join("、") : "候选人";
}

function stringPayload(event: GameEvent | undefined, key: string): string | null {
  const value = event?.payload[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberPayload(event: GameEvent | undefined, key: string): number | null {
  const value = event?.payload[key];
  return typeof value === "number" ? value : null;
}

function withClaim(message: string, claim: string | null): string {
  if (!claim) return message;
  const claimText = `公开声称：${formatRole(claim)}`;
  return message ? `${message} ${claimText}` : claimText;
}

function extractRoleClaim(message: string): string | null {
  const match = message.match(/公开声称：([^。.\s]+)/);
  return match?.[1] ?? null;
}

function formatPlayerId(playerId?: string | null): string {
  if (!playerId) return "玩家";
  const numeric = playerId.match(/^P(\d+)$/i)?.[1];
  if (!numeric) return playerId;
  const labels: Record<string, string> = {
    "1": "一号",
    "2": "二号",
    "3": "三号",
    "4": "四号",
    "5": "五号",
    "6": "六号",
    "7": "七号",
    "8": "八号",
    "9": "九号"
  };
  return labels[numeric] ?? `${numeric}号`;
}

function formatWinner(winner: string): string {
  if (winner === "villagers") return "好人阵营";
  if (winner === "werewolves") return "狼人阵营";
  return winner;
}
