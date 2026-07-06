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

- Faithful 1982 mechanics: port price tables, Li Yuen's extortion and
  pirate fleets, Elder Brother Wu's 10%/month loans, McHenry's repairs,
  storms, seizures, muggings, animated sea battles, original scoring.
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
