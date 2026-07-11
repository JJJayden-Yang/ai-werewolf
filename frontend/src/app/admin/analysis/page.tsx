const DEFAULT_GRAFANA_BASE_URL = "http://localhost:3000";

function grafanaUrl(path: string): string {
  const base = (
    process.env.NEXT_PUBLIC_GRAFANA_BASE_URL || DEFAULT_GRAFANA_BASE_URL
  ).replace(/\/$/, "");
  return `${base}${path}`;
}

export default function AdminAnalysisPage() {
  const gameAnalysisUrl = grafanaUrl(
    "/d/ai-wolf-run-batch/ai-wolf-run-batch-metrics?orgId=1&kiosk=tv"
  );
  const runningGamesUrl = grafanaUrl(
    "/d/ai-wolf-running-games/ai-wolf-running-games?orgId=1&kiosk=tv"
  );

  return (
    <section className="analysis-console">
      <div className="analysis-header">
        <div>
          <p className="eyebrow">Admin Analysis</p>
          <h1 className="page-title">Grafana</h1>
        </div>
        <div className="analysis-tabs" aria-label="Analysis modules">
          <a href="#game-analysis">Game Analysis</a>
          <a href="#running-games">Running Games</a>
        </div>
      </div>

      <section id="game-analysis" className="analysis-panel">
        <div className="analysis-panel-header">
          <h2>Game Analysis</h2>
          <a href={gameAnalysisUrl} target="_blank" rel="noreferrer">
            Open
          </a>
        </div>
        <iframe
          title="AI Wolf game analysis Grafana dashboard"
          src={gameAnalysisUrl}
          loading="lazy"
        />
      </section>

      <section id="running-games" className="analysis-panel">
        <div className="analysis-panel-header">
          <h2>Running Games</h2>
          <a href={runningGamesUrl} target="_blank" rel="noreferrer">
            Open
          </a>
        </div>
        <iframe
          title="AI Wolf running games Grafana dashboard"
          src={runningGamesUrl}
          loading="lazy"
        />
      </section>
    </section>
  );
}
