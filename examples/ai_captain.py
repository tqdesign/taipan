"""AI Captain — teaching example: drive Taipan! through its backend API.

This script demonstrates the core loop of every AI agent:

    OBSERVE  ->  the game's JSON state + the prompt describing legal moves
    REASON   ->  a rules engine (level 1) or Claude (level 2) picks a move
    ACT      ->  POST the move back to /api/step
    ... repeat until the game ends.

The game server is the perfect classroom for this because every
response already contains what an agent needs: a full state snapshot,
the messages since the last move, and a `prompt` object that
*enumerates the legal actions* (choices with keys, numbers with hints).
No screen scraping, no guessing.

Levels:
  --captain rules    Level 1: no AI. Hard-coded heuristics. Free, fast.
  --captain claude   Level 2: Claude decides at real decision points.

The single most important pattern here is DECISION-POINT FILTERING:
most steps in a game are pauses, acknowledgements, and bookkeeping.
The agent answers those with two lines of code and only spends an
(expensive, slow) model call on steps where a decision actually
matters. Watch the `answer()` method.

Usage:
    # terminal 1: run the game server
    uv run main.py

    # terminal 2: watch a rules bot play (no AI, no key needed)
    uv run python examples/ai_captain.py --captain rules --verbose

    # let Claude play (uses ANTHROPIC_API_KEY or `ant auth login`)
    uv run --with anthropic python examples/ai_captain.py \
        --captain claude --verbose

Cost note: a full game is hundreds of steps but only ~100-200 real
decisions. With the default model that's real money (dollars, not
cents) per game — that's part of the lesson. Use --model
claude-haiku-4-5 for classroom budgets, or --max-steps to cap a demo.
"""

import argparse
import json
import sys
import urllib.request

# ----------------------------------------------------------------------
# The game client: a thin wrapper over two endpoints.

class GameClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.session_id = None

    def _post(self, path, body):
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

    def new_game(self):
        data = self._post("/api/new", {})
        self.session_id = data["session_id"]
        return data["event"]

    def step(self, value):
        data = self._post("/api/step", {"session_id": self.session_id,
                                        "value": value})
        return data["event"]


# ----------------------------------------------------------------------
# Level 1: a rules-based captain. No AI anywhere — this is the baseline
# that teaches the loop itself, and the fallback when the AI misfires.

# Rough midpoint value of each commodity, used to judge if a price is
# high or low relative to "normal" (opium, silk, arms, general).
TYPICAL_PRICE = [11000, 1600, 90, 26]

class RulesCaptain:
    name = "rules"

    def __init__(self):
        self.sell_phase = True   # at each port: sell first, then buy once

    def answer(self, event, transcript):
        prompt = event["prompt"]
        state = event["state"]
        kind = prompt["kind"]
        text = prompt.get("text") or ""

        # ---- trivial steps: never worth any reasoning ----
        if kind in ("pause", "ack"):
            return ""
        if kind == "text":
            return "Rule Britannia Ltd."

        keys = [o["key"] for o in prompt.get("options", [])]

        if kind == "choice":
            # game setup
            if "How will you sail" in text:
                return "1"                      # classic mode
            if "Do you want to start" in text:
                return "1"                      # cash and a debt
            # battle orders: fight if armed, otherwise run
            if keys == ["f", "r", "t"]:
                return "f" if state["guns"] > 0 else "r"
            if "throw overboard" in text:
                return "g"                      # ditch the cheap stuff
            # Li Yuen wants a donation: protection is worth it
            if "Will you pay" in text:
                return "y"
            # Elder Brother Wu: only visit to repay debt we can afford
            if "business with Elder Brother Wu" in text:
                return "y" if 0 < state["debt"] <= state["cash"] else "n"
            # offers (ship, gun, repairs): the engine only offers what
            # we can afford, and bigger/armed/fixed is usually better
            if "50 more capacity" in text or "ship's gun" in text \
                    or "wanting repairs" in text:
                return "y"
            # retirement: the moment it's offered, take the win
            if "Retire and count your fortune" in text:
                return "y"
            # the port menu: sell high, buy low, then sail on
            if text.startswith("Shall I"):
                if "r" in keys:
                    return "r"                  # we're millionaires!
                if self.sell_phase and self._item_to_sell(state) is not None:
                    return "s"
                if not self.sell_phase and self._item_to_buy(state) is not None:
                    self.sell_phase = True      # buy once, then leave
                    return "b"
                if not self.sell_phase:
                    self.sell_phase = True
                return "q"
            if "What do you wish me to sell" in text:
                self.sell_phase = self._item_to_sell(state) is not None
                item = self._item_to_sell(state)
                self.sell_phase = False
                return "osag"[item] if item is not None else "o"
            if "What do you wish me to buy" in text:
                item = self._item_to_buy(state)
                return "osag"[item] if item is not None else "g"
            # destination: bounce between Hong Kong and Shanghai
            if "do you wish me to go to" in text:
                return "2" if state["location"] == "Hong Kong" else "1"
            # anything unrecognized: first option (better than stalling)
            return keys[0] if keys else ""

        if kind == "number":
            # amounts: 'a' means all/max, which suits a simple bot,
            # except debts and banking which we handle by hint
            if "repay" in text:
                return "a"
            if "borrow" in text or "deposit" in text or "withdraw" in text:
                return "0"
            return "a"

        return ""

    def _item_to_sell(self, state):
        """Sell any cargo priced above its typical value."""
        prices = state.get("prices")
        if not prices:
            return None
        for i, amount in enumerate(state["hold_items"]):
            if amount > 0 and prices[i] > TYPICAL_PRICE[i]:
                return i
        return None

    def _item_to_buy(self, state):
        """Buy whatever is cheapest relative to its typical value."""
        prices = state.get("prices")
        if not prices or state["cash"] < 100:
            return None
        ratios = [(prices[i] / TYPICAL_PRICE[i], i) for i in range(4)
                  if state["cash"] // prices[i] > 0]
        if not ratios:
            return None
        ratio, item = min(ratios)
        return item if ratio < 0.95 else None


# ----------------------------------------------------------------------
# Level 2: Claude decides. Same loop, but real decisions go to a model.

CLAUDE_SYSTEM = """\
You are the captain in Taipan!, the classic 1982 trading game set in
the 1860s China trade. You will be shown the game state and a prompt
with the legal moves; answer with your chosen move.

Strategy fundamentals:
- Buy commodities where cheap, sell where dear. Typical mid prices:
  Opium ~11000, Silk ~1600, Arms ~90, General ~26.
- Elder Brother Wu's debt compounds at 10% PER MONTH. Repay fast;
  borrow only for a trade you can complete quickly.
- Cash above ~25000 attracts muggers; the bank (Hong Kong only) is
  safe and pays 0.5%/month.
- Paying Li Yuen's donation buys protection from his pirate fleet.
- In battle: Fight when you have guns; Run when you don't; throwing
  cargo lightens the ship to aid escape.
- Score = net worth / 100 / months^1.1 — speed matters. Retire when
  the option appears unless you are close to a much bigger milestone.

Answer format: reply with ONLY the move, optionally followed by " | "
and a one-line reason. For choices, the move is the option's key
(single character or digit). For amounts, a whole number, or "a" for
all/maximum. Examples:
b | opium is 40% below typical here
a | sell everything, price is double typical
n | debt is manageable, avoid Wu's 10%/month
"""


class ClaudeCaptain:
    name = "claude"

    def __init__(self, model, effort, verbose):
        import anthropic                       # lazy: only for AI mode
        # Zero-arg client: resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
        # or an `ant auth login` profile automatically.
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.verbose = verbose
        self.fallback = RulesCaptain()         # when the model misfires
        self.ai_calls = 0

    def answer(self, event, transcript):
        prompt = event["prompt"]
        kind = prompt["kind"]

        # ---- DECISION-POINT FILTERING ----
        # Pauses, acknowledgements and the firm name are not decisions.
        # Answering them locally is the difference between ~150 model
        # calls per game and ~700. Never spend tokens on trivia.
        if kind in ("pause", "ack"):
            return ""
        if kind == "text":
            return "Claude & Co."

        move = self._ask_claude(event, transcript)
        valid = self._validate(move, prompt)
        if valid is not None:
            return valid
        # The model answered something illegal (it happens!) —
        # log it and fall back to the rules captain. Always validate.
        if self.verbose:
            print(f"      [invalid AI move {move!r}; using rules fallback]")
        return self.fallback.answer(event, transcript)

    def _ask_claude(self, event, transcript):
        self.ai_calls += 1
        situation = self._describe(event, transcript)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,                   # room for adaptive thinking
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            system=CLAUDE_SYSTEM,
            messages=[{"role": "user", "content": situation}],
        )
        if response.stop_reason == "refusal":
            return ""                          # extremely unlikely here
        text = next((b.text for b in response.content
                     if b.type == "text"), "")
        move, _, reason = text.strip().partition("|")
        if self.verbose and reason.strip():
            print(f"      [claude: {reason.strip()}]")
        return move.strip().lower()

    def _describe(self, event, transcript):
        """Compact, model-friendly view of the situation. Everything
        here comes straight from the API response — the observation
        half of observe/reason/act."""
        state = event["state"]
        prompt = event["prompt"]
        lines = [
            f"Date: {state['month']} {state['year']} | "
            f"Location: {state['location']}",
            f"Cash: {state['cash']} | Bank: {state['bank']} | "
            f"Debt: {state['debt']}",
            f"Ship: {state['guns']} guns, hold space {state['hold_space']}"
            f" of {state['capacity']}, condition {state['status_pct']}%",
            f"Cargo aboard (opium/silk/arms/general): "
            f"{state['hold_items']}",
            f"Warehouse (Hong Kong): {state['warehouse']}",
        ]
        if state.get("prices"):
            lines.append(f"Prices here: {state['prices']}")
        if state.get("seen_prices"):
            seen = "; ".join(
                f"{s['port']} {s['prices']} ({s['when']})"
                for s in state["seen_prices"])
            lines.append(f"Market log (last seen prices): {seen}")
        if event.get("battle"):
            b = event["battle"]
            lines.append(f"BATTLE: {b['ships']} ships attacking, we have "
                         f"{b['guns']} guns, seaworthiness "
                         f"{b['status_pct']}%")
        if transcript:
            lines.append("Recent events: " + " / ".join(transcript[-8:]))
        lines.append("")
        lines.append(f"PROMPT: {prompt.get('text') or '(choose)'}")
        if prompt.get("hint"):
            lines.append(f"Hint: {prompt['hint']}")
        if prompt["kind"] == "choice":
            opts = ", ".join(f"[{o['key']}] {o['label']}"
                             for o in prompt["options"])
            lines.append(f"Legal moves: {opts}")
        else:
            lines.append("Legal moves: a whole number"
                         + (", or 'a' for all"
                            if prompt.get("allow_all", True) else ""))
        lines.append("Your move:")
        return "\n".join(lines)

    def _validate(self, move, prompt):
        """Never trust model output blindly — map it onto a legal move."""
        if prompt["kind"] == "choice":
            keys = [o["key"] for o in prompt["options"]]
            return move if move in keys else None
        if prompt["kind"] == "number":
            if move in ("a", "all") and prompt.get("allow_all", True):
                return "a"
            return move if move.isdigit() else None
        return None


# ----------------------------------------------------------------------
# The agent loop itself: observe -> reason -> act.

def play(client, captain, max_steps, verbose):
    event = client.new_game()
    transcript = []
    for step in range(1, max_steps + 1):
        # OBSERVE: collect what happened since our last move
        for message in event["messages"]:
            if "text" in message:
                transcript.append(message["text"])
                if verbose:
                    print(f"   | {message['text']}")
        prompt = event["prompt"]
        if prompt["kind"] == "end":
            print(f"\nGAME OVER after {step} steps: "
                  f"score {prompt['score']:,} ({prompt['rating']})")
            if isinstance(captain, ClaudeCaptain):
                print(f"Model calls spent: {captain.ai_calls} "
                      f"({captain.ai_calls * 100 // step}% of steps — "
                      f"decision-point filtering at work)")
            return prompt["score"]

        # REASON: pick a move
        move = captain.answer(event, transcript)
        if verbose and prompt["kind"] not in ("pause", "ack"):
            shown = (prompt.get("text") or "")[:60]
            print(f"{step:4d} [{captain.name}] {shown!r} -> {move!r}")

        # ACT: send it back
        event = client.step(move)
    print(f"\nStopped at --max-steps {max_steps} (game unfinished).")
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Play Taipan! through its API with a bot or with "
                    "Claude — a teaching example of the agent loop.")
    parser.add_argument("--server", default="http://127.0.0.1:8000",
                        help="game server (default: local)")
    parser.add_argument("--captain", choices=["rules", "claude"],
                        default="rules")
    parser.add_argument("--model", default="claude-opus-4-8",
                        help="model for --captain claude "
                             "(claude-haiku-4-5 is the budget option)")
    parser.add_argument("--effort", default="low",
                        choices=["low", "medium", "high"],
                        help="thinking effort per decision (default low; "
                             "higher plays better and costs more)")
    parser.add_argument("--max-steps", type=int, default=5000)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.captain == "claude":
        try:
            captain = ClaudeCaptain(args.model, args.effort, args.verbose)
        except Exception as exc:
            sys.exit(f"Could not create the Claude client ({exc}). "
                     f"Install the SDK (pip install anthropic / uv run "
                     f"--with anthropic) and set ANTHROPIC_API_KEY or "
                     f"run `ant auth login`.")
    else:
        captain = RulesCaptain()

    client = GameClient(args.server)
    play(client, captain, args.max_steps, args.verbose)


if __name__ == "__main__":
    main()
