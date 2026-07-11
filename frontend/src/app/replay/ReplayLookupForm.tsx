"use client";

import type { FormEvent } from "react";
import { useState } from "react";
import { useRouter } from "next/navigation";

export function ReplayLookupForm() {
  const router = useRouter();
  const [gameId, setGameId] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = gameId.trim();
    if (!trimmed) return;
    router.push(`/replay/${encodeURIComponent(trimmed)}`);
  }

  return (
    <form className="lookup-form" onSubmit={submit}>
      <label className="muted" htmlFor="game-id">
        对局编号
      </label>
      <input
        className="text-input"
        id="game-id"
        onChange={(event) => setGameId(event.target.value)}
        placeholder="demo-jiangnan-9p"
        value={gameId}
      />
      <button className="menu-button primary" type="submit">
        <span>打开回放</span>
        <span>进入</span>
      </button>
    </form>
  );
}
