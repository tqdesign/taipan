# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A web port of Art Canfil's 1982 Apple II game **Taipan!** — Python game engine + FastAPI server + vanilla-JS retro terminal frontend. Uses `uv` for Python environment/dependency management.

## Commands

- Run the server: `uv run main.py` (serves http://127.0.0.1:8000)
- Dev server with reload: `uv run uvicorn main:app --reload`
- Tests: `uv run pytest` — formulas, scoring, replay determinism, random-play completion. Run after any engine change.
- Heavier soak test: `uv run python scripts/smoke.py` — 200 random games to completion with invariant checks.
- Add a dependency: `uv add <package>`

## Architecture

The core design decision: **the game engine is a Python generator**, not a state machine. `Game.run()` in `taipan/engine.py` executes the game's linear flow top-to-bottom (mirroring the original line-numbered BASIC), yielding an event dict whenever player input is needed and receiving the answer via `.send(value)`. Sub-flows (trading, sea battles, Elder Brother Wu, etc.) are generator methods composed with `yield from`, whose return values are the player's parsed answers.

Each yielded event contains:
- `messages` — text lines and battle FX (`{"fx": "blast"|"sink"|"appear"|"clear"|"incoming", "slot": n}`) accumulated since the last input
- `prompt` — what input is expected: `choice` (options with hotkeys), `number` (supports `A` = All, like the original), `text`, `pause` (auto-continue with timeout), `end`
- `state` — full snapshot (cash, hold, warehouse, prices, date, ship status...)
- `battle` — sea-battle snapshot or null; the frontend switches screens based on this

`main.py` holds one generator per browser session in an in-memory dict (`/api/new` creates, `/api/step` sends input). **Persistence is event sourcing**: the engine is deterministic given its seed, so each session is saved to `saves/` as `(seed, input log)` and an unknown session id is restored by replaying the log into a fresh generator. Consequences: never make the engine consume randomness that isn't derived from `self.rng`, and don't reorder RNG calls in existing flows — both would corrupt every in-flight save. Run the server single-worker (live generators are per-process). High scores persist in `saves/highscores.json`, recorded server-side when an `end` event is first produced.

`static/app.js` renders events sequentially: it animates message/FX queues, then shows the prompt; the lorcha ship art and sink animation frames are copied from the C port. The client stores its session id in `localStorage` and resumes via `GET /api/state/{id}` after a refresh. Sound is synthesized with WebAudio (no assets); the font is self-hosted VT323 (OFL license alongside it in `static/fonts/`).

Player options (fast play, auto-repeat battle orders, sound) are **client-side only** (`taipan_opts` in localStorage) — the engine's pause timeouts are advisory and the battle-orders prompt is detected by its option keys being exactly `f,r,t`, so engine changes aren't needed and saves stay compatible. Auto-repeated orders remember only Fight/Run, never Throw cargo, and are sent after a grace window (1s, 250ms in fast play) so the player can still change orders mid-battle.

## Fidelity is the point

`taipan/engine.py` is a faithful port of `reference/taipan-original.bas` (the authoritative Applesoft BASIC listing), with message text and a few clarified behaviors from `reference/taipan-c-port.c` (Jay Link's canonical C port). Formulas (prices, Li Yuen extortion, Wu's 10%/month interest, combat odds, booty, scoring) intentionally match the BASIC source — check changes against those references before "fixing" anything that looks odd (e.g., buying cargo beyond hold capacity is allowed and shows "Overload"; opium seizures only happen outside Hong Kong; `FN R(X) = INT(RND*X)` is `Game.r()`). Where the two references disagree, the BASIC listing wins except where noted in comments (Li Yuen protection decay follows the C port).
