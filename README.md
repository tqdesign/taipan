# Taipan!

**Play it now: <https://playtaipan.com>**

A web port of **Taipan!**, Art Canfil's classic trading game (TRS-80
1979, Apple II 1982), set in the China trade of the 1860s. Sail between
seven ports trading opium, silk, arms and general cargo; borrow from
Elder Brother Wu; pay off (or defy) the pirate lord Li Yuen; fight sea
battles; and retire a millionaire — fast, because the score formula
punishes slow fortunes.

This port was built almost entirely in conversation with an AI (Claude,
by Anthropic): it tracked down the original Applesoft BASIC listing,
ported the engine line-by-line to Python, built the terminal UI, wrote
the tests, and deployed it — while the owner played, pointed at things,
and decided what the game should become. The full story is in the
in-game **[about]** dialog.

The game logic is a faithful port of the original BASIC source
([reference/taipan-original.bas](reference/taipan-original.bas)), with
message text from Jay Link's C port
([reference/taipan-c-port.c](reference/taipan-c-port.c)). The UI is a
green-phosphor CRT terminal in the browser: VT323 font, scanlines,
WebAudio sound (no assets), and the lorcha battle art of the original.

## The rule: Classic is sacred, Extended is the sandbox

- **Classic mode** is the exact 1982 game — price formulas, Li Yuen's
  extortion math, Wu's 10%/month compounding interest, combat odds,
  booty, and scoring all match the BASIC listing. Quirks included: you
  can still buy more cargo than the ship holds and sail nowhere until
  you fix the Overload.
- **Extended mode** (chosen at game start) is where improvements live:
  - **Economy**: tavern rumors of price spikes (~75% reliable);
    per-port opium markets (strict Nagasaki pays a premium but seizes
    1-in-8; lax Batavia is safe and cheap); bribable harbor masters;
    charter contracts (deliver X to port Y by a deadline for ~1.4x
    market); a rival firm — Jardine's, Dent & Co., or Russell & Co. —
    slumping and spiking your markets.
  - **The sea**: voyages take 1–3 months by real sailing distance, and
    every month at sea compounds Wu's interest; typhoon season
    (Jul–Sep) makes storms uglier, with a warning before you sail;
    pirate fleets are capped at plausible sizes (30 ordinary / 50 Li
    Yuen), taming the original's late-game 300-ship grinds; decisive
    victories may capture a prize lorcha.
  - **People who remember**: three donations make you a friend of Li
    Yuen's fleet (halved demands, captains may spare you); three
    refusals and Captain Feng hunts you with bigger fleets; pay Wu off
    in full twice and your rate drops to 8%.
  - **History**: a scripted 1860s timeline — the Convention of Peking,
    the Taiping rebellion, the fall of Nanking — moves port prices for
    a season.
  - **The dockyard**: one-time refits from McHenry — copper hull
    (better escapes), new rigging (shorter long voyages), long nines
    (heavier broadsides), and a gilded figurehead (vanity).
  - **Late-game headwinds** (added after a 94-year, 231-billion run
    proved the endgame was a riskless grind): **banks can fail** — a
    warning circulates a season ahead, then deposits above an insured
    500,000 take a 25–40% loss, so there is no riskless place to park
    a fortune; **careers end** — the partners force retirement after
    25 years (Jan 1885), so the benchmark is your best quarter-century,
    not your patience; and **price drift is capped** at 3× the 1860
    base values, keeping multi-decade arbitrage spreads sane.

## Around both modes

- **Daily challenge** (press D on the title screen): every captain
  sails the same salted seed, classic rules, on a per-day leaderboard.
- **Challenge links (async PvP)**: after any finished game, *Challenge
  a friend* copies a link. Whoever opens it plays the same seas against
  your ghost — your net-worth pace shown live and on their end-of-game
  chart — and lands on that challenge's own board, with retries
  labelled (`try #14`).
- **Ghost race**: your personal-best run's curve overlays the chart,
  with a live ahead/behind pace line in the market log.
- **Achievements**: a dozen honors (Ma Tsu, Feng's Bane, Never Owed a
  Copper...) announced in the epilogue, first unlocks recorded in the
  hall of fame.
- **Captain's log**: a dated journal of your career, with one-click
  copy for sharing.
- **Voyage epilogue**: full statistics and a net-worth-over-time chart.
- **Market log**: a collapsible table remembering the last prices seen
  in every port.
- **Quality of life**: How to Play guide; Esc cancels any input (the
  one question whose "No" ends the game insists on a real answer);
  25/50/75%/Max/All amount buttons; loss events (seizures, muggings,
  theft) require an explicit OK so fast play can't skip them; options
  for fast play and auto-repeating battle orders; restart or quit the
  current voyage from options; games survive refreshes and server
  restarts (every session is event-sourced as seed + input log and
  replayed on demand).

## Run locally

```sh
uv run main.py
```

Then open <http://127.0.0.1:8000>. Dev server with auto-reload:
`uv run uvicorn main:app --reload`. On Windows, `runme.bat` starts the
server and opens the browser.

## Test

```sh
uv run pytest
```

~70 tests: original formulas (prices, scoring, ratings), replay
determinism, random-play completion, extended-mode rules, cancel
flows, and the server layer (challenges, rate limits, cache headers,
save restore). Heavier soak test:
`uv run python scripts/smoke.py` (200 random games to completion).

## Deploy

Ships with a `Dockerfile` (single worker by design — live game
generators are per-process):

```sh
docker build -t taipan .
docker run -p 8000:8000 -v taipan-saves:/app/saves taipan
```

- `HOST`/`PORT` come from the environment.
- Persist `/app/saves` on a volume: sessions, boards, achievements,
  challenges, and the daily-seed salt live there.
- The daily seed is salted server-side (`DAILY_SALT` env var, or a
  random salt generated once into `saves/daily_salt.txt`) so it can't
  be derived from this public source and rehearsed offline.
- Abuse protection is built in: per-IP, per-endpoint rate limits;
  input length caps; replay-log caps; save/challenge file TTL and
  count caps; profanity masking on firm names; explicit Cache-Control
  headers so CDN edges never serve stale code across a deploy.
- No authentication — scores are honor-system firm names.

The production instance runs on Fly.io behind Cloudflare
(`fly.toml` included; a GitHub push to `main` auto-deploys). The
build stamps a version shown in the game's top-right corner
(`vMMDDYY.HHMM`, UTC).

## Structure

- `taipan/engine.py` — the game engine: a Python generator that yields
  events (messages + state snapshot + prompt) and receives player
  input via `.send()`, mirroring the original's linear BASIC flow.
  Fully deterministic given its seed, which powers saves, replays,
  daily challenges, and challenge links alike.
- `main.py` — FastAPI server: one generator per browser session,
  event-sourced saves in `saves/`, boards, challenges, rate limiting.
- `static/` — the web client (splash, port screen, sea battles,
  dialogs), vanilla JS.
- `reference/` — the original sources this port is checked against.
- `tests/` — engine and server test suites.

## Credits

Original game by **Art Canfil**. Reference C port by Jay Link. VT323
font by Peter Hull (OFL, license alongside the font). Built with
Claude (Anthropic).
