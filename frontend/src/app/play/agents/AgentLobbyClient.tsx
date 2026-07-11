"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { gameApi, soulApi } from "@/lib/api";
import { formatApiError } from "@/lib/api/errors";
import type { CreateGameRequest } from "@/lib/api/gameApi";
import type { SoulTemplate } from "@/lib/api/soulApi";
import { formatRole } from "@/lib/formatters/role";
import type { Role } from "@/lib/types/contracts";

type RoomConfig = CreateGameRequest;
const ROLE_OPTIONS: Role[] = ["werewolf", "seer", "witch", "hunter", "villager"];

function numberParam(value: string | null, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseRoomConfig(params: URLSearchParams): RoomConfig {
  const playerCount = numberParam(params.get("player_count"), 9) === 6 ? 6 : 9;
  const armParam = params.get("arm");
  const arm = armParam === "v1" || armParam === "v2" ? armParam : "v0";
  const mode = params.get("mode") === "mock" ? "mock" : "llm";
  const flavor = params.get("model_flavor");
  const modelFlavor = (flavor === "CODE" || flavor === "DEEPSEEK") ? flavor : "PRO";
  return {
    player_count: playerCount,
    arm,
    seed: Math.max(0, numberParam(params.get("seed"), 0)),
    temperature: Math.min(1, Math.max(0, numberParam(params.get("temperature"), 0.8))),
    mode,
    model_flavor: modelFlavor
  };
}

function seatIds(count: 6 | 9): string[] {
  return Array.from({ length: count }, (_, index) => `P${index + 1}`);
}

function randomAssignments(
  seats: string[],
  souls: SoulTemplate[],
  excludedSeat?: string | null
): Record<string, string> {
  if (souls.length === 0) return {};
  return Object.fromEntries(
    seats.filter((seat) => seat !== excludedSeat).map((seat) => {
      const soul = souls[Math.floor(Math.random() * souls.length)];
      return [seat, soul.id];
    })
  );
}

export function AgentLobbyClient() {
  const router = useRouter();
  const params = useSearchParams();
  const roomConfig = useMemo(() => parseRoomConfig(new URLSearchParams(params.toString())), [params]);
  const seats = useMemo(() => seatIds(roomConfig.player_count), [roomConfig.player_count]);
  const [souls, setSouls] = useState<SoulTemplate[]>([]);
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [selectedSeat, setSelectedSeat] = useState("P1");
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [customName, setCustomName] = useState("");
  const [customId, setCustomId] = useState("");
  const [customContent, setCustomContent] = useState("");
  const [humanEnabled, setHumanEnabled] = useState(false);
  const [humanSeat, setHumanSeat] = useState<string | null>(null);
  const [humanRole, setHumanRole] = useState<Role | null>(null);
  const [roleModalSeat, setRoleModalSeat] = useState<string | null>(null);

  useEffect(() => {
    let ignore = false;
    setIsLoading(true);
    soulApi
      .listSouls()
      .then((response) => {
        if (!ignore) setSouls(response.souls);
      })
      .catch((caught) => {
        if (!ignore) setError(formatApiError(caught, "读取 soul 模板失败"));
      })
      .finally(() => {
        if (!ignore) setIsLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, []);

  const aiSeats = humanEnabled && humanSeat ? seats.filter((seat) => seat !== humanSeat) : seats;
  const filledCount = aiSeats.filter((seat) => assignments[seat]).length;
  const humanReady = !humanEnabled || Boolean(humanSeat && humanRole);
  const canStart = humanReady && filledCount === aiSeats.length && !isSubmitting;
  const selectedSoulId = assignments[selectedSeat];
  const selectedSoul = souls.find((soul) => soul.id === selectedSoulId);

  function assignSoul(soulId: string) {
    if (humanEnabled && selectedSeat === humanSeat) return;
    setAssignments((current) => ({ ...current, [selectedSeat]: soulId }));
  }

  function toggleHuman(enabled: boolean) {
    setHumanEnabled(enabled);
    if (!enabled) {
      setHumanSeat(null);
      setHumanRole(null);
      setRoleModalSeat(null);
      return;
    }
    const seat = humanSeat ?? selectedSeat;
    setHumanSeat(seat);
    setRoleModalSeat(seat);
    setAssignments((current) => {
      const next = { ...current };
      delete next[seat];
      return next;
    });
  }

  function selectSeat(seat: string) {
    if (humanEnabled && (!humanSeat || seat === humanSeat)) {
      setHumanSeat(seat);
      setSelectedSeat(seat);
      setRoleModalSeat(seat);
      setAssignments((current) => {
        const next = { ...current };
        delete next[seat];
        return next;
      });
      return;
    }
    setSelectedSeat(seat);
  }

  function chooseHumanRole(role: Role) {
    const seat = roleModalSeat ?? humanSeat ?? selectedSeat;
    setHumanEnabled(true);
    setHumanSeat(seat);
    setHumanRole(role);
    setSelectedSeat(seat);
    setAssignments((current) => {
      const next = { ...current };
      delete next[seat];
      return next;
    });
    setRoleModalSeat(null);
  }

  async function refreshSouls() {
    const response = await soulApi.listSouls();
    setSouls(response.souls);
  }

  async function createCustomSoul(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      await soulApi.createSoul({
        name: customName,
        content: customContent,
        soul_id: customId.trim() || undefined
      });
      setCustomName("");
      setCustomId("");
      setCustomContent("");
      await refreshSouls();
    } catch (caught) {
      setError(formatApiError(caught, "保存自定义 soul 失败"));
    }
  }

  async function deleteSoul(soulId: string) {
    setError(null);
    try {
      await soulApi.deleteSoul(soulId);
      setAssignments((current) => {
        const next = { ...current };
        for (const [seat, assignedSoul] of Object.entries(next)) {
          if (assignedSoul === soulId) delete next[seat];
        }
        return next;
      });
      await refreshSouls();
    } catch (caught) {
      setError(formatApiError(caught, "删除自定义 soul 失败"));
    }
  }

  async function startGame() {
    setError(null);
    setIsSubmitting(true);
    try {
      const response = await gameApi.createGame({
        ...roomConfig,
        seat_souls: assignments,
        human_seat: humanEnabled ? humanSeat ?? undefined : undefined,
        human_role: humanEnabled ? humanRole ?? undefined : undefined
      });
      if (humanEnabled && humanSeat) {
        router.push(
          `/player/games/${encodeURIComponent(response.game_id)}/live?player_id=${encodeURIComponent(humanSeat)}`
        );
      } else {
        router.push(`/play/${encodeURIComponent(response.game_id)}`);
      }
    } catch (caught) {
      setError(formatApiError(caught, "启动对局失败"));
      setIsSubmitting(false);
    }
  }

  return (
    <section className="screen play-screen agent-lobby-screen">
      <div className="play-shell agent-lobby-shell">
        <header className="play-header">
          <div>
            <p className="eyebrow">Agent Lobby</p>
            <h1 className="page-title">角色大厅</h1>
          </div>
          <div className="agent-header-actions">
            <Link className="secondary-button play-back" href="/play">
              返回设置
            </Link>
            <button className="play-button agent-start" disabled={!canStart} onClick={startGame} type="button">
              {isSubmitting ? "正在启动…" : "开始游戏"}
            </button>
          </div>
        </header>

        <div className="agent-room-summary">
          <span>{roomConfig.player_count} 人局</span>
          <span>{roomConfig.arm.toUpperCase()}</span>
          <span>{roomConfig.mode === "llm" ? `真实 LLM · ${roomConfig.model_flavor}` : "Mock"}</span>
          <span>Seed {roomConfig.seed}</span>
          <span>温度 {roomConfig.temperature.toFixed(1)}</span>
        </div>

        {roomConfig.mode === "mock" ? (
          <p className="agent-warning">Mock 对局不会调用 LLM，当前 soul 选择仅作预览，不影响 Mock Agent 行为。</p>
        ) : null}
        {error ? <p className="form-error">{error}</p> : null}

        <div className="agent-lobby-grid">
          <section className="setup-panel agent-seat-panel">
            <div className="agent-panel-head">
              <div>
                <span>座位模板</span>
                <strong>{filledCount}/{aiSeats.length} AI 已选择</strong>
              </div>
              <button
                className="secondary-button"
                disabled={souls.length === 0}
                onClick={() => setAssignments(randomAssignments(seats, souls, humanEnabled ? humanSeat : null))}
                type="button"
              >
                一键随机 soul
              </button>
            </div>

            <label className="human-placeholder">
              <input
                checked={humanEnabled}
                onChange={(event) => toggleHuman(event.target.checked)}
                type="checkbox"
              />
              <span>自己参与游戏</span>
              <em>{humanSeat && humanRole ? `${humanSeat} · ${formatRole(humanRole)}` : "点击座位选择身份"}</em>
            </label>

            <div className="seat-grid">
              {seats.map((seat) => {
                const isHumanSeat = humanEnabled && humanSeat === seat;
                const soul = souls.find((item) => item.id === assignments[seat]);
                return (
                  <button
                    className={[
                      "seat-tile",
                      selectedSeat === seat ? "active" : "",
                      isHumanSeat ? "human-seat" : ""
                    ].filter(Boolean).join(" ")}
                    key={seat}
                    onClick={() => selectSeat(seat)}
                    type="button"
                  >
                    <span>{seat}</span>
                    <strong>{isHumanSeat ? `你 · ${humanRole ? formatRole(humanRole) : "待选身份"}` : soul?.name ?? "待选择"}</strong>
                    <small>{isHumanSeat ? "真人玩家" : soul?.source === "custom" ? "自定义" : soul ? "内置" : "空座"}</small>
                  </button>
                );
              })}
            </div>
          </section>

          <aside className="setup-panel agent-soul-panel">
            <div className="agent-panel-head">
              <div>
                <span>模板库</span>
                <strong>{selectedSeat} · {humanEnabled && selectedSeat === humanSeat ? "真人座位" : selectedSoul?.name ?? "未选择"}</strong>
              </div>
            </div>

            {isLoading ? <p className="soul-empty">正在读取模板…</p> : null}
            {!isLoading && souls.length === 0 ? <p className="soul-empty">暂无可用 soul 模板。</p> : null}

            <div className="soul-list">
              {souls.map((soul) => (
                <article className={selectedSoulId === soul.id ? "soul-card active" : "soul-card"} key={soul.id}>
                  <button disabled={humanEnabled && selectedSeat === humanSeat} onClick={() => assignSoul(soul.id)} type="button">
                    <span className="soul-avatar">{soul.name.slice(0, 1)}</span>
                    <span className="soul-copy">
                      <strong>{soul.name}</strong>
                      <small>{soul.summary}</small>
                      <em>{soul.source === "custom" ? "自定义模板" : "内置模板"}</em>
                    </span>
                  </button>
                  {soul.source === "custom" ? (
                    <button className="soul-delete" onClick={() => deleteSoul(soul.id)} type="button">
                      删除
                    </button>
                  ) : null}
                </article>
              ))}
            </div>

            <form className="custom-soul-form" onSubmit={createCustomSoul}>
              <h2>新增自定义 Soul</h2>
              <input
                className="text-input"
                onChange={(event) => setCustomName(event.target.value)}
                placeholder="模板名称"
                value={customName}
              />
              <input
                className="text-input"
                onChange={(event) => setCustomId(event.target.value)}
                placeholder="可选 ID：letters_numbers"
                value={customId}
              />
              <textarea
                className="text-input soul-editor"
                onChange={(event) => setCustomContent(event.target.value)}
                placeholder="# Soul：稳健复盘型&#10;&#10;用 Markdown 写完整人格模板。角色策略、allowed_actions、信息边界永远优先。"
                value={customContent}
              />
              <button className="secondary-button" type="submit">
                保存为模板
              </button>
            </form>
          </aside>
        </div>
      </div>
      {roleModalSeat ? (
        <div className="role-modal-backdrop" role="presentation">
          <section className="role-modal" role="dialog" aria-label="选择真人角色" aria-modal="true">
            <div className="agent-panel-head">
              <div>
                <span>真人座位</span>
                <strong>{roleModalSeat} · 选择角色</strong>
              </div>
            </div>
            <div className="role-option-grid">
              {ROLE_OPTIONS.map((role) => (
                <button
                  className={humanRole === role && humanSeat === roleModalSeat ? "role-option active" : "role-option"}
                  key={role}
                  onClick={() => chooseHumanRole(role)}
                  type="button"
                >
                  <strong>{formatRole(role)}</strong>
                  <small>{role}</small>
                </button>
              ))}
            </div>
            <button className="secondary-button" onClick={() => setRoleModalSeat(null)} type="button">
              取消
            </button>
          </section>
        </div>
      ) : null}
    </section>
  );
}
