"""Engine tests: original-game formulas, determinism, and random play."""

import random

import pytest

from taipan.engine import BASE_PRICE, CANCEL, Game, fancy


# ----------------------------------------------------------------------
# Money formatting

@pytest.mark.parametrize("value,expected", [
    (0, "0"),
    (999, "999"),
    (999_999, "999,999"),
    (1_000_000, "1 Million"),
    (2_500_000, "2.5 Million"),
    (1_230_000_000, "1.23 Billion"),
    (5_000_000_000_000, "5 Trillion"),
])
def test_fancy(value, expected):
    assert fancy(value) == expected


# ----------------------------------------------------------------------
# Prices must stay inside the original ranges:
# price = base//2 * (1..3) * unit_multiplier   (BASIC line 2100)

def test_price_ranges():
    g = Game(seed=42)
    for port in range(1, 8):
        g.port = port
        for _ in range(50):
            g.set_prices()
            for i in range(4):
                unit = BASE_PRICE[i][0]
                base = BASE_PRICE[i][port] // 2
                assert base * unit <= g.price[i] <= base * 3 * unit, (
                    f"item {i} port {port}: {g.price[i]}")


# ----------------------------------------------------------------------
# Scoring (BASIC 20000): score = net / 100 / months^1.1

def drain(gen):
    """Run a generator to its first yield and return the event."""
    return next(gen)


@pytest.mark.parametrize("cash,bank,debt,months,rating", [
    (100_000_000, 0, 0, 12, "Ma Tsu"),          # score ~64,766
    (20_000_000, 0, 0, 12, "Master Taipan"),    # score ~12,953
    (3_000_000, 0, 0, 12, "Taipan"),            # score ~1,942
    (1_000_000, 0, 0, 12, "Compradore"),        # score ~647
    (200_000, 0, 0, 12, "Galley Hand"),         # score ~129
    (0, 0, 50_000, 12, "Galley Hand"),          # negative score
])
def test_score_and_rating(cash, bank, debt, months, rating):
    g = Game(seed=1)
    g.cash, g.bank, g.debt = cash, bank, debt
    g.month = ((months - 1) % 12) + 1
    g.year = 1860 + (months - 1) // 12
    ev = drain(g._final_stats())
    expected = int((cash + bank - debt) / 100 / months ** 1.1)
    assert ev["prompt"]["score"] == expected
    assert ev["prompt"]["rating"] == rating
    assert ev["done"] is True


# ----------------------------------------------------------------------
# Determinism: same seed + same inputs => same state. This is what makes
# the server's save/replay persistence correct.

def scripted_run(seed, n_steps=300):
    rng = random.Random(9999)
    g = Game(seed=seed)
    gen = g.run()
    ev = next(gen)
    inputs = []
    for _ in range(n_steps):
        p = ev["prompt"]
        if p["kind"] == "end":
            break
        if p["kind"] == "text":
            v = "Determinism Ltd."
        elif p["kind"] == "choice":
            v = rng.choice(p["options"])["key"]
        elif p["kind"] == "number":
            v = rng.choice(["a", "0", "1", "10", "500"])
        else:
            v = ""
        inputs.append(v)
        try:
            ev = gen.send(v)
        except StopIteration:
            break
    return inputs, ev["state"]


def replay(seed, inputs):
    g = Game(seed=seed)
    gen = g.run()
    ev = next(gen)
    for v in inputs:
        try:
            ev = gen.send(v)
        except StopIteration:
            break
    return ev["state"]


def test_replay_determinism():
    for seed in (7, 123, 55555):
        inputs, final_state = scripted_run(seed)
        assert replay(seed, inputs) == final_state


# ----------------------------------------------------------------------
# Random play: many games must run to completion without violating
# invariants (a compact version of scripts/smoke.py).

def test_random_games_complete():
    for seed in range(30):
        rng = random.Random(seed)
        g = Game(seed=seed)
        gen = g.run()
        ev = next(gen)
        for step in range(20000):
            st = ev["state"]
            assert st["cash"] >= 0 and st["bank"] >= 0 and st["debt"] >= 0
            assert all(v >= 0 for v in st["hold_items"] + st["warehouse"])
            assert st["warehouse_used"] <= 10000
            if st["prices"]:
                assert all(p > 0 for p in st["prices"])
            p = ev["prompt"]
            if p["kind"] == "end":
                break
            if p["kind"] == "text":
                v = "Test Co."
            elif p["kind"] == "choice":
                v = rng.choice(p["options"])["key"]
            elif p["kind"] == "number":
                v = rng.choice(["a", "0", "3", "100", "999999999"])
            else:
                v = ""
            try:
                ev = gen.send(v)
            except StopIteration:
                break
        else:
            pytest.fail(f"game with seed {seed} never ended")


# ----------------------------------------------------------------------
# A couple of specific original behaviours worth pinning down.

def test_start_options():
    """BASIC 10160/10170: the two starting loadouts."""
    for choice, cash, debt, guns, hold, bp in [
            ("1", 400, 5000, 0, 60, 10), ("2", 0, 0, 5, 10, 7)]:
        g = Game(seed=3)
        gen = g.run()
        next(gen)
        gen.send("Test Co.")
        gen.send("1")            # mode: classic
        assert (g.cash, g.debt, g.guns, g.hold, g.bp) != (
            cash, debt, guns, hold, bp)  # not applied until choice made
        gen.send(choice)
        assert (g.cash, g.debt, g.guns, g.hold, g.bp) == (
            cash, debt, guns, hold, bp)


# ----------------------------------------------------------------------
# Modes, daily challenge, and extended-mode rules.

def test_mode_prompt_sets_extended():
    g = Game(seed=3)
    gen = g.run()
    next(gen)
    ev = gen.send("Test Co.")
    assert "How will you sail" in ev["prompt"]["text"]
    gen.send("2")
    assert g.mode == "extended" and g.extended is True


def test_daily_forces_classic_and_skips_mode_prompt():
    g = Game(seed=3, mode="classic", daily="2026-07-05")
    gen = g.run()
    next(gen)
    ev = gen.send("Test Co.")
    assert ev["prompt"]["text"].startswith("Do you want to start")
    assert g.extended is False
    assert g.snapshot()["daily"] == "2026-07-05"


def test_classic_never_reads_extended_rules():
    """Classic prices must stay in the original ranges even at ports
    with an extended-mode opium premium."""
    g = Game(seed=42, mode="classic")
    g.port = 3   # Nagasaki: 1.5x opium premium in extended
    for _ in range(50):
        g.set_prices()
        base, unit = BASE_PRICE[0][3] // 2, BASE_PRICE[0][0]
        assert base * unit <= g.price[0] <= base * 3 * unit
    assert g.wu_rate == 0.10


def test_extended_opium_premium():
    g = Game(seed=42, mode="extended")
    g.port = 3
    lo = int(BASE_PRICE[0][3] // 2 * BASE_PRICE[0][0] * 1.5)
    hi = int(BASE_PRICE[0][3] // 2 * 3 * BASE_PRICE[0][0] * 1.5)
    for _ in range(50):
        g.set_prices()
        assert lo <= g.price[0] <= hi


def test_price_memory_records_ports():
    g = Game(seed=8, mode="classic")
    for port in (1, 4, 6):
        g.port = port
        g.set_prices()
    seen = g.snapshot()["seen_prices"]
    assert [s["port"] for s in seen] == ["Hong Kong", "Saigon",
                                         "Singapore"]
    assert all(len(s["prices"]) == 4 for s in seen)


def test_wu_trust_lowers_rate_in_extended_only():
    for mode, want in (("extended", 0.08), ("classic", 0.10)):
        g = Game(seed=3, mode=mode)
        g.wu_payoffs = 1
        g.debt, g.cash = 100, 1000
        gen = g._elder_brother_wu()
        ev = next(gen)                    # business with Wu?
        ev = gen.send("y")                # -> repay how much?
        ev = gen.send("100")              # clears the debt
        assert "borrow" in ev["prompt"]["text"]
        try:
            gen.send("0")
        except StopIteration:
            pass
        assert g.wu_rate == want


def test_end_event_carries_history_and_stats():
    g = Game(seed=3, mode="classic")
    g.cash = 5000
    ev = next(g._final_stats())
    assert ev["prompt"]["kind"] == "end"
    assert ev["prompt"]["net_history"][-1][1] == 5000
    assert ev["prompt"]["stats"]["battles"] == 0


def test_overload_allowed_but_cannot_sail():
    """Buying beyond hold capacity is original behaviour ('Overload');
    the ship must refuse to sail until fixed."""
    g = Game(seed=3)
    g.hold_ = [0, 0, 0, 200]
    g.hold = -140
    assert g.snapshot()["overloaded"] is True


# ----------------------------------------------------------------------
# ESC cancels input flows and unwinds to the enclosing menu.

def to_port_menu(gen, ev):
    """Answer prompts conservatively until the port menu appears."""
    for _ in range(60):
        p = ev["prompt"]
        if p["kind"] == "choice" and (p["text"] or "").startswith("Shall I"):
            return ev
        if p["kind"] == "text":
            v = "Esc & Co."
        elif p["kind"] == "choice":
            keys = [o["key"] for o in p["options"]]
            v = "1" if "1" in keys and "y" not in keys else "n"
        elif p["kind"] == "number":
            v = "0"
        else:
            v = ""
        ev = gen.send(v)
    raise AssertionError("never reached the port menu")


def start_at_menu(seed=11):
    g = Game(seed=seed)
    gen = g.run()
    ev = to_port_menu(gen, next(gen))
    return g, gen, ev


def is_port_menu(ev):
    p = ev["prompt"]
    return p["kind"] == "choice" and p["text"].startswith("Shall I")


def test_cancel_buy_at_item_and_amount():
    g, gen, ev = start_at_menu()
    cash = g.cash
    ev = gen.send("b")                      # what to buy?
    assert ev["prompt"]["cancellable"] is True
    ev = gen.send(CANCEL)                   # never mind
    assert is_port_menu(ev) and g.cash == cash
    ev = gen.send("b")
    ev = gen.send("o")                      # opium -> how much?
    ev = gen.send(CANCEL)
    assert is_port_menu(ev) and g.cash == cash and sum(g.hold_) == 0


def test_cancel_destination_returns_to_menu():
    g, gen, ev = start_at_menu()
    ev = gen.send("q")                      # quit trading -> where to?
    assert ev["prompt"]["cancellable"] is True
    ev = gen.send(CANCEL)
    assert is_port_menu(ev)
    assert g.port == 1                      # still in Hong Kong


def test_cancel_bank_leaves_balances():
    g, gen, ev = start_at_menu()
    g.cash, g.bank = 5000, 7000
    ev = gen.send("v")                      # deposit?
    ev = gen.send(CANCEL)
    assert is_port_menu(ev)
    assert (g.cash, g.bank) == (5000, 7000)


def test_bank_percentage_presets():
    g, gen, ev = start_at_menu()
    g.cash, g.bank = 1000, 400
    ev = gen.send("v")                      # deposit prompt
    assert ev["prompt"]["presets"] == [
        {"label": "0%", "value": 0},
        {"label": "25%", "value": 250},
        {"label": "50%", "value": 500},
        {"label": "75%", "value": 750}]
    ev = gen.send("250")                    # take the 25% preset
    assert (g.cash, g.bank) == (750, 650)
    assert ev["prompt"]["presets"] == [     # withdraw prompt: % of bank
        {"label": "0%", "value": 0},
        {"label": "25%", "value": 162},
        {"label": "50%", "value": 325},
        {"label": "75%", "value": 487}]
    gen.send("0")
    assert (g.cash, g.bank) == (750, 650)


def test_buy_max_preset_respects_hold():
    g, gen, ev = start_at_menu()
    g.cash = 10 * g.price[3]                # can afford 10 General Cargo
    g.hold = 4                              # but only 4 units of space
    ev = gen.send("b")
    ev = gen.send("g")
    assert ev["prompt"]["presets"] == [{"label": "Max", "value": 4}]
    ev = gen.send("4")                      # take Max
    assert g.hold == 0 and g.hold_[3] == 4
    assert is_port_menu(ev)


def test_buy_max_preset_capped_by_cash():
    g, gen, ev = start_at_menu()
    g.cash = 3 * g.price[0]                 # afford 3 opium
    g.hold = 60
    ev = gen.send("b")
    ev = gen.send("o")
    assert ev["prompt"]["presets"] == [{"label": "Max", "value": 3}]
    gen.send(CANCEL)


def test_transfer_aboard_max_preset_respects_hold():
    g, gen, ev = start_at_menu()
    g.warehouse[0] = 500                    # opium stockpiled ashore
    g.hold = 60
    ev = gen.send("t")                      # transfer cargo
    assert "move aboard ship" in ev["prompt"]["text"]
    assert ev["prompt"]["presets"] == [{"label": "Max", "value": 60}]
    ev = gen.send("60")                     # take Max
    assert g.hold == 0 and g.hold_[0] == 60 and g.warehouse[0] == 440
    assert is_port_menu(ev)


def test_voyage_months_matrix_is_sane():
    from taipan.engine import VOYAGE_MONTHS
    for a in range(1, 8):
        assert VOYAGE_MONTHS[a][a] == 0
        for b in range(1, 8):
            if a != b:
                assert 1 <= VOYAGE_MONTHS[a][b] <= 3
                assert VOYAGE_MONTHS[a][b] == VOYAGE_MONTHS[b][a]


def drive_travel(mode, dest, seed):
    """Run _travel() to completion; returns the game or None if a
    battle/storm interfered with the clean-voyage assumption."""
    g = Game(seed=seed, mode=mode)
    g.debt = 1000
    gen = g._travel()
    ev = next(gen)
    v = dest
    try:
        while True:
            ev = gen.send(v)
            p = ev["prompt"]
            if p["kind"] == "choice":     # battle orders: bad seed
                return None
            v = ""
    except StopIteration:
        pass
    return g


def test_extended_voyage_takes_distance_months():
    for seed in range(50):
        g = drive_travel("extended", "7", seed)   # HK -> Batavia: 2 mo
        if g and g.port == 7:                     # not blown off course
            assert g.month == 3                   # Jan + 2 months
            assert g.debt == 1210                 # 10% compounded twice
            return
    raise AssertionError("no clean voyage found in 50 seeds")


def test_classic_voyage_is_always_one_month():
    for seed in range(50):
        g = drive_travel("classic", "7", seed)
        if g and g.port == 7:
            assert g.month == 2                   # a single month
            assert g.debt == 1100
            return
    raise AssertionError("no clean voyage found in 50 seeds")


def test_extended_caps_fleet_size_classic_does_not():
    from taipan.engine import (GENERIC, LI_YUEN, MAX_GENERIC_FLEET,
                               MAX_LI_YUEN_FLEET)
    big_classic, big_li = 0, 0
    for mode in ("classic", "extended"):
        g = Game(seed=99, mode=mode)
        g.capacity, g.guns = 5000, 20    # a late-game leviathan
        for _ in range(300):
            n_gen = g._fleet_size(GENERIC)
            n_li = g._fleet_size(LI_YUEN)
            if mode == "extended":
                assert n_gen <= MAX_GENERIC_FLEET
                assert n_li <= MAX_LI_YUEN_FLEET
            else:
                big_classic = max(big_classic, n_gen)
                big_li = max(big_li, n_li)
    # classic keeps the original unbounded scaling
    assert big_classic > MAX_GENERIC_FLEET
    assert big_li > MAX_LI_YUEN_FLEET


def test_retire_requires_confirmation():
    g, gen, ev = start_at_menu()
    g.cash = 2_000_000
    ev = gen.send("v")                      # cycle the bank to rebuild
    ev = gen.send("0")                      # the menu with Retire in it
    ev = gen.send("0")
    retire = [o for o in ev["prompt"]["options"] if o["key"] == "r"]
    assert retire and retire[0]["danger"] is True
    ev = gen.send("r")                      # -> are you sure?
    assert "Retire" in ev["prompt"]["text"]
    ev = gen.send("n")                      # changed my mind
    assert is_port_menu(ev)
    assert g.cash == 2_000_000
    ev = gen.send("r")
    ev = gen.send("y")                      # millionaire screen (pause)
    ev = gen.send("")                       # -> final stats
    assert ev["prompt"]["kind"] == "end"


def drain_gen(gen, answers):
    """Drive a sub-generator with scripted answers; returns events."""
    events = [next(gen)]
    try:
        for a in answers:
            events.append(gen.send(a))
    except StopIteration:
        pass
    return events


def test_charter_delivery_pays_bonus():
    g = Game(seed=7, mode="extended")
    g.port = 3
    g.hold_[1] = 200
    g.charter = {"item": 1, "qty": 150, "dest": 3, "due": g.time + 2,
                 "bonus": 9000}
    cash = g.cash
    gen = g._check_charter()
    ev = next(gen)                          # delivery message pause
    assert any("charter is fulfilled" in (m.get("text") or "")
               for m in ev["messages"])
    assert g.cash == cash + 9000
    assert g.hold_[1] == 50 and g.charter is None
    assert g.stats["charters_done"] == 1


def test_charter_expires():
    g = Game(seed=7, mode="extended")
    g.port = 2
    g.charter = {"item": 0, "qty": 100, "dest": 5, "due": g.time - 1,
                 "bonus": 5000}
    gen = g._check_charter()
    next(gen)
    assert g.charter is None
    assert g.stats["charters_failed"] == 1


def test_dockyard_buys_refit():
    from taipan.engine import REFITS
    g = Game(seed=7, mode="extended")
    g.cash = 500_000
    gen = g._dockyard()
    ev = next(gen)
    labels = [o["label"] for o in ev["prompt"]["options"]]
    assert any("Copper" in x for x in labels)
    assert labels[-1] == "Leave"
    gen.send("1")                           # buy the first refit
    first = sorted(k for k in REFITS if k in g.refits)
    assert len(g.refits) == 1
    assert g.cash < 500_000
    assert g.snapshot()["refits"] == [REFITS[first[0]][0]]


def test_achievements_from_stats():
    g = Game(seed=7, mode="extended")
    g.stats["ships_sunk"] = 120
    g.stats["storms"] = 6
    g.li_donations = 3
    g.wu_trusted = True
    g.feng_survived = True
    g.max_warehouse = 10000
    g.stats["charters_done"] = 3
    g.refits.add("figurehead")
    ids = {a["id"] for a in g._achievements(60000, "Ma Tsu", 80_000_000)}
    assert {"ma_tsu", "scourge", "fleet_friend", "wus_word",
            "fengs_bane", "storm_rider", "godown_full",
            "charter_master", "figurehead"} <= ids
    # a fresh game earns nothing
    g2 = Game(seed=8)
    assert g2._achievements(100, "Galley Hand", 10_000) == []


def test_end_event_carries_journal_and_achievements():
    g = Game(seed=7, mode="classic")
    g.log_event("Test entry.")
    ev = next(g._final_stats())
    assert ev["prompt"]["journal"][0]["text"] == "Test entry."
    assert "achievements" in ev["prompt"]


def test_ack_prompt_carries_message_lines():
    """Loss events yield an 'ack' prompt (modal with OK) that carries
    the message lines so it survives a refresh."""
    g = Game(seed=3)
    g.say("Bad Joss!!", cls="warn")
    g.say("The local authorities have seized your Opium cargo.")
    ev = next(g._ack())
    assert ev["prompt"]["kind"] == "ack"
    assert ev["prompt"]["lines"] == [
        {"text": "Bad Joss!!", "cls": "warn"},
        {"text": "The local authorities have seized your Opium cargo.",
         "cls": "normal"}]
    # the same lines also went out as normal log messages
    assert [m["text"] for m in ev["messages"]] == [
        "Bad Joss!!",
        "The local authorities have seized your Opium cargo."]


def test_bank_presets_skip_zero_and_duplicates():
    assert Game._pct_presets(0) == [{"label": "0%", "value": 0}]
    assert Game._pct_presets(2) == [{"label": "0%", "value": 0},
                                    {"label": "50%", "value": 1}]


def test_wu_bailout_refuses_esc():
    """The bailout question ends the game on No, so ESC must re-ask
    rather than count as No."""
    g = Game(seed=5)
    gen = g._ask_yn("Are you willing?", esc_is_no=False)
    ev = next(gen)
    assert ev["prompt"]["cancellable"] is False
    ev = gen.send(CANCEL)                   # ignored: asked again
    assert ev["prompt"]["kind"] == "choice"
    with pytest.raises(StopIteration) as stop:
        gen.send("n")
    assert stop.value.value is False


def test_esc_counts_as_no_on_offers():
    g = Game(seed=5)
    gen = g._ask_yn("Buy a gun?")
    next(gen)
    with pytest.raises(StopIteration) as stop:
        gen.send(CANCEL)
    assert stop.value.value is False
