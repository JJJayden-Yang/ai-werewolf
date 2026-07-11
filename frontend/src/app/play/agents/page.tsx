import { Suspense } from "react";

import { AgentLobbyClient } from "./AgentLobbyClient";

export default function AgentLobbyPage() {
  return (
    <Suspense fallback={<section className="notice-panel">正在进入角色大厅…</section>}>
      <AgentLobbyClient />
    </Suspense>
  );
}
