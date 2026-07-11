"use client";

import type { FormEvent } from "react";
import { useState } from "react";
import { useRouter } from "next/navigation";

export function AdminRunLookupForm() {
  const router = useRouter();
  const [gameId, setGameId] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = gameId.trim();
    if (!trimmed) return;
    router.push(`/admin/runs/${encodeURIComponent(trimmed)}/timeline`);
  }

  return (
    <form className="lookup-form" onSubmit={submit}>
      <label className="muted" htmlFor="admin-game-id">
        Game ID
      </label>
      <input
        className="text-input"
        id="admin-game-id"
        onChange={(event) => setGameId(event.target.value)}
        placeholder="g_20260528_001"
        value={gameId}
      />
      <button className="menu-button primary" type="submit">
        <span>Open Audit</span>
        <span>Enter</span>
      </button>
    </form>
  );
}
