import Link from "next/link";

import { PendingBackendPanel } from "@/components/ui/PendingBackendPanel";

export default function PlayerGamesPage() {
  return (
    <section className="screen">
      <PendingBackendPanel apiName="gameApi.listGames()" title="Player Games">
        Live game listing is pending backend support. Replay history remains the
        active player-facing workflow.
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
