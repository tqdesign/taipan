"""Taipan! web server: serves the retro UI and drives game sessions."""

import threading
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from taipan.engine import Game

MAX_SESSIONS = 500

app = FastAPI(title="Taipan!")

_sessions: dict[str, dict] = {}
_lock = threading.Lock()


class StepRequest(BaseModel):
    session_id: str
    value: str | None = None


@app.post("/api/new")
def new_game():
    game = Game()
    gen = game.run()
    event = next(gen)
    session_id = uuid.uuid4().hex
    with _lock:
        # Drop oldest sessions if we somehow accumulate too many.
        while len(_sessions) >= MAX_SESSIONS:
            _sessions.pop(next(iter(_sessions)))
        _sessions[session_id] = {"gen": gen, "last": event}
    return {"session_id": session_id, "event": event}


@app.post("/api/step")
def step(req: StepRequest):
    with _lock:
        sess = _sessions.get(req.session_id)
    if sess is None:
        raise HTTPException(404, "No such game")
    with _lock:
        try:
            event = sess["gen"].send(req.value)
        except StopIteration:
            event = {**sess["last"], "messages": [], "done": True}
        sess["last"] = event
    return {"event": event}


@app.get("/api/state/{session_id}")
def state(session_id: str):
    with _lock:
        sess = _sessions.get(session_id)
    if sess is None:
        raise HTTPException(404, "No such game")
    return {"event": sess["last"]}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static",
                           html=True), name="static")


def main():
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
