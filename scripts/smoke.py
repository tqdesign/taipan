"""Engine smoke test: plays many random games to completion.

Drives the Game.run() generator with random-but-valid answers and checks
core invariants (cash/bank/debt never negative, prices positive, etc.).
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from taipan.engine import Game  # noqa: E402

GAMES = 200
MAX_STEPS = 20000


def answer(prompt, rng):
    kind = prompt["kind"]
    if kind == "text":
        return "Smoke & Co."
    if kind in ("pause", "ack"):
        return ""
    if kind == "choice":
        # Mostly valid picks, occasionally garbage to test re-prompting.
        if rng.random() < 0.05:
            return "zz"
        return rng.choice(prompt["options"])["key"]
    if kind == "number":
        roll = rng.random()
        if roll < 0.2 and prompt.get("allow_all", True):
            return "a"
        if roll < 0.25:
            return "not-a-number"
        return str(rng.choice([0, 1, 5, 10, 100, 1000, 10**7]))
    if kind == "end":
        return None
    raise AssertionError(f"unknown prompt kind {kind}")


def check_invariants(ev, step):
    st = ev["state"]
    assert st["cash"] >= 0, f"negative cash at step {step}: {st['cash']}"
    assert st["bank"] >= 0, f"negative bank at step {step}: {st['bank']}"
    assert st["debt"] >= 0, f"negative debt at step {step}: {st['debt']}"
    assert all(v >= 0 for v in st["hold_items"]), "negative hold"
    assert all(v >= 0 for v in st["warehouse"]), "negative warehouse"
    assert 0 <= st["warehouse_used"] <= 10000, "warehouse overflow"
    if st["prices"]:
        assert all(p > 0 for p in st["prices"]), f"bad price {st['prices']}"
    if ev["battle"]:
        assert ev["battle"]["ships"] >= 0, "negative ships"


def play_one(seed):
    rng = random.Random(seed)
    game = Game(seed=seed)
    gen = game.run()
    ev = next(gen)
    steps = 0
    while True:
        steps += 1
        if steps > MAX_STEPS:
            raise AssertionError(f"game (seed {seed}) never ended")
        check_invariants(ev, steps)
        if ev["prompt"]["kind"] == "end":
            return steps
        try:
            ev = gen.send(answer(ev["prompt"], rng))
        except StopIteration:
            return steps


def main():
    total = 0
    for seed in range(GAMES):
        total += play_one(seed)
    print(f"OK: {GAMES} games completed, {total} total steps, "
          f"avg {total // GAMES} steps/game")


if __name__ == "__main__":
    main()
