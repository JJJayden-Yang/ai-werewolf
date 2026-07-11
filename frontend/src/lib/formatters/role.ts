import type { Camp, PlayerStatus, Role } from "@/lib/types/contracts";

const ROLE_LABELS: Record<Role, string> = {
  werewolf: "狼人",
  seer: "预言家",
  witch: "女巫",
  hunter: "猎人",
  villager: "村民"
};

const CAMP_LABELS: Record<Camp, string> = {
  werewolf: "狼人阵营",
  villager: "好人阵营"
};

const STATUS_LABELS: Record<PlayerStatus, string> = {
  alive: "存活",
  dead: "出局"
};

export function formatRole(role?: Role | string | null): string {
  if (!role) return "未知身份";
  return ROLE_LABELS[role as Role] ?? role;
}

export function formatCamp(camp?: Camp | string | null): string {
  if (!camp) return "未知阵营";
  return CAMP_LABELS[camp as Camp] ?? camp;
}

export function formatPlayerStatus(status?: PlayerStatus | string | null): string {
  if (!status) return "未知状态";
  return STATUS_LABELS[status as PlayerStatus] ?? status;
}
