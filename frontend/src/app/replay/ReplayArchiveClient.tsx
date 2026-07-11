"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import type { ReplaySummary } from "@/lib/types/contracts";

type ReplayArchiveClientProps = {
  replays: ReplaySummary[];
};

type FilterState = {
  playerCount: number | "all";
  mode: string;
  tag: string;
};

const ALL = "all";
const PAGE_SIZE = 4;

export function ReplayArchiveClient({ replays }: ReplayArchiveClientProps) {
  const [filters, setFilters] = useState<FilterState>({
    playerCount: "all",
    mode: ALL,
    tag: ALL
  });
  const [page, setPage] = useState(1);
  const filterOptions = useMemo(() => buildFilterOptions(replays), [replays]);
  const filteredReplays = useMemo(
    () => replays.filter((replay) => matchesFilters(replay, filters)),
    [filters, replays]
  );
  const totalPages = Math.max(1, Math.ceil(filteredReplays.length / PAGE_SIZE));
  const clampedPage = Math.min(page, totalPages);
  const pageReplays = filteredReplays.slice(
    (clampedPage - 1) * PAGE_SIZE,
    clampedPage * PAGE_SIZE
  );

  useEffect(() => {
    setPage(1);
  }, [filters]);

  return (
    <section className="archive-list-panel">
      <div className="archive-filter-groups" aria-label="筛选项">
        <FilterSelect
          label="人数"
          onChange={(value) =>
            setFilters((current) => ({
              ...current,
              playerCount: value === ALL ? "all" : Number(value)
            }))
          }
          options={filterOptions.playerCounts.map((count) => ({
            label: `${count}人局`,
            value: String(count)
          }))}
          value={filters.playerCount === "all" ? ALL : String(filters.playerCount)}
        />

        {filterOptions.modes.length > 0 ? (
          <FilterSelect
            label="模式"
            onChange={(mode) => setFilters((current) => ({ ...current, mode }))}
            options={filterOptions.modes.map((mode) => ({ label: mode, value: mode }))}
            value={filters.mode}
          />
        ) : null}

        {filterOptions.tags.length > 0 ? (
          <FilterSelect
            label="关键事件"
            onChange={(tag) => setFilters((current) => ({ ...current, tag }))}
            options={filterOptions.tags.map((tag) => ({ label: tag, value: tag }))}
            value={filters.tag}
          />
        ) : null}
      </div>

      {filteredReplays.length === 0 ? (
        <div className="replay-card muted">暂无符合筛选条件的历史回放。</div>
      ) : (
        <div className="replay-scroll-panel">
          <div className="archive-list-head">
            <div>
              <span>选择回放</span>
              <strong>{filteredReplays.length} 局</strong>
            </div>
            {filteredReplays.length > PAGE_SIZE ? (
              <div className="replay-pagination" aria-label="回放翻页">
                <button
                  className="secondary-button"
                  disabled={clampedPage <= 1}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                  type="button"
                >
                  上一页
                </button>
                <span>
                  第 {clampedPage} / {totalPages} 页
                </span>
                <button
                  className="secondary-button"
                  disabled={clampedPage >= totalPages}
                  onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                  type="button"
                >
                  下一页
                </button>
              </div>
            ) : null}
          </div>
          <div className="replay-scroll-list" aria-label="回放列表">
            {pageReplays.map((replay) => (
              <ReplaySummaryCard key={replay.gameId} replay={replay} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function FilterSelect({
  label,
  onChange,
  options,
  value
}: {
  label: string;
  onChange: (value: string) => void;
  options: { label: string; value: string }[];
  value: string;
}) {
  return (
    <label className="archive-filter-select">
      <span>{label}</span>
      <select onChange={(event) => onChange(event.target.value)} value={value}>
        <option value={ALL}>全部</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function ReplaySummaryCard({ replay }: { replay: ReplaySummary }) {
  return (
    <Link
      className="replay-card"
      href={`/replay/${encodeURIComponent(replay.gameId)}`}
    >
      <div>
        <strong>{replay.gameId}</strong>
        <p>{formatMeta(replay)}</p>
        <small className="replay-card-time">开启时间：{formatCreatedAt(replay.createdAt)}</small>
      </div>
      <div className="replay-card-tags">
        {replay.rounds ? <span>{replay.rounds} 回合</span> : null}
        {replay.durationSec ? <span>{Math.round(replay.durationSec / 60)} 分钟</span> : null}
        {replay.tags.map((tag) => (
          <span key={tag}>{tag}</span>
        ))}
      </div>
    </Link>
  );
}

function buildFilterOptions(replays: ReplaySummary[]) {
  return {
    playerCounts: Array.from(
      new Set(replays.flatMap((replay) => (replay.playerCount ? [replay.playerCount] : [])))
    ).sort((a, b) => a - b),
    modes: Array.from(
      new Set(replays.flatMap((replay) => (replay.mode?.trim() ? [replay.mode.trim()] : [])))
    ).sort((a, b) => a.localeCompare(b, "zh-CN", { numeric: true })),
    tags: Array.from(new Set(replays.flatMap((replay) => replay.tags))).sort((a, b) =>
      a.localeCompare(b, "zh-CN", { numeric: true })
    )
  };
}

function matchesFilters(replay: ReplaySummary, filters: FilterState): boolean {
  if (filters.playerCount !== "all" && replay.playerCount !== filters.playerCount) return false;
  if (filters.mode !== ALL && normalize(replay.mode) !== normalize(filters.mode)) return false;
  if (filters.tag !== ALL && !replay.tags.includes(filters.tag)) return false;
  return true;
}

function formatMeta(replay: ReplaySummary): string {
  return [
    replay.playerCount ? `${replay.playerCount}人局` : null,
    replay.mode,
    replay.status === "completed" ? "已完成" : replay.status,
    replay.winner ? `胜方：${formatWinner(replay.winner)}` : null
  ]
    .filter(Boolean)
    .join(" · ");
}

function formatCreatedAt(createdAt?: string | null): string {
  if (!createdAt) return "未记录";
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return createdAt;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  }).format(date);
}

function formatWinner(winner: string): string {
  if (winner === "villagers") return "好人";
  if (winner === "werewolves") return "狼人";
  if (winner === "villager") return "好人";
  if (winner === "werewolf") return "狼人";
  return winner;
}

function normalize(value?: string | null): string {
  return (value ?? "").trim().toLowerCase();
}
