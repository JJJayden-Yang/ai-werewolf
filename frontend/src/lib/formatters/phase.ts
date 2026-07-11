import type { Phase } from "@/lib/types/contracts";

const PHASE_LABELS: Record<Phase, string> = {
  INIT: "初始化",
  ROLE_ASSIGNMENT: "身份分配",
  NIGHT_WEREWOLF: "夜晚狼人",
  NIGHT_SEER: "夜晚预言家",
  NIGHT_WITCH: "夜晚女巫",
  DAY_ANNOUNCEMENT: "白天公布",
  HUNTER_SHOOT: "猎人开枪",
  DAY_DISCUSSION: "白天发言",
  DAY_VOTE: "白天投票",
  DAY_TIE_DISCUSSION: "平票发言",
  DAY_TIE_REVOTE: "平票重投",
  EXILE_RESOLUTION: "放逐结算",
  NO_EXILE_RESOLUTION: "无人出局",
  EXILE_LAST_WORDS: "遗言",
  WIN_CHECK: "胜负判断",
  GAME_OVER: "对局结束"
};

export function formatPhase(phase?: Phase | string | null): string {
  if (!phase) return "未知阶段";
  return PHASE_LABELS[phase as Phase] ?? phase;
}
