"""Server-level tests: persistence, challenges, rate limiting."""

import random

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient with saves isolated to a temp dir and a clean
    rate-limit window."""
    save_dir = tmp_path / "saves"
    monkeypatch.setattr(main, "SAVE_DIR", save_dir)
    monkeypatch.setattr(main, "CHALLENGE_DIR", save_dir / "challenges")
    monkeypatch.setattr(main, "HIGHSCORE_FILE",
                        save_dir / "highscores.json")
    monkeypatch.setattr(main, "DAILY_FILE", save_dir / "dailyscores.json")
    monkeypatch.setattr(main, "ACHIEVEMENT_FILE",
                        save_dir / "achievements.json")
    monkeypatch.setattr(main, "NEW_GAMES_PER_MINUTE", 1000)
    main._sessions.clear()
    main._rate.clear()
    return TestClient(main.app)


def play_to_end(client, body=None, seed=0, record=False):
    """Random-play a game to its end; optionally record (prompt, input)
    pairs for deterministic replay."""
    rng = random.Random(seed)
    d = client.post("/api/new", json=body or {}).json()
    sid, ev = d["session_id"], d["event"]
    log = []
    for _ in range(20000):
        p = ev["prompt"]
        if p["kind"] == "end":
            return sid, ev, log
        if p["kind"] == "text":
            v = "Server Test Co."
        elif p["kind"] == "choice":
            v = rng.choice([o["key"] for o in p["options"]])
        elif p["kind"] == "number":
            v = rng.choice(["a", "0", "5", "1000"])
        else:
            v = ""
        if record:
            log.append((p.get("text") or "", v))
        ev = client.post("/api/step",
                         json={"session_id": sid, "value": v}).json()["event"]
    raise AssertionError("game never ended")


def test_challenge_flow_is_deterministic(client):
    # Finish a game, recording every input.
    sid, ev, log = play_to_end(client, seed=11, record=True)
    score = ev["prompt"]["score"]

    # Turn it into a challenge.
    cid = client.post("/api/challenge",
                      json={"session_id": sid}).json()["challenge_id"]
    info = client.get(f"/api/challenge/{cid}").json()
    assert info["creator"]["score"] == score
    assert info["creator"]["firm"] == "Server Test Co."
    assert "seed" not in info                 # seed must stay private
    assert info["mode"] in ("classic", "extended")

    # Replay the identical inputs in the challenge (minus the mode
    # answer - the challenge forces the mode, so that prompt is
    # never asked).
    d = client.post("/api/new", json={"challenge": cid}).json()
    sid2, ev2 = d["session_id"], d["event"]
    for text, v in log:
        if "How will you sail" in text:
            continue
        ev2 = client.post(
            "/api/step",
            json={"session_id": sid2, "value": v}).json()["event"]
    assert ev2["prompt"]["kind"] == "end"
    assert ev2["prompt"]["score"] == score    # same seas, same fate

    # The attempt landed on the challenge board.
    info = client.get(f"/api/challenge/{cid}").json()
    assert len(info["attempts"]) == 1
    assert info["attempts"][0]["score"] == score


def test_challenge_requires_finished_game(client):
    d = client.post("/api/new", json={}).json()
    res = client.post("/api/challenge", json={"session_id":
                                              d["session_id"]})
    assert res.status_code == 400


def test_unknown_challenge_404(client):
    assert client.get(f"/api/challenge/{'0' * 32}").status_code == 404
    assert client.post("/api/new",
                       json={"challenge": "0" * 32}).status_code == 404


def test_bad_ids_rejected(client):
    assert client.get("/api/challenge/../etc").status_code in (400, 404)
    assert client.get(f"/api/state/{'z' * 32}").status_code == 400


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "NEW_GAMES_PER_MINUTE", 3)
    main._rate.clear()
    codes = [client.post("/api/new", json={}).status_code
             for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_session_survives_memory_eviction(client):
    """A session pushed out of memory is replayed from its save file."""
    d = client.post("/api/new", json={}).json()
    sid = d["session_id"]
    ev = client.post("/api/step",
                     json={"session_id": sid,
                           "value": "Evict & Co."}).json()["event"]
    main._sessions.clear()                    # simulate restart
    got = client.get(f"/api/state/{sid}").json()["event"]
    assert got["state"] == ev["state"]
