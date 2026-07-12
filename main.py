"""Taipan! web server: serves the retro UI and drives game sessions.

Persistence design: the engine is deterministic given its RNG seed, so a
game is fully described by (seed, list of inputs). Every step appends to
an on-disk event log in saves/; an unknown session id is restored by
replaying its log into a fresh generator. This survives server restarts
and browser refreshes.

Challenges: a finished game can be turned into a challenge link. The
challenge file stores the seed, the forced mode, the creator's score and
net-worth curve (for the ghost race), and an attempts board.

DEPLOYMENT NOTE: live generators are held in this process's memory, so
run a single worker (the default here). With multiple workers a session
would be replayed from disk on each worker that sees it, which works but
wastes CPU and risks interleaved writes to the same save file. Bind
host/port via the HOST and PORT environment variables; persist the
saves/ directory on a volume.
"""

import hashlib
import json
import os
import random
import re
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import date
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from taipan.engine import ENGINE_VERSION, Game

ROOT = Path(__file__).parent
SAVE_DIR = ROOT / "saves"
CHALLENGE_DIR = SAVE_DIR / "challenges"
HIGHSCORE_FILE = SAVE_DIR / "highscores.json"
DAILY_FILE = SAVE_DIR / "dailyscores.json"
ACHIEVEMENT_FILE = SAVE_DIR / "achievements.json"
MAX_SESSIONS = 500
MAX_HIGHSCORES = 10
MAX_SAVE_FILES = 2000
SAVE_TTL_DAYS = 30
MAX_CHALLENGE_ATTEMPTS = 50
MAX_CHALLENGE_FILES = 500

# Abuse limits, per IP per minute. Humans in fast play peak around
# 5-10 steps/second in short bursts; these are far above legitimate
# play and exist to stop floods, not players.
NEW_GAMES_PER_MINUTE = 12
STEPS_PER_MINUTE = 600
CHALLENGES_PER_MINUTE = 3
STATE_READS_PER_MINUTE = 120

# The longest legitimate input is a 22-char firm name; anything huge
# would bloat the replay log.
MAX_INPUT_LENGTH = 64
# Restoring a session replays its whole log; refuse absurd ones.
MAX_REPLAY_INPUTS = 50_000

app = FastAPI(title="Taipan!")


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """Explicit cache policy so CDNs (Cloudflare) don't guess: code and
    API stay fresh across deploys; heavy immutable assets cache a day.
    StaticFiles serves ETags, so no-cache revalidations are cheap 304s.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif (path in ("/", "/index.html") or path.endswith(".js")
            or path.endswith(".css")):
        response.headers["Cache-Control"] = "no-cache"
    elif path.endswith((".ttf", ".png", ".ico", ".svg", ".webmanifest")):
        response.headers["Cache-Control"] = "public, max-age=86400"
    return response

_sessions: dict[str, dict] = {}
_registry_lock = threading.Lock()   # guards _sessions dict itself
_score_lock = threading.Lock()      # guards the high-score files
_challenge_lock = threading.Lock()  # guards challenge files
_rate: dict[str, deque] = {}
_rate_lock = threading.Lock()


def _app_version() -> str:
    """Build stamp vMMDDYY.HHMM. Baked into version.txt by the Docker
    build; in local dev, derived from the newest source file mtime."""
    stamp = ROOT / "version.txt"
    if stamp.exists():
        return stamp.read_text(encoding="utf-8").strip()
    sources = [ROOT / "main.py", ROOT / "taipan" / "engine.py",
               ROOT / "static" / "app.js", ROOT / "static" / "index.html",
               ROOT / "static" / "style.css"]
    newest = max(f.stat().st_mtime for f in sources if f.exists())
    return time.strftime("v%m%d%y.%H%M", time.gmtime(newest))


APP_VERSION = _app_version()


def today() -> str:
    return date.today().isoformat()


def _daily_salt() -> str:
    """Server-side secret mixed into the daily seed. Without it, anyone
    could derive tomorrow's seed from this (public) source code and
    rehearse the whole day offline. Set DAILY_SALT in the environment,
    or a random one is generated once and kept in saves/."""
    salt = os.environ.get("DAILY_SALT")
    if salt:
        return salt
    path = SAVE_DIR / "daily_salt.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    salt = secrets.token_hex(16)
    SAVE_DIR.mkdir(exist_ok=True)
    path.write_text(salt, encoding="utf-8")
    return salt


def daily_seed(day: str) -> int:
    """Deterministic seed shared by every player on a given day."""
    return int(hashlib.sha256(
        f"taipan-daily-{_daily_salt()}-{day}".encode())
        .hexdigest()[:15], 16)


class NewRequest(BaseModel):
    daily: bool = False
    challenge: str | None = None


class StepRequest(BaseModel):
    session_id: str
    value: str | None = None


class ChallengeRequest(BaseModel):
    session_id: str


# ----------------------------------------------------------------------
# Hygiene

# Firm names appear on public boards, so profanity is masked before the
# name ever reaches the engine (and therefore before it reaches the
# input log, keeping replays deterministic). Best effort, not an arms
# race: leetspeak is normalized, strong words match anywhere, milder
# words only match standalone to avoid Scunthorpe-style false positives
# (Grass & Sons, Hancock Trading, Dickens & Co. stay untouched).
_LEET = str.maketrans("01345789@$!", "oieastbgasi")
_PROFANE_ANYWHERE = ["fuck", "shit", "cunt", "nigg", "faggot", "bitch",
                     "whore", "slut", "asshole", "dickhead", "jizz"]
_PROFANE_STANDALONE = ["ass", "dick", "cock", "tit", "tits", "cum",
                       "twat", "prick", "fag", "spic", "chink", "kike",
                       "wank", "penis", "anus", "rape", "rapist",
                       "pussy", "bastard", "douche", "retard"]


def clean_firm(name: str) -> str:
    if not name:
        return name
    norm = name.lower().translate(_LEET)
    masked = list(name)

    def mask(match):
        for i in range(match.start(), match.end()):
            masked[i] = "*"

    for word in _PROFANE_ANYWHERE:
        for m in re.finditer(re.escape(word), norm):
            mask(m)
    for word in _PROFANE_STANDALONE:
        pattern = rf"(?<![a-z]){re.escape(word)}(?![a-z])"
        for m in re.finditer(pattern, norm):
            mask(m)
    return "".join(masked)


def _check_hex_id(some_id: str):
    # Our ids are uuid4 hex; validate before using them in paths.
    if not (len(some_id) == 32 and all(c in "0123456789abcdef"
                                       for c in some_id)):
        raise HTTPException(400, "Bad id")


def _rate_limit(request: Request, bucket: str, per_minute: int):
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _rate_lock:
        window = _rate.setdefault((bucket, ip), deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= per_minute:
            raise HTTPException(429, "Catch your breath, Taipan. "
                                     "The harbor master waves you off.")
        window.append(now)


def _session_save_files():
    if not SAVE_DIR.exists():
        return []
    return [f for f in SAVE_DIR.glob("*.json")
            if len(f.stem) == 32
            and all(c in "0123456789abcdef" for c in f.stem)]


def prune_saves():
    """Startup sweep: drop stale session saves and cap the file count."""
    cutoff = time.time() - SAVE_TTL_DAYS * 86400
    files = _session_save_files()
    for f in files:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
    files = sorted(_session_save_files(),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[MAX_SAVE_FILES:]:
        try:
            f.unlink()
        except OSError:
            pass


# ----------------------------------------------------------------------
# Save files

def _save_path(session_id: str) -> Path:
    _check_hex_id(session_id)
    return SAVE_DIR / f"{session_id}.json"


def _write_save(session_id: str, sess: dict):
    SAVE_DIR.mkdir(exist_ok=True)
    payload = {"version": ENGINE_VERSION, "seed": sess["seed"],
               "inputs": sess["inputs"], "scored": sess["scored"],
               "daily": sess["daily"], "mode": sess["mode"],
               "challenge": sess["challenge"], "updated": time.time()}
    _save_path(session_id).write_text(json.dumps(payload),
                                      encoding="utf-8")


def _register(session_id: str, sess: dict):
    with _registry_lock:
        while len(_sessions) >= MAX_SESSIONS:
            _sessions.pop(next(iter(_sessions)))
        _sessions[session_id] = sess


def _restore(session_id: str) -> dict | None:
    """Rebuild a session from its save file by replaying the input log."""
    path = _save_path(session_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != ENGINE_VERSION:
        # The engine's prompt/RNG flow changed since this was saved;
        # replaying would produce a silently wrong game state.
        path.unlink(missing_ok=True)
        return None
    if len(data.get("inputs", [])) > MAX_REPLAY_INPUTS:
        # No legitimate game gets near this; refuse the replay cost.
        path.unlink(missing_ok=True)
        return None
    daily = data.get("daily")
    mode = data.get("mode") or ("classic" if daily else None)
    game = Game(seed=data["seed"], mode=mode, daily=daily)
    gen = game.run()
    event = next(gen)
    for value in data["inputs"]:
        try:
            event = gen.send(value)
        except StopIteration:
            break
    sess = {"gen": gen, "last": event, "seed": data["seed"],
            "inputs": data["inputs"], "scored": data.get("scored", False),
            "daily": daily, "mode": mode,
            "challenge": data.get("challenge"),
            "lock": threading.Lock()}
    _register(session_id, sess)
    return sess


def _get_session(session_id: str) -> dict:
    with _registry_lock:
        sess = _sessions.get(session_id)
    if sess is None:
        sess = _restore(session_id)
    if sess is None:
        raise HTTPException(404, "No such game")
    return sess


# ----------------------------------------------------------------------
# High scores and achievements

def _load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _record_highscore(event: dict):
    prompt = event.get("prompt") or {}
    state = event.get("state") or {}
    daily = prompt.get("daily")
    entry = {
        "firm": state.get("firm", "?"),
        "score": prompt.get("score", 0),
        "rating": prompt.get("rating", "?"),
        "mode": prompt.get("mode", "classic"),
        "date": f"{state.get('month', '?')} {state.get('year', '?')}",
        "when": time.strftime("%Y-%m-%d"),
    }
    with _score_lock:
        SAVE_DIR.mkdir(exist_ok=True)
        scores = _load_json(HIGHSCORE_FILE, [])
        scores.append(entry)
        scores.sort(key=lambda s: s["score"], reverse=True)
        HIGHSCORE_FILE.write_text(
            json.dumps(scores[:MAX_HIGHSCORES], indent=1),
            encoding="utf-8")
        if daily:
            boards = _load_json(DAILY_FILE, {})
            board = boards.get(daily, [])
            board.append(entry)
            board.sort(key=lambda s: s["score"], reverse=True)
            boards[daily] = board[:MAX_HIGHSCORES]
            # Keep only the last two weeks of daily boards.
            for key in sorted(boards)[:-14]:
                del boards[key]
            DAILY_FILE.write_text(json.dumps(boards, indent=1),
                                  encoding="utf-8")
        # First-unlock registry of achievements.
        earned = prompt.get("achievements") or []
        if earned:
            unlocked = _load_json(ACHIEVEMENT_FILE, {})
            for a in earned:
                if a["id"] not in unlocked:
                    unlocked[a["id"]] = {
                        "name": a["name"], "desc": a["desc"],
                        "firm": entry["firm"], "when": entry["when"]}
            ACHIEVEMENT_FILE.write_text(json.dumps(unlocked, indent=1),
                                        encoding="utf-8")


# ----------------------------------------------------------------------
# Challenges

def _challenge_path(challenge_id: str) -> Path:
    _check_hex_id(challenge_id)
    return CHALLENGE_DIR / f"{challenge_id}.json"


def _load_challenge(challenge_id: str) -> dict:
    path = _challenge_path(challenge_id)
    if not path.exists():
        raise HTTPException(404, "No such challenge")
    return json.loads(path.read_text(encoding="utf-8"))


def _record_attempt(challenge_id: str, event: dict):
    prompt = event.get("prompt") or {}
    state = event.get("state") or {}
    with _challenge_lock:
        try:
            data = _load_challenge(challenge_id)
        except HTTPException:
            return
        # Per-firm try counter: a 40th-try score shouldn't masquerade
        # as a first-try score (best effort - names are honor-system).
        firm = state.get("firm", "?")
        tries = data.setdefault("tries", {})
        tries[firm] = tries.get(firm, 0) + 1
        data["attempts"].append({
            "firm": firm,
            "score": prompt.get("score", 0),
            "rating": prompt.get("rating", "?"),
            "attempt": tries[firm],
            "when": time.strftime("%Y-%m-%d"),
        })
        data["attempts"].sort(key=lambda s: s["score"], reverse=True)
        data["attempts"] = data["attempts"][:MAX_CHALLENGE_ATTEMPTS]
        _challenge_path(challenge_id).write_text(
            json.dumps(data), encoding="utf-8")


# ----------------------------------------------------------------------
# API

@app.post("/api/new")
def new_game(req: NewRequest, request: Request):
    _rate_limit(request, "new", NEW_GAMES_PER_MINUTE)
    day = None
    mode = None
    if req.challenge:
        meta = _load_challenge(req.challenge)
        seed, mode = meta["seed"], meta["mode"]
        game = Game(seed=seed, mode=mode)
    elif req.daily:
        day = today()
        seed, mode = daily_seed(day), "classic"
        game = Game(seed=seed, mode=mode, daily=day)
    else:
        seed = random.getrandbits(64)
        game = Game(seed=seed)
    gen = game.run()
    event = next(gen)
    session_id = uuid.uuid4().hex
    sess = {"gen": gen, "last": event, "seed": seed, "inputs": [],
            "scored": False, "daily": day, "mode": mode,
            "challenge": req.challenge, "lock": threading.Lock()}
    _register(session_id, sess)
    _write_save(session_id, sess)
    return {"session_id": session_id, "event": event,
            "challenge": req.challenge}


@app.post("/api/step")
def step(req: StepRequest, request: Request):
    _rate_limit(request, "step", STEPS_PER_MINUTE)
    sess = _get_session(req.session_id)
    with sess["lock"]:
        value = req.value
        if value and len(value) > MAX_INPUT_LENGTH:
            value = value[:MAX_INPUT_LENGTH]
        # The only free-text prompt is the firm name; scrub it before
        # it reaches the engine or the replay log.
        if ((sess["last"].get("prompt") or {}).get("kind") == "text"
                and value):
            value = clean_firm(value)
        try:
            event = sess["gen"].send(value)
            sess["inputs"].append(value)
        except StopIteration:
            event = {**sess["last"], "messages": [], "done": True}
        sess["last"] = event
        if (event.get("done") and (event.get("prompt") or {}).get("kind")
                == "end" and not sess["scored"]):
            sess["scored"] = True
            _record_highscore(event)
            if sess["challenge"]:
                _record_attempt(sess["challenge"], event)
        _write_save(req.session_id, sess)
    return {"event": event}


@app.get("/api/state/{session_id}")
def state(session_id: str, request: Request):
    _rate_limit(request, "state", STATE_READS_PER_MINUTE)
    sess = _get_session(session_id)
    with sess["lock"]:
        # Re-deliver the last event, minus one-shot messages/fx that the
        # client already animated before the refresh.
        event = {**sess["last"], "messages": []}
    return {"event": event, "challenge": sess["challenge"]}


@app.post("/api/challenge")
def create_challenge(req: ChallengeRequest, request: Request):
    _rate_limit(request, "challenge", CHALLENGES_PER_MINUTE)
    sess = _get_session(req.session_id)
    with sess["lock"]:
        last = sess["last"]
        prompt = last.get("prompt") or {}
        if not (last.get("done") and prompt.get("kind") == "end"):
            raise HTTPException(400, "Finish the voyage first, Taipan.")
        state_ = last.get("state") or {}
        challenge_id = uuid.uuid4().hex
        data = {
            "seed": sess["seed"],
            "mode": prompt.get("mode") or "classic",
            "created": time.strftime("%Y-%m-%d"),
            "creator": {
                "firm": state_.get("firm", "?"),
                "score": prompt.get("score", 0),
                "rating": prompt.get("rating", "?"),
                "net_history": prompt.get("net_history") or [],
            },
            "attempts": [],
        }
    with _challenge_lock:
        CHALLENGE_DIR.mkdir(parents=True, exist_ok=True)
        # Cap the number of challenge files; oldest go first.
        files = sorted(CHALLENGE_DIR.glob("*.json"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[MAX_CHALLENGE_FILES - 1:]:
            try:
                f.unlink()
            except OSError:
                pass
        _challenge_path(challenge_id).write_text(json.dumps(data),
                                                 encoding="utf-8")
    return {"challenge_id": challenge_id}


@app.get("/api/challenge/{challenge_id}")
def challenge_info(challenge_id: str):
    data = _load_challenge(challenge_id)
    # The seed stays server-side; opponents shouldn't be able to
    # dry-run the exact world locally before playing "for real".
    return {"mode": data["mode"], "created": data["created"],
            "creator": data["creator"], "attempts": data["attempts"]}


@app.get("/api/version")
def version():
    return {"version": APP_VERSION}


@app.get("/api/highscores")
def highscores():
    day = today()
    with _score_lock:
        return {"scores": _load_json(HIGHSCORE_FILE, []),
                "daily_date": day,
                "daily_scores": _load_json(DAILY_FILE, {}).get(day, []),
                "achievements": _load_json(ACHIEVEMENT_FILE, {})}


app.mount("/", StaticFiles(directory=ROOT / "static", html=True),
          name="static")

prune_saves()


def main():
    uvicorn.run(app,
                host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")),
                # Backpressure: queue excess connections rather than
                # letting a flood exhaust the single worker.
                limit_concurrency=100,
                timeout_keep_alive=10)


if __name__ == "__main__":
    main()
