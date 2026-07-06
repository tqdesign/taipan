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

## Test

```sh
uv run python scripts/smoke.py
```

Plays 200 random games to completion and checks engine invariants.

## Structure

- `taipan/engine.py` — the game engine. Runs as a generator that yields
  events (messages + state snapshot + prompt) and receives player input
  via `.send()`, mirroring the original's linear BASIC flow.
- `main.py` — FastAPI server; one generator per browser session.
- `static/` — the web client (splash, port screen, sea battles).
- `reference/` — the original sources this port is based on.
