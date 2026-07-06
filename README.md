# Taipan!

A web port of **Taipan!**, Art Canfil's classic trading game (TRS-80 1979,
Apple II 1982), set in the China trade of the 1800s. Sail between seven
ports trading opium, silk, arms and general cargo; borrow from Elder
Brother Wu; pay off (or defy) the pirate lord Li Yuen; fight sea battles;
and retire a millionaire.

The game logic is a faithful Python port of the original Applesoft BASIC
listing (see [reference/taipan-original.bas](reference/taipan-original.bas)),
with message text from Jay Link's C port
([reference/taipan-c-port.c](reference/taipan-c-port.c)). The UI is a
retro green-phosphor CRT terminal in the browser.

## Run

```sh
uv run main.py
```

Then open <http://127.0.0.1:8000>.

For development with auto-reload:

```sh
uv run uvicorn main:app --reload
```

On Windows you can also just double-click `runme.bat`.

## Test

```sh
uv run pytest
```

Covers the original formulas (prices, scoring/ratings, money
formatting), replay determinism, and random-play completion. For a
heavier soak test:

```sh
uv run python scripts/smoke.py
```

Plays 200 random games to completion and checks engine invariants.

## Features

- **Classic mode**: faithful 1982 mechanics — port price tables, Li
  Yuen's extortion and pirate fleets, Elder Brother Wu's 10%/month
  loans, McHenry's repairs, storms, seizures, muggings, animated sea
  battles, original scoring.
- **Extended mode** (chosen at game start): tavern rumors of price
  spikes (~75% reliable), per-port opium markets (strict ports like
  Nagasaki pay a premium but seize more; lax Batavia is safe and
  cheap), prize ships captured after decisive victories, reputations —
  steady donors become friends of Li Yuen's fleet while repeat
  refusers get hunted by Captain Feng, and reliable borrowers earn a
  better rate from Wu — plus scripted history of the 1860s (the
  Convention of Peking, the Taiping rebellion, and more) moving
  prices. Pirate fleets are capped at plausible sizes (30 ordinary /
  50 for Li Yuen), taming the original's late-game 300-ship grinds.
  Distance matters: voyages take 1-3 months by real sea distance
  (Shanghai-Nagasaki is a short hop; Batavia is the far end of the
  world), and every month at sea compounds Wu's interest — long
  arbitrage runs now have a real cost. Classic keeps the original's
  one-month voyages.
- **More extended-mode ventures**: charter contracts (deliver X to Y
  by a deadline for a fat bonus), McHenry's dockyard refits (copper
  hull, faster rigging, long nines, a vanity figurehead), a rival firm
  (Jardine's, Dent & Co., or Russell & Co.) moving your markets,
  typhoon season (Jul-Sep, uglier storms with a warning before you
  sail), and bribable harbor masters when your opium is about to be
  seized.
- **Achievements**: a dozen honors (Ma Tsu, Feng's Bane, Never Owed a
  Copper...) announced at game end and recorded first-unlock in the
  hall of fame.
- **Captain's log**: a dated journal of your career's notable events,
  viewable at game end with one-click copy for sharing.
- **Ghost race**: your best run's net-worth curve overlays the
  end-of-game chart, and the market log shows live whether you're
  ahead of or behind your record pace.
- **Challenge links (async PvP)**: after any finished game, "Challenge
  a friend" copies a link. Whoever opens it sails the *same seas*
  (same seed, same mode) against your ghost — your net-worth pace
  shown live and on their chart — and lands on that challenge's own
  board. Retry as often as pride demands.

## Deploy

The app ships with a `Dockerfile` (single worker by design — live
game generators are per-process):

```sh
docker build -t taipan .
docker run -p 8000:8000 -v taipan-saves:/app/saves taipan
```

`HOST`/`PORT` are read from the environment. Persist `/app/saves` on a
volume: it holds sessions, hall-of-fame boards, achievements, and
challenges. Basic abuse protection is built in (per-IP rate limit on
new games, save-file TTL and count caps), but there is no
authentication — scores are honor-system firm names.

Works as-is on Fly.io, Railway, or Render: point the platform at the
Dockerfile, attach a small volume at `/app/saves`, done.
- **Daily challenge** (press D on the splash screen): everyone plays
  the same seed on the same day, classic rules, with its own
  leaderboard.
- **Market log**: a collapsible table remembering the last prices you
  saw in every port.
- **Voyage epilogue**: end-of-game statistics (battles, booty,
  donations, interest paid...) and a net-worth-over-time chart.
- Games survive refreshes and server restarts: every session is saved
  as (RNG seed + input log) in `saves/` and replayed on demand.
- Hall of fame: top scores persist across games (`saves/highscores.json`).
- Retro CRT presentation: VT323 terminal font, scanlines, WebAudio
  sound effects (press M to mute).
- Options (top-right corner): fast play (no delays/animations) and
  auto-repeating battle orders, so you don't press Fight every round --
  a nod to the original, which repeated your last orders after its
  3-second keyboard poll.
- ESC cancels any input: back out of buy/sell amounts, bank visits,
  cargo transfers, Wu negotiations, or the destination prompt without
  committing to anything. (Yes/No offers treat ESC as No; the one
  question whose No ends the game insists on an explicit answer.)

## Structure

- `taipan/engine.py` — the game engine. Runs as a generator that yields
  events (messages + state snapshot + prompt) and receives player input
  via `.send()`, mirroring the original's linear BASIC flow. Fully
  deterministic given its seed, which is what makes save/replay work.
- `main.py` — FastAPI server; one generator per browser session,
  event-sourced saves in `saves/`. Run single-worker (the default).
- `static/` — the web client (splash, port screen, sea battles).
- `reference/` — the original sources this port is based on.
