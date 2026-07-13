"""AI Captain — teaching example: drive Taipan! through its backend API.

This script demonstrates the core loop of every AI agent:

    OBSERVE  ->  the game's JSON state + the prompt describing legal moves
    REASON   ->  a rules engine (level 1) or an LLM (level 2) picks a move
    ACT      ->  POST the move back to /api/step
    ... repeat until the game ends.

Levels:
  --captain rules    Level 1: no AI. Hard-coded heuristics. Free, fast.
  --captain claude   Level 2: Anthropic's Claude decides (ANTHROPIC_API_KEY)
  --captain grok     Level 2: xAI's Grok decides       (XAI_API_KEY)
  --captain gemini   Level 2: Google's Gemini decides  (GEMINI_API_KEY)
  --compare          Play every provider you have a key for, same seed,
                     and print a comparison table.

All LLM captains share the same brain (observation building, output
validation, decision-point filtering) and differ ONLY in the API call —
a clean illustration that the agent loop is provider-agnostic even
though each provider speaks its own dialect.

Fair comparisons: pass --daily so every captain sails the SAME seed
(the game's daily-challenge mechanism). Their scores land on the daily
leaderboard together — the game's own scoreboard becomes your model
benchmark.

Usage:
    # terminal 1: run the game server
    uv run main.py

    # terminal 2:
    uv run python examples/ai_captain.py --captain rules --verbose
    uv run --with anthropic python examples/ai_captain.py \
        --captain claude --daily --verbose
    uv run --with anthropic python examples/ai_captain.py --compare

Cost note: a full game is hundreds of steps but only ~100-200 real
decisions after filtering. That is real money on frontier models —
part of the lesson. Cap demos with --max-steps; pick budget models
with --claude-model / --grok-model / --gemini-model.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# ----------------------------------------------------------------------
# The game client: a thin wrapper over two endpoints.

class GameClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.session_id = None

    def _post(self, path, body):
        # A bot plays fast enough to trip the server's own rate limits
        # (600 steps/min per IP) — a real-world lesson: agents must
        # respect 429s with backoff, not crash on them.
        payload = json.dumps(body).encode()
        for _ in range(60):
            req = urllib.request.Request(
                self.base_url + path, data=payload,
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    time.sleep(5)
                    continue
                raise
        raise RuntimeError("server kept rate-limiting for 5 minutes")

    def new_game(self, daily=False):
        data = self._post("/api/new", {"daily": daily})
        self.session_id = data["session_id"]
        return data["event"]

    def step(self, value):
        data = self._post("/api/step", {"session_id": self.session_id,
                                        "value": value})
        return data["event"]


# ----------------------------------------------------------------------
# Level 1: a rules-based captain. No AI anywhere — this is the baseline
# that teaches the loop itself, and the fallback when an AI misfires.

# Rough midpoint value of each commodity, used to judge if a price is
# high or low relative to "normal" (opium, silk, arms, general).
TYPICAL_PRICE = [11000, 1600, 90, 26]

class RulesCaptain:
    name = "rules"
    firm = "Rule Britannia Ltd."

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
            return self.firm

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
# Level 2: an LLM decides. One shared brain, provider-specific dialects.

SYSTEM_PROMPT = """\
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


class LLMCaptain:
    """Shared agent brain: observation building, decision-point
    filtering, output validation. Subclasses implement one method —
    the provider API call."""

    name = "llm"
    firm = "LLM & Co."

    def __init__(self, verbose):
        self.verbose = verbose
        self.fallback = RulesCaptain()         # when the model misfires
        self.ai_calls = 0

    # -- subclasses implement this ------------------------------------
    def _complete(self, system, user):
        """Send one (system, user) exchange, return the reply text."""
        raise NotImplementedError

    # -- shared loop logic ---------------------------------------------
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
            return self.firm

        self.ai_calls += 1
        try:
            reply = self._complete(SYSTEM_PROMPT, self._describe(event,
                                                                 transcript))
        except Exception as exc:               # network blip, rate limit
            if self.verbose:
                print(f"      [{self.name} API error: {exc}; "
                      f"rules fallback]")
            return self.fallback.answer(event, transcript)

        move, _, reason = (reply or "").strip().partition("|")
        move = move.strip().lower()
        if self.verbose and reason.strip():
            print(f"      [{self.name}: {reason.strip()[:100]}]")

        valid = self._validate(move, prompt)
        if valid is not None:
            return valid
        # The model answered something illegal (it happens!) —
        # log it and fall back to the rules captain. Always validate.
        if self.verbose:
            print(f"      [invalid {self.name} move {move!r}; "
                  f"rules fallback]")
        return self.fallback.answer(event, transcript)

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


def _http_json(url, headers, body, retries=2):
    """Tiny JSON POST helper with one retry for transient errors —
    used by the providers we call over raw REST."""
    payload = json.dumps(body).encode()
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json", **headers})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 529) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            detail = exc.read().decode(errors="replace")[:200]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from None


class ClaudeCaptain(LLMCaptain):
    """Anthropic's Claude, via the official SDK."""
    name = "claude"
    firm = "Claude (Anthropic)"

    def __init__(self, model, effort, verbose):
        super().__init__(verbose)
        import anthropic                       # lazy: only for this mode
        # Zero-arg client: resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
        # or an `ant auth login` profile automatically.
        self.client = anthropic.Anthropic()
        if not (getattr(self.client, "api_key", None)
                or getattr(self.client, "auth_token", None)):
            raise RuntimeError("no Anthropic credentials (set "
                               "ANTHROPIC_API_KEY or run `ant auth login`)")
        self.model = model
        self.effort = effort

    def _complete(self, system, user):
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,                   # room for adaptive thinking
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            return ""                          # extremely unlikely here
        return next((b.text for b in response.content
                     if b.type == "text"), "")


class GrokCaptain(LLMCaptain):
    """xAI's Grok, via its (OpenAI-compatible) REST API."""
    name = "grok"
    firm = "Grok (xAI)"

    def __init__(self, model, verbose):
        super().__init__(verbose)
        self.key = os.environ.get("XAI_API_KEY")
        if not self.key:
            raise RuntimeError("XAI_API_KEY is not set")
        self.model = model

    def _complete(self, system, user):
        data = _http_json(
            "https://api.x.ai/v1/chat/completions",
            {"Authorization": f"Bearer {self.key}"},
            {"model": self.model,
             "messages": [{"role": "system", "content": system},
                          {"role": "user", "content": user}],
             "max_tokens": 2000})
        return data["choices"][0]["message"]["content"] or ""


class GeminiCaptain(LLMCaptain):
    """Google's Gemini, via the Generative Language REST API."""
    name = "gemini"
    firm = "Gemini (Google)"

    def __init__(self, model, verbose):
        super().__init__(verbose)
        self.key = (os.environ.get("GEMINI_API_KEY")
                    or os.environ.get("GOOGLE_API_KEY"))
        if not self.key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.model = model

    def _complete(self, system, user):
        data = _http_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent",
            {"x-goog-api-key": self.key},
            {"system_instruction": {"parts": [{"text": system}]},
             "contents": [{"role": "user", "parts": [{"text": user}]}],
             "generationConfig": {"maxOutputTokens": 2000}})
        candidates = data.get("candidates") or []
        if not candidates:                     # safety block or empty
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts)


# ----------------------------------------------------------------------
# The agent loop itself: observe -> reason -> act.

def play(client, captain, max_steps, verbose, daily=False):
    event = client.new_game(daily=daily)
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
            print(f"\n[{captain.name}] GAME OVER after {step} steps: "
                  f"score {prompt['score']:,} ({prompt['rating']})")
            if isinstance(captain, LLMCaptain):
                print(f"[{captain.name}] model calls: {captain.ai_calls} "
                      f"({captain.ai_calls * 100 // step}% of steps — "
                      f"decision-point filtering at work)")
            return {"captain": captain.name, "score": prompt["score"],
                    "rating": prompt["rating"], "steps": step,
                    "ai_calls": getattr(captain, "ai_calls", 0)}

        # REASON: pick a move
        move = captain.answer(event, transcript)
        if verbose and prompt["kind"] not in ("pause", "ack"):
            shown = (prompt.get("text") or "")[:60]
            print(f"{step:4d} [{captain.name}] {shown!r} -> {move!r}")

        # ACT: send it back
        event = client.step(move)
    print(f"\n[{captain.name}] stopped at --max-steps {max_steps} "
          f"(game unfinished).")
    return {"captain": captain.name, "score": None, "rating": "-",
            "steps": max_steps, "ai_calls": getattr(captain, "ai_calls", 0)}


def build_captain(which, args):
    if which == "rules":
        return RulesCaptain()
    if which == "claude":
        return ClaudeCaptain(args.claude_model, args.effort, args.verbose)
    if which == "grok":
        return GrokCaptain(args.grok_model, args.verbose)
    if which == "gemini":
        return GeminiCaptain(args.gemini_model, args.verbose)
    raise ValueError(which)


def main():
    parser = argparse.ArgumentParser(
        description="Play Taipan! through its API with a rules bot or an "
                    "LLM — a teaching example of the agent loop.")
    parser.add_argument("--server", default="http://127.0.0.1:8000",
                        help="game server (default: local)")
    parser.add_argument("--captain",
                        choices=["rules", "claude", "grok", "gemini"],
                        default="rules")
    parser.add_argument("--compare", action="store_true",
                        help="play every provider with a configured key "
                             "(plus the rules baseline) on the same "
                             "daily seed and print a comparison table")
    parser.add_argument("--daily", action="store_true",
                        help="play the daily challenge: same seed for "
                             "every captain today — the fair comparison")
    parser.add_argument("--claude-model", default="claude-opus-4-8",
                        help="Anthropic model (claude-haiku-4-5 = budget)")
    parser.add_argument("--effort", default="low",
                        choices=["low", "medium", "high"],
                        help="Claude thinking effort per decision")
    parser.add_argument("--grok-model", default="grok-4",
                        help="xAI model name")
    parser.add_argument("--gemini-model", default="gemini-2.5-pro",
                        help="Google model name")
    parser.add_argument("--max-steps", type=int, default=5000)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.compare:
        results = []
        for which in ("rules", "claude", "grok", "gemini"):
            try:
                captain = build_captain(which, args)
            except Exception as exc:
                print(f"[{which}] skipped: {exc}")
                continue
            print(f"\n===== {captain.firm} sets sail =====")
            client = GameClient(args.server)
            results.append(play(client, captain, args.max_steps,
                                args.verbose, daily=True))
        if results:
            print("\n===== RESULTS (same daily seed) =====")
            print(f"{'captain':<10} {'score':>12} {'rating':<15} "
                  f"{'steps':>6} {'AI calls':>9}")
            for r in sorted(results,
                            key=lambda r: (r['score'] is None,
                                           -(r['score'] or 0))):
                score = f"{r['score']:,}" if r["score"] is not None else "-"
                print(f"{r['captain']:<10} {score:>12} {r['rating']:<15} "
                      f"{r['steps']:>6} {r['ai_calls']:>9}")
            print("\nThe daily leaderboard in the game UI shows the same "
                  "standings: [scores] on the title screen.")
        return

    try:
        captain = build_captain(args.captain, args)
    except Exception as exc:
        sys.exit(f"Could not create the {args.captain} captain: {exc}")
    client = GameClient(args.server)
    play(client, captain, args.max_steps, args.verbose, daily=args.daily)


if __name__ == "__main__":
    main()
