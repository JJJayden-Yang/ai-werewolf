import Link from "next/link";

export default function PlayerPage() {
  return (
    <section className="screen">
      <div className="list-panel" style={{ maxWidth: 760 }}>
        <p className="eyebrow">Player</p>
        <h1 className="page-title">Player Entry</h1>
        <div className="menu-stack">
          <Link className="menu-button primary" href="/replay">
            <span>Replay History</span>
            <span>Open</span>
          </Link>
          <Link className="menu-button disabled" href="/player/games">
            <span>Live Games</span>
            <span>Pending</span>
          </Link>
        </div>
      </div>
    </section>
  );
}
