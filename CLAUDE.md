# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A web port of Art Canfil's 1982 Apple II game **Taipan!** — Python game engine + FastAPI server + vanilla-JS retro terminal frontend. Uses `uv` for Python environment/dependency management.

## Commands

- Run the server: `uv run main.py` (serves http://127.0.0.1:8000)
- Dev server with reload: `uv run uvicorn main:app --reload`
- Engine smoke test: `uv run python scripts/smoke.py` — plays 200 random games to completion, asserting invariants (no negative cash/cargo/debt, valid prices). Run this after any engine change.
- Add a dependency: `uv add <package>`

## Architecture

The core design decision: **the game engine is a Python generator**, not a state machine. `Game.run()` in `taipan/engine.py` executes the game's linear flow top-to-bottom (mirroring the original line-numbered BASIC), yielding an event dict whenever player input is needed and receiving the answer via `.send(value)`. Sub-flows (trading, sea battles, Elder Brother Wu, etc.) are generator methods composed with `yield from`, whose return values are the player's parsed answers.

Each yielded event contains:
- `messages` — text lines and battle FX (`{"fx": "blast"|"sink"|"appear"|"clear"|"incoming", "slot": n}`) accumulated since the last input
- `prompt` — what input is expected: `choice` (options with hotkeys), `number` (supports `A` = All, like the original), `text`, `pause` (auto-continue with timeout), `end`
- `state` — full snapshot (cash, hold, warehouse, prices, date, ship status...)
- `battle` — sea-battle snapshot or null; the frontend switches screens based on this

`main.py` holds one generator per browser session in an in-memory dict (`/api/new` creates, `/api/step` sends input, sessions are not persisted). `static/app.js` renders events sequentially: it animates message/FX queues, then shows the prompt; the lorcha ship art and sink animation frames are copied from the C port.

## Fidelity is the point

`taipan/engine.py` is a faithful port of `reference/taipan-original.bas` (the authoritative Applesoft BASIC listing), with message text and a few clarified behaviors from `reference/taipan-c-port.c` (Jay Link's canonical C port). Formulas (prices, Li Yuen extortion, Wu's 10%/month interest, combat odds, booty, scoring) intentionally match the BASIC source — check changes against those references before "fixing" anything that looks odd (e.g., buying cargo beyond hold capacity is allowed and shows "Overload"; opium seizures only happen outside Hong Kong; `FN R(X) = INT(RND*X)` is `Game.r()`). Where the two references disagree, the BASIC listing wins except where noted in comments (Li Yuen protection decay follows the C port).
