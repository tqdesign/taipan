# Teaching AI with Taipan!: the agent loop

This folder shows how to make **another program play the game through
its backend API — with an AI making the decisions**. It's a compact,
complete example of how every AI agent works:

```
        ┌────────────────────────────────────────────┐
        │                                            │
        ▼                                            │
   OBSERVE                REASON                 ACT │
   GET the game    ──►    pick a move    ──►    POST the move
   state + prompt         (rules or AI)         to /api/step
```

The game is an unusually good classroom for this because the API
already speaks "agent": every response contains the full **state**
(cash, cargo, prices, date...), the **messages** since your last move,
and a **prompt** object that *enumerates the legal actions*. There is
nothing to scrape and nothing to guess:

```json
{
  "state":   { "cash": 400, "location": "Hong Kong", "prices": [...] },
  "messages": [ {"text": "Li Yuen asks 195 in donation..."} ],
  "prompt":  { "kind": "choice",
               "text": "Will you pay?",
               "options": [ {"key": "y", "label": "Yes"},
                            {"key": "n", "label": "No"} ] }
}
```

Two endpoints drive everything:

| Endpoint | Purpose |
|---|---|
| `POST /api/new` | start a game, returns `session_id` + first event |
| `POST /api/step` `{session_id, value}` | make a move, returns the next event |

## Running it

Start the game server in one terminal:

```sh
uv run main.py
```

**Level 1 — the rules captain (no AI, free):**

```sh
uv run python examples/ai_captain.py --captain rules --verbose
```

Hard-coded heuristics: sell what's pricey, buy what's cheap, fight
when armed, pay Li Yuen, repay Wu. It teaches the *loop* — and it's
the baseline the AI must beat.

**Level 2 — an LLM as captain.** Three providers are wired in, each
using its own API key:

| Captain | Provider | API key env var | Model flag (default) |
|---|---|---|---|
| `--captain claude` | Anthropic | `ANTHROPIC_API_KEY` (or `ant auth login`) | `--claude-model claude-opus-4-8` |
| `--captain grok`   | xAI       | `XAI_API_KEY`     | `--grok-model grok-4` |
| `--captain gemini` | Google    | `GEMINI_API_KEY`  | `--gemini-model gemini-2.5-pro` |

```sh
uv run --with anthropic python examples/ai_captain.py \
    --captain claude --verbose
uv run python examples/ai_captain.py --captain grok --verbose
uv run python examples/ai_captain.py --captain gemini --verbose
```

Every real decision goes to the model, which answers with a move and a
one-line reason you'll see in the log. All three captains share the
same brain — observation building, validation, filtering — and differ
*only* in the API call (Claude via the official SDK; Grok and Gemini
via their own REST APIs, no extra dependencies). That's a lesson in
itself: the agent loop is provider-agnostic even though every provider
speaks a different dialect.

## Comparing the models

```sh
uv run --with anthropic python examples/ai_captain.py --compare
```

`--compare` plays one game per provider you have a key for (plus the
rules baseline), **all on the same seed** via the game's daily
challenge, and prints a table:

```
===== RESULTS (same daily seed) =====
captain         score rating           steps  AI calls
claude         12,405 Master Taipan      612       142
gemini          3,180 Taipan             548       131
grok            1,022 Taipan             590       137
rules            -425 Galley Hand        401         0
```

Same starting prices, same world — score differences are strategy, not
luck. The game's own **daily leaderboard** shows the identical
standings (each captain signs its firm name as "Claude (Anthropic)",
"Grok (xAI)", "Gemini (Google)"), so students can watch the benchmark
on the game's title screen via [scores]. For single fair runs, add
`--daily` to any `--captain` command.

The model-name defaults age quickly — check each provider's docs and
pass the current flagship (or budget) model via the flags.

## The lessons baked into the code

1. **Decision-point filtering** (the big one). Most steps are pauses,
   acknowledgements, and bookkeeping. The agent answers those with two
   lines of local code and reserves model calls for real decisions —
   the difference between ~150 calls and ~700 per game. The end-of-game
   report prints the ratio. *Don't spend tokens on trivia* is the most
   transferable habit in agent building.

2. **Validate every model output.** The model occasionally answers
   something illegal ("x" to a yes/no question). `_validate()` maps
   output onto the legal move set and falls back to the rules captain
   when it can't. An agent without output validation is a crash
   waiting to happen.

3. **Observation design matters.** `_describe()` chooses what the
   model sees: state, prices, the market log (memory!), recent events,
   the legal moves. Better observations beat cleverer prompts.

4. **Cost is a design axis.** `--effort low` is fast and cheap;
   `--effort high` plays noticeably better and costs more. The
   `--model` flag makes the tradeoff concrete: try
   `claude-haiku-4-5` and compare scores.

## Costs, and classroom logistics

- A full game is several hundred steps but only ~100–200 model calls
  after filtering. At default settings expect a few dollars per game;
  with `claude-haiku-4-5` a fraction of that. `--max-steps 200` caps a
  demo.
- **Run against a local server**, not the public site: a classroom
  behind one IP will trip the public rate limits (12 new games/min),
  and bot scores would pollute the public hall of fame. Locally,
  everyone gets their own ocean and their own leaderboard.

## Exercises (level 3 and beyond)

- **Memory**: give the captain a scratchpad — a string it can update
  each turn ("plan: buy silk in HK, sell in Saigon") that gets fed
  back into the next prompt. Watch multi-voyage plans emerge.
- **Benchmarking**: play the same *challenge link* (which fixes the
  seed) with two different prompts, or two different models, and
  compare scores. Deterministic seeds turn strategy differences into
  measurable numbers.
- **Prompt engineering**: the strategy notes in `CLAUDE_SYSTEM` are
  deliberately basic. Can you write a system prompt that beats the
  rules captain reliably? That retires by 1865?
- **Cheaper triage**: use a cheap model for routine port visits and a
  strong one only for battles and Wu negotiations — a two-model agent.
- **Full autonomy**: wrap the loop to play N games overnight and graph
  the score distribution per prompt variant.
