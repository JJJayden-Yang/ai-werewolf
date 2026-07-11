# AI Wolf Frontend

Next App Router frontend for player replay and admin audit workflows.

## Setup

```bash
npm install
npm run dev
```

The API facade reads `NEXT_PUBLIC_API_BASE_URL`; when unset it falls back to
`NEXT_PUBLIC_AI_WOLF_API_BASE_URL`, then `http://localhost:8000`.

Sample replay cards can be supplied with:

```bash
NEXT_PUBLIC_AI_WOLF_SAMPLE_GAME_IDS=g_001,g_002
```

On WSL mounted drives such as `/mnt/g`, `npm install` may fail on package
symlinks. Prefer installing on a Linux filesystem, or use a no-bin-links npm
workflow if the repo must stay on the mounted drive.

## Backend Surface

Current real HTTP API:

- `GET /health`
- `GET /replay/{game_id}`
- `POST /games`
- `GET /games`
- `GET /games/{game_id}/status`
- `GET /games/{game_id}/events?since=N`

Facade methods for deep audit and strategy APIs intentionally throw
`PendingBackendApiError` until those HTTP endpoints exist.

## Routes

Player-facing:

- `/`
- `/home`
- `/play`
- `/play/[gameId]`
- `/replay`
- `/replay/[gameId]`
- `/player`

Pending player live-game routes:

- `/player/games`
- `/player/games/new`
- `/player/games/[gameId]/live`

Admin MVP-A:

- `/admin`
- `/admin/runs`
- `/admin/runs/[gameId]/timeline`
- `/admin/runs/[gameId]/events`
- `/admin/runs/[gameId]/raw`

Pending admin MVP-B:

- `/admin/runs/[gameId]/belief`
- `/admin/runs/[gameId]/decisions`
- `/admin/runs/[gameId]/context`
- `/admin/runs/[gameId]/errors`

Pending strategy:

- `/admin/strategy`
- `/admin/strategy/prompts`
- `/admin/strategy/belief-rules`
- `/admin/strategy/experiments`

## Data Boundaries

Player replay uses a server-side projection before data reaches the browser:

- `PlayerReplayEvent` contains only `key`, `index`, `kind`, `round`, `phase`,
  `actor`, `target`, `title`, `body`, and `tone`.
- `PlayerSeatView` contains only `playerId`, `role`, and `camp`.
- Player replay does not receive raw `GameEvent`, raw `ReplayPlayer`, payload,
  event ids, trace ids, prompt data, belief deltas, or audit attribution fields.
- `/replay/[gameId]` renders replay content only after a `game_over` event.

Admin audit may display full `ReplayData`, raw events, payloads, and visibility
because it is an engineering/debugging surface.

## Verification State

The current scaffold has not been typechecked in this environment because
frontend dependencies are intentionally not installed. After installing
dependencies, run:

```bash
npm run typecheck
```
