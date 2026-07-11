import Link from "next/link";

import { PendingBackendPanel } from "@/components/ui/PendingBackendPanel";

export default function NewPlayerGamePage() {
  return (
    <section className="screen">
      <PendingBackendPanel apiName="gameApi.createGame()" title="Start Game">
        Game creation is pending backend support. This page intentionally does
        not mock create/run/status behavior.
        <div style={{ marginTop: 18 }}>
          <Link className="menu-button primary" href="/replay">
            <span>Replay History</span>
            <span>Open</span>
          </Link>
        </div>
      </PendingBackendPanel>
    </section>
  );
}
