"""Balance simulation: does an aggressive, cash-rich captain actually
face real risk of death in extended mode, or does the ship become
untouchable once it's big?

A heuristic "greedy captain" bot plays many full games per mode: it
always pays Li Yuen protection (cheap insurance), repairs damage fully
whenever offered (keeping the ship at low damage is exactly the case
the storm-risk floor targets), always accepts ship and gun upgrade
offers, fights once armed (flees before that), and shuttles
Hong Kong <-> Shanghai trading General Cargo (cheap enough to always
afford a load, unlike Opium on a starting 400 cash). It never
voluntarily retires, so each game runs until death or (in extended)
forced retirement - this isolates how much the engine's own hazards
matter, since the bot never uses "just quit" as a survival strategy.

Run: uv run python scripts/balance_sim.py [--games N]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from taipan.engine import Game  # noqa: E402

MAX_STEPS = 20000


def answer(prompt, state):
    kind = prompt["kind"]
    text = prompt.get("text") or ""
    if kind == "text":
        return "Greedy & Co."
    if kind in ("pause", "ack"):
        return ""
    if kind == "end":
        return None

    if kind == "number":
        low = text.lower()
        if "buy" in low:
            for p in prompt.get("presets", []):
                if p["label"] == "Max":
                    return str(p["value"])
            return "0"
        if "sell" in low or "spend" in low:   # sell cargo / McHenry repairs
            return "a"
        return "0"

    # kind == "choice"
    options = prompt["options"]
    keys = {o["key"] for o in options}
    if keys == {"f", "r", "t"}:
        return "f" if state["guns"] > 0 else "r"   # can't shoot, so flee
    if "How will you sail" in text:
        return state["mode_key"]
    if "How long will you trade" in text:
        return "1"                             # full career (measure hazards)
    if "Do you want to start" in text:
        return "2"                             # 5 guns: can fight from turn 1
    if "seasonal mission" in text.lower() or "charter is offered" in text:
        return "n"                             # keep the loop simple
    if "Accept?" in text and ("mission" in text.lower()
                              or "charter" in text.lower()):
        return "n"
    if "trade in your" in text:
        return "y"                             # ship upgrade
    if "buy a ship's gun" in text:
        return "y"                             # gun upgrade
    if "wanting repairs" in text:
        return "y"                             # McHenry: keep damage low
    if "Will you pay?" in text:
        return "y"                             # Li Yuen protection: cheap
                                                 # insurance, always worth it
    if "make up the difference" in text:
        return "y"
    if "business with Elder Brother Wu" in text:
        return "n"
    if "Are you willing" in text:
        return "y"                             # Wu bailout: avoid ending
    if "Retire and count your fortune" in text:
        return "n"                             # never voluntarily retire
    if "wish me to go to" in text:
        return "2" if state["here"] == "Hong Kong" else "1"
    if "q" in keys and "b" in keys:             # the port menu
        phase = state["phase"]
        state["phase"] = (phase + 1) % 3
        return ("s", "b", "q")[phase]
    if keys == {"o", "s", "a", "g"}:            # item picker
        return "g"                              # General: cheap enough to
                                                 # always afford a Max buy
    for o in options:
        if not o.get("danger"):
            return o["key"]
    return options[0]["key"]


CAUSES = {
    "Retired after twenty-five years at the helm": "forced_retirement",
    "The pirates got us": "battle_death",
    "Lost with all hands in a storm": "storm_death",
    "Retired a millionaire": "voluntary_retire",
}


def classify(journal):
    for entry in reversed(journal):
        text = entry["text"] if isinstance(entry, dict) else entry
        for needle, cause in CAUSES.items():
            if needle in text:
                return cause
    return "other"


def play_one(seed, mode):
    game = Game(seed=seed, mode=mode)
    state = {"mode_key": "1" if mode == "classic" else "2",
             "phase": 0, "here": "Hong Kong", "guns": 0}
    gen = game.run()
    ev = next(gen)
    steps = 0
    while True:
        steps += 1
        st = ev["state"]
        if st.get("location"):
            state["here"] = st["location"]
        if "guns" in st:
            state["guns"] = st["guns"]
        prompt = ev["prompt"]
        if prompt["kind"] == "end":
            return {"cause": classify(prompt["journal"]),
                     "score": prompt["score"], "years": game.time / 12,
                     "capacity": game.capacity, "guns": game.guns,
                     "battles": game.stats["battles"],
                     "storms": game.stats["storms"],
                     "timeout": False}
        if steps > MAX_STEPS:
            return {"cause": "timeout", "score": None,
                    "years": game.time / 12, "capacity": game.capacity,
                    "guns": game.guns, "battles": game.stats["battles"],
                    "storms": game.stats["storms"], "timeout": True}
        try:
            ev = gen.send(answer(prompt, state))
        except StopIteration:
            return {"cause": "stopiteration", "score": None,
                    "years": game.time / 12, "capacity": game.capacity,
                    "guns": game.guns, "battles": game.stats["battles"],
                    "storms": game.stats["storms"], "timeout": False}


def summarize(mode, results):
    n = len(results)
    causes = {}
    for r in results:
        causes[r["cause"]] = causes.get(r["cause"], 0) + 1
    print(f"\n=== {mode} ({n} games) ===")
    for cause, count in sorted(causes.items(), key=lambda kv: -kv[1]):
        print(f"  {cause:20s} {count:4d}  ({100 * count / n:5.1f}%)")
    died = sum(v for k, v in causes.items()
              if k in ("battle_death", "storm_death"))
    scored = [r["score"] for r in results if r["score"] is not None]
    print(f"  died (battle/storm): {died}/{n} ({100 * died / n:.1f}%)")
    if scored:
        print(f"  avg score: {sum(scored) / len(scored):,.0f}   "
              f"max score: {max(scored):,.0f}")
    print(f"  avg years played: {sum(r['years'] for r in results) / n:.1f}")
    print(f"  avg final capacity: "
          f"{sum(r['capacity'] for r in results) / n:,.0f}   "
          f"avg final guns: {sum(r['guns'] for r in results) / n:.1f}")
    print(f"  avg battles: {sum(r['battles'] for r in results) / n:.1f}   "
          f"avg storms survived: {sum(r['storms'] for r in results) / n:.1f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=100)
    args = parser.parse_args()

    for mode in ("classic", "extended"):
        results = [play_one(seed, mode) for seed in range(args.games)]
        summarize(mode, results)


if __name__ == "__main__":
    main()
