"""Taipan! game engine.

A faithful port of Art Canfil's Taipan! (Apple II, 1982), based on the
original Applesoft BASIC listing (reference/taipan-original.bas) with
message text and a few clarified formulas taken from Jay Link's C port
(reference/taipan-c-port.c).

The game runs as a generator: Game.run() yields an "event" dict whenever
it needs player input, and receives the player's answer via .send().
Each event carries the messages printed since the last input, a snapshot
of the game state, the active sea-battle state (if any), and a prompt
descriptor telling the client what kind of input is expected.
"""

from __future__ import annotations

import random

GENERIC = 1
LI_YUEN = 2

# Sent by the client when the player presses ESC on a cancellable
# prompt; helpers then return None and the calling flow unwinds.
CANCEL = "\x1b"

BATTLE_NOT_FINISHED = 0
BATTLE_WON = 1
BATTLE_INTERRUPTED = 2
BATTLE_FLED = 3
BATTLE_LOST = 4

ITEMS = ["Opium", "Silk", "Arms", "General Cargo"]
LOCATIONS = ["At sea", "Hong Kong", "Shanghai", "Nagasaki", "Saigon",
             "Manila", "Singapore", "Batavia"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
STATUS_LABELS = ["Critical", "Poor", "Fair", "Good", "Prime", "Perfect"]

# BASE_PRICE[item] = [unit multiplier, then base price per port 1-7]
BASE_PRICE = [
    [1000, 11, 16, 15, 14, 12, 10, 13],   # Opium
    [100,  11, 14, 15, 16, 10, 13, 12],   # Silk
    [10,   12, 16, 10, 11, 13, 14, 15],   # Arms
    [1,    10, 11, 12, 13, 14, 15, 16],   # General Cargo
]

WAREHOUSE_CAPACITY = 10000


class _GameOver(Exception):
    """Raised anywhere in the flow to unwind to the final stats screen."""


def fancy(num) -> str:
    """Format money the way the original does: big numbers get a word."""
    n = int(num)
    for div, unit in ((1_000_000_000_000, "Trillion"),
                      (1_000_000_000, "Billion"),
                      (1_000_000, "Million")):
        if n >= div:
            s = f"{n / div:.2f}".rstrip("0").rstrip(".")
            return f"{s} {unit}"
    return f"{n:,}"


class Game:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.firm = "Taipan"
        self.cash = 0
        self.bank = 0
        self.debt = 0
        self.ec = 20.0            # base enemy health; grows each year
        self.ed = 0.5             # enemy damage factor; grows each year
        self.base = [row[:] for row in BASE_PRICE]
        self.price = [0, 0, 0, 0]
        self.warehouse = [0, 0, 0, 0]
        self.hold_ = [0, 0, 0, 0]
        self.hold = 0             # free space; negative means overloaded
        self.capacity = 60
        self.guns = 0
        self.bp = 10              # 1-in-bp chance of hostiles per voyage
        self.damage = 0.0
        self.month = 1
        self.year = 1860
        self.li = 0               # Li Yuen protection counter
        self.port = 1
        self.dest = 0
        self.wu_warn = False
        self.wu_bailout = 0
        self.booty = 0
        self.battle = None
        self.ended = False
        self._msgs = []
        self.set_prices()

    # ------------------------------------------------------------------
    # BASIC's FN R(X) = INT(RND * X)
    def r(self, x) -> int:
        if x <= 0:
            return 0
        return int(self.rng.random() * x)

    def rand01(self) -> float:
        return self.rng.random()

    @property
    def time(self) -> int:
        """Months elapsed (TI in the BASIC source; starts at 1)."""
        return (self.year - 1860) * 12 + self.month

    # ------------------------------------------------------------------
    # Output/event plumbing
    def say(self, text, cls="normal"):
        self._msgs.append({"text": text, "cls": cls})

    def head(self, text):
        self.say(text, cls="head")

    def fx(self, kind, slot=None, **extra):
        self._msgs.append({"fx": kind, "slot": slot, **extra})

    def _event(self, prompt):
        ev = {
            "messages": self._msgs,
            "prompt": prompt,
            "state": self.snapshot(),
            "battle": self.battle_snapshot(),
            "done": self.ended,
        }
        self._msgs = []
        return ev

    def _pause(self, ms=1800):
        yield self._event({"kind": "pause", "timeout": ms})

    def _ask_choice(self, text, options, default=None, cancellable=False):
        keys = [o["key"] for o in options]
        while True:
            v = yield self._event({"kind": "choice", "text": text,
                                   "options": options,
                                   "cancellable": cancellable})
            if cancellable and v == CANCEL:
                return None
            v = (v or "").strip().lower()
            if v == "" and default is not None:
                return default
            if v in keys:
                return v

    def _ask_yn(self, text, esc_is_no=True):
        """Yes/No question. ESC counts as No, except for questions with
        irreversible stakes (esc_is_no=False), which insist on Y or N."""
        c = yield from self._ask_choice(text, [{"key": "y", "label": "Yes"},
                                               {"key": "n", "label": "No"}],
                                        cancellable=esc_is_no)
        return c == "y"

    def _ask_num(self, text, hint=None, allow_all=True, cancellable=True):
        """Number entry; 'A' means All, like the original. Returns None
        if the player cancels."""
        while True:
            v = yield self._event({"kind": "number", "text": text,
                                   "hint": hint, "allow_all": allow_all,
                                   "cancellable": cancellable})
            if cancellable and v == CANCEL:
                return None
            s = (v or "").strip().lower()
            if allow_all and s in ("a", "all", "*"):
                return -1
            if s.isdigit():
                return int(s)

    def _ask_item(self, text):
        options = [{"key": k, "label": name}
                   for k, name in zip("osag", ITEMS)]
        c = yield from self._ask_choice(text, options, cancellable=True)
        return None if c is None else "osag".index(c)

    def _ask_text(self, text, maxlen=22):
        while True:
            v = yield self._event({"kind": "text", "text": text,
                                   "maxlen": maxlen})
            v = (v or "").strip()
            if 0 < len(v) <= maxlen:
                return v

    # ------------------------------------------------------------------
    # Snapshots
    def snapshot(self):
        pct = max(0, 100 - int(self.damage / self.capacity * 100))
        in_use = sum(self.warehouse)
        return {
            "firm": self.firm,
            "month": MONTHS[self.month - 1],
            "year": self.year,
            "location": LOCATIONS[self.port],
            "destination": LOCATIONS[self.dest] if self.port == 0 else None,
            "cash": int(self.cash), "cash_str": fancy(self.cash),
            "bank": int(self.bank), "bank_str": fancy(self.bank),
            "debt": int(self.debt), "debt_str": fancy(self.debt),
            "items": ITEMS,
            "hold_items": self.hold_[:],
            "warehouse": self.warehouse[:],
            "warehouse_used": in_use,
            "warehouse_vacant": WAREHOUSE_CAPACITY - in_use,
            "hold_space": self.hold,
            "overloaded": self.hold < 0,
            "guns": self.guns,
            "capacity": self.capacity,
            "status_pct": pct,
            "status_label": STATUS_LABELS[min(5, pct // 20)],
            "prices": self.price[:] if self.port != 0 else None,
            "net": int(self.cash + self.bank - self.debt),
        }

    def battle_snapshot(self):
        if not self.battle:
            return None
        b = self.battle
        pct = max(0, 100 - int(self.damage / self.capacity * 100))
        on_screen = sum(1 for hp in b["slots"] if hp > 0)
        return {
            "ships": b["ships"],
            "orders": b["orders_label"],
            "guns": self.guns,
            "slots": [1 if hp > 0 else 0 for hp in b["slots"]],
            "more": b["ships"] > on_screen,
            "status_pct": pct,
            "status_label": STATUS_LABELS[min(5, pct // 20)],
            "hold_items": self.hold_[:],
        }

    # ------------------------------------------------------------------
    # Game start
    def run(self):
        self.firm = yield from self._ask_text(
            "Taipan, what will you name your Firm?")
        c = yield from self._ask_choice(
            "Do you want to start . . .",
            [{"key": "1", "label": "With cash (and a debt)"},
             {"key": "2", "label": "With five guns and no cash (but no debt!)"}])
        if c == "1":
            self.cash, self.debt = 400, 5000
            self.hold, self.guns = 60, 0
            self.li, self.bp = 0, 10
        else:
            self.cash, self.debt = 0, 0
            self.hold, self.guns = 10, 5
            self.li, self.bp = 1, 7

        try:
            while True:
                yield from self._arrival_events()
                while True:
                    yield from self._port_menu()
                    # Destination prompt may be cancelled (ESC): back to
                    # the port menu without re-running arrival events.
                    if (yield from self._travel()):
                        break
        except _GameOver:
            pass
        yield from self._final_stats()

    def set_prices(self):
        for i in range(4):
            self.price[i] = (self.base[i][self.port] // 2
                             * (self.r(3) + 1) * self.base[i][0])

    # ------------------------------------------------------------------
    # Port arrival events (BASIC 1000-2501)
    def _arrival_events(self):
        if self.port == 1:
            if self.li == 0 and self.cash > 0:
                yield from self._li_yuen_extortion()
            if self.damage > 0:
                yield from self._mchenry()
            if self.debt >= 10000 and not self.wu_warn:
                self.wu_warn = True
                self.head("Comprador's Report")
                self.say(f"Elder Brother Wu has sent {self.r(100) + 50} "
                         f"braves to escort you to the Wu mansion, "
                         f"{self.firm}.")
                yield from self._pause()
                self.say("Elder Brother Wu reminds you of the Confucian "
                         "ideal of personal worthiness, and how this "
                         "applies to paying one's debts.")
                yield from self._pause(2600)
                self.say("He is reminded of a fabled barbarian who came "
                         "to a bad end, after not caring for his "
                         "obligations.")
                self.say(f"He hopes no such fate awaits you, his friend, "
                         f"{self.firm}.")
                yield from self._pause(3200)
            yield from self._elder_brother_wu()

        t = self.time
        # Trade-in offer for a bigger ship (BASIC 1610)
        amount = (int(1000 + self.r(1000 * (t + 5) / 6))
                  * ((self.capacity // 50) * (self.damage > 0) + 1))
        if self.cash >= amount and self.r(4) == 0:
            yield from self._new_ship(amount)

        # Offer of a ship's gun (BASIC 1710)
        amount = self.r(1000 * (t + 5) / 6) + 500
        if self.cash >= amount and self.r(3) == 0:
            yield from self._new_gun(amount)

        # Opium seizure outside Hong Kong (BASIC 1900)
        if self.port != 1 and self.hold_[0] > 0 and self.r(18) == 0:
            fine = int(self.rand01() * self.cash / 1.8) + 1 if self.cash > 0 else 0
            self.hold += self.hold_[0]
            self.hold_[0] = 0
            self.cash -= fine
            self.head("Comprador's Report")
            self.say("Bad Joss!!", cls="warn")
            if fine > 0:
                self.say(f"The local authorities have seized your Opium "
                         f"cargo and have also fined you {fancy(fine)}, "
                         f"{self.firm}!")
            else:
                self.say(f"The local authorities have seized your Opium "
                         f"cargo, {self.firm}!")
            yield from self._pause(2600)

        # Warehouse theft (BASIC 2000)
        if sum(self.warehouse) > 0 and self.r(50) == 0:
            for i in range(4):
                self.warehouse[i] = int(self.warehouse[i] / 1.8
                                        * self.rand01())
            self.head("Comprador's Report")
            self.say(f"Messenger reports large theft from warehouse, "
                     f"{self.firm}.", cls="warn")
            yield from self._pause(2600)

        self.set_prices()

        # Li Yuen's protection wears off over time (C port behaviour)
        if self.r(20) == 0 and self.li > 0:
            self.li += 1
            if self.li == 4:
                self.li = 0

        if self.port != 1 and self.li == 0 and self.r(4) != 0:
            self.head("Comprador's Report")
            self.say(f"Li Yuen has sent a Lieutenant, {self.firm}. He says "
                     f"his admiral wishes to see you in Hong Kong, "
                     f"posthaste!")
            yield from self._pause()

        # Sudden price change (BASIC 2410)
        if self.r(9) == 0:
            i = self.r(4)
            self.head("Comprador's Report")
            if self.r(2) == 0:
                self.price[i] = self.price[i] // 5
                self.say(f"{self.firm}!!  The price of {ITEMS[i]} has "
                         f"dropped to {self.price[i]}!!")
            else:
                self.price[i] = self.price[i] * (self.r(5) + 5)
                self.say(f"{self.firm}!!  The price of {ITEMS[i]} has "
                         f"risen to {fancy(self.price[i])}!!")
            yield from self._pause()

        # Mugging when carrying too much cash (BASIC 2501)
        if self.cash > 25000 and self.r(20) == 0:
            robbed = int(self.rand01() * self.cash / 1.4)
            self.cash -= robbed
            self.head("Comprador's Report")
            self.say("Bad Joss!!", cls="warn")
            self.say(f"You've been beaten up and robbed of {fancy(robbed)} "
                     f"in cash, {self.firm}!!")
            yield from self._pause(2600)

    def _li_yuen_extortion(self):
        t = self.time
        if t > 12:
            j = self.r(1000 * t) + 1000 * t
            amount = int(self.rand01() * self.cash) + j
        else:
            amount = int(self.rand01() * self.cash / 1.8)
        if amount <= 0:
            return
        self.head("Comprador's Report")
        self.say(f"Li Yuen asks {fancy(amount)} in donation to the temple "
                 f"of Tin Hau, the Sea Goddess.")
        if not (yield from self._ask_yn("Will you pay?")):
            return
        if amount <= self.cash:
            self.cash -= amount
            self.li = 1
            return
        self.say(f"{self.firm}, you do not have enough cash!!", cls="warn")
        if (yield from self._ask_yn(
                "Do you want Elder Brother Wu to make up the difference "
                "for you?")):
            self.debt += amount - self.cash
            self.cash = 0
            self.li = 1
            self.say("Elder Brother has given Li Yuen the difference "
                     "between what he wanted and your cash on hand and "
                     "added the same amount to your debt.")
        else:
            self.cash = 0
            self.say("Very well. Elder Brother Wu will not pay Li Yuen the "
                     "difference.  I would be very wary of pirates if I "
                     "were you, " + self.firm + ".")
        yield from self._pause(3000)

    def _mchenry(self):
        self.head("Comprador's Report")
        self.say(f'{self.firm}, Mc Henry from the Hong Kong Shipyards has '
                 f'arrived!!  He says, "I see ye\'ve a wee bit of damage '
                 f'to yer ship.')
        if not (yield from self._ask_yn('Will ye be wanting repairs?"')):
            return
        t = self.time
        percent = int(self.damage / self.capacity * 100 + 0.5)
        br = int((self.rand01() * (60 * (t + 3) / 4) + 25 * (t + 3) / 4)
                 * self.capacity / 50)
        br = max(br, 1)
        full = br * int(self.damage) + 1
        while True:
            amount = yield from self._ask_num(
                f"Och, 'tis a pity to be {percent}% damaged. We can fix "
                f"yer whole ship for {fancy(full)}, or make partial "
                f"repairs if you wish. How much will ye spend?",
                hint=f"Full repairs: {fancy(full)} - You have "
                     f"{fancy(self.cash)} in cash")
            if amount is None:
                return
            if amount == -1:
                amount = min(full, self.cash)
            if amount > self.cash:
                self.say(f"{self.firm}, you have only {fancy(self.cash)} "
                         f"in cash.", cls="warn")
                continue
            break
        self.cash -= amount
        self.damage = max(0, int(self.damage) - int(amount / br + 0.5))

    def _elder_brother_wu(self):
        self.head("Comprador's Report")
        if not (yield from self._ask_yn(
                "Do you have business with Elder Brother Wu, the "
                "moneylender?")):
            return
        broke = (int(self.cash) == 0 and int(self.bank) == 0
                 and self.guns == 0 and sum(self.hold_) == 0
                 and sum(self.warehouse) == 0)
        if broke:
            self.wu_bailout += 1
            i = self.r(1500) + 500
            j = self.r(2000) * self.wu_bailout + 1500
            if not (yield from self._ask_yn(
                    f"Elder Brother is aware of your plight, {self.firm}. "
                    f"He is willing to loan you an additional {i} if you "
                    f"will pay back {j}. Are you willing, {self.firm}?",
                    esc_is_no=False)):  # refusing ends the game: no ESC
                self.say(f"Very well, {self.firm}, the game is over!",
                         cls="warn")
                yield from self._pause(2600)
                raise _GameOver
            self.cash += i
            self.debt += j
            self.say(f"Very well, {self.firm}.  Good joss!!")
            yield from self._pause()
        else:
            if self.debt > 0 and self.cash > 0:
                while True:
                    amount = yield from self._ask_num(
                        "How much do you wish to repay him?",
                        hint=f"You owe {fancy(self.debt)} - You have "
                             f"{fancy(self.cash)} in cash")
                    if amount is None:
                        return
                    if amount == -1:
                        amount = min(self.cash, self.debt)
                    if amount > self.cash:
                        self.say(f"{self.firm}, you have only "
                                 f"{fancy(self.cash)} in cash.", cls="warn")
                        continue
                    amount = min(amount, self.debt)
                    self.cash -= amount
                    self.debt -= amount
                    break
            while True:
                amount = yield from self._ask_num(
                    "How much do you wish to borrow?",
                    hint=f"He will loan you up to {fancy(self.cash * 2)}")
                if amount is None:
                    return
                if amount == -1:
                    amount = self.cash * 2
                if amount > self.cash * 2:
                    self.say(f"He won't loan you so much, {self.firm}!",
                             cls="warn")
                    continue
                self.cash += amount
                self.debt += amount
                break

        # Cutthroats prey on the deeply indebted (BASIC 1460)
        if self.debt > 20000 and self.cash > 0 and self.r(5) == 0:
            num = self.r(3) + 1
            self.cash = 0
            self.say("Bad joss!!", cls="warn")
            self.say(f"{num} of your bodyguards have been killed by "
                     f"cutthroats and you have been robbed of all of your "
                     f"cash, {self.firm}!!")
            yield from self._pause(3000)

    def _new_ship(self, amount):
        self.head("Comprador's Report")
        cond = "damaged" if self.damage > 0 else "fine"
        if (yield from self._ask_yn(
                f"Do you wish to trade in your {cond} ship for one with "
                f"50 more capacity by paying an additional "
                f"{fancy(amount)}, {self.firm}?")):
            self.cash -= amount
            self.hold += 50
            self.capacity += 50
            self.damage = 0

    def _new_gun(self, amount):
        self.head("Comprador's Report")
        if (yield from self._ask_yn(
                f"Do you wish to buy a ship's gun for {fancy(amount)}, "
                f"{self.firm}?")):
            if self.hold < 10:
                self.say(f"Your ship would be overburdened, {self.firm}!",
                         cls="warn")
                yield from self._pause()
            else:
                self.cash -= amount
                self.hold -= 10
                self.guns += 1

    # ------------------------------------------------------------------
    # Port menu (BASIC 2510-2698)
    def _port_menu(self):
        while True:
            if self.port == 1:
                options = [{"key": "b", "label": "Buy"},
                           {"key": "s", "label": "Sell"},
                           {"key": "v", "label": "Visit bank"},
                           {"key": "t", "label": "Transfer cargo"},
                           {"key": "w", "label": "Wheedle Wu"},
                           {"key": "q", "label": "Quit trading"}]
                if self.cash + self.bank - self.debt >= 1_000_000:
                    options.append({"key": "r", "label": "Retire"})
            else:
                options = [{"key": "b", "label": "Buy"},
                           {"key": "s", "label": "Sell"},
                           {"key": "q", "label": "Quit trading"}]
            labels = ", ".join(o["label"] for o in options[:-1])
            c = yield from self._ask_choice(
                f"Shall I {labels}, or {options[-1]['label']}?", options)
            if c == "b":
                yield from self._buy()
            elif c == "s":
                yield from self._sell()
            elif c == "v":
                yield from self._visit_bank()
            elif c == "t":
                yield from self._transfer()
            elif c == "w":
                yield from self._elder_brother_wu()
            elif c == "r":
                yield from self._retire()
            elif c == "q":
                if self.hold < 0:
                    self.head("Comprador's Report")
                    self.say(f"Your ship is overloaded, {self.firm}!!",
                             cls="warn")
                    yield from self._pause()
                else:
                    return

    def _buy(self):
        i = yield from self._ask_item(
            f"What do you wish me to buy, {self.firm}?")
        if i is None:
            return
        afford = int(self.cash) // self.price[i]
        while True:
            amount = yield from self._ask_num(
                f"How much {ITEMS[i]} shall I buy, {self.firm}?",
                hint=f"You can afford {afford:,}")
            if amount is None:
                return
            if amount == -1:
                amount = afford
            if amount <= afford:
                break
        self.cash -= amount * self.price[i]
        self.hold_[i] += amount
        self.hold -= amount

    def _sell(self):
        i = yield from self._ask_item(
            f"What do you wish me to sell, {self.firm}?")
        if i is None:
            return
        while True:
            amount = yield from self._ask_num(
                f"How much {ITEMS[i]} shall I sell, {self.firm}?",
                hint=f"You have {self.hold_[i]:,}")
            if amount is None:
                return
            if amount == -1:
                amount = self.hold_[i]
            if amount <= self.hold_[i]:
                break
        self.hold_[i] -= amount
        self.cash += amount * self.price[i]
        self.hold += amount

    def _visit_bank(self):
        while True:
            amount = yield from self._ask_num(
                "How much will you deposit?",
                hint=f"You have {fancy(self.cash)} in cash")
            if amount is None:
                return
            if amount == -1:
                amount = self.cash
            if amount <= self.cash:
                self.cash -= amount
                self.bank += amount
                break
            self.say(f"{self.firm}, you only have {fancy(self.cash)} "
                     f"in cash.", cls="warn")
        while True:
            amount = yield from self._ask_num(
                "How much will you withdraw?",
                hint=f"You have {fancy(self.bank)} in the bank")
            if amount is None:
                return
            if amount == -1:
                amount = self.bank
            if amount <= self.bank:
                self.bank -= amount
                self.cash += amount
                break
            self.say(f"{self.firm}, you only have {fancy(self.bank)} "
                     f"in the bank.", cls="warn")

    def _transfer(self):
        if sum(self.hold_) == 0 and sum(self.warehouse) == 0:
            self.say(f"You have no cargo, {self.firm}.")
            yield from self._pause()
            return
        for i in range(4):
            if self.hold_[i] > 0:
                while True:
                    vacant = WAREHOUSE_CAPACITY - sum(self.warehouse)
                    amount = yield from self._ask_num(
                        f"How much {ITEMS[i]} shall I move to the "
                        f"warehouse, {self.firm}?",
                        hint=f"Aboard: {self.hold_[i]:,} - Warehouse space: "
                             f"{vacant:,}")
                    if amount is None:
                        return
                    if amount == -1:
                        amount = min(self.hold_[i], vacant)
                    if amount > self.hold_[i]:
                        self.say(f"You have only {self.hold_[i]}, "
                                 f"{self.firm}.", cls="warn")
                        continue
                    if amount > vacant:
                        if vacant == 0:
                            self.say(f"Your warehouse is full, "
                                     f"{self.firm}!", cls="warn")
                        else:
                            self.say(f"Your warehouse will only hold an "
                                     f"additional {vacant}, {self.firm}!",
                                     cls="warn")
                        continue
                    self.hold_[i] -= amount
                    self.warehouse[i] += amount
                    self.hold += amount
                    break
            if self.warehouse[i] > 0:
                while True:
                    amount = yield from self._ask_num(
                        f"How much {ITEMS[i]} shall I move aboard ship, "
                        f"{self.firm}?",
                        hint=f"In warehouse: {self.warehouse[i]:,}")
                    if amount is None:
                        return
                    if amount == -1:
                        amount = self.warehouse[i]
                    if amount > self.warehouse[i]:
                        self.say(f"You have only {self.warehouse[i]}, "
                                 f"{self.firm}.", cls="warn")
                        continue
                    self.warehouse[i] -= amount
                    self.hold_[i] += amount
                    self.hold -= amount
                    break

    def _retire(self):
        self.head("Comprador's Report")
        self.say("Y o u ' r e   a", cls="big")
        self.say("M I L L I O N A I R E !", cls="big")
        yield from self._pause(3200)
        raise _GameOver

    # ------------------------------------------------------------------
    # Travel (BASIC 2700-3350 / C quit())
    def _travel(self):
        options = [{"key": str(i), "label": LOCATIONS[i]}
                   for i in range(1, 8) if i != self.port]
        self.head("Comprador's Report")
        c = yield from self._ask_choice(
            f"{self.firm}, do you wish me to go to: 1) Hong Kong, "
            f"2) Shanghai, 3) Nagasaki, 4) Saigon, 5) Manila, "
            f"6) Singapore, or 7) Batavia ?", options, cancellable=True)
        if c is None:
            return False
        self.dest = int(c)
        self.port = 0

        result = BATTLE_NOT_FINISHED
        self.head("Captain's Report")
        if self.r(self.bp) == 0:
            n = self.r(self.capacity / 10 + self.guns) + 1
            self.say(f"{n} hostile ship{'s' if n != 1 else ''} "
                     f"approaching, {self.firm}!!", cls="warn")
            yield from self._pause()
            result = yield from self._sea_battle(GENERIC, n)

        if result == BATTLE_INTERRUPTED:
            self.head("Captain's Report")
            self.say(f"Li Yuen's fleet drove them off!")
            yield from self._pause()

        if ((result == BATTLE_NOT_FINISHED and self.r(4 + 8 * self.li) == 0)
                or result == BATTLE_INTERRUPTED):
            self.say(f"Li Yuen's pirates, {self.firm}!!", cls="warn")
            yield from self._pause()
            if self.li > 0:
                self.say("Good joss!! They let us be!!")
                yield from self._pause()
            else:
                n = self.r(self.capacity / 5 + self.guns) + 5
                self.say(f"{n} ships of Li Yuen's pirate fleet, "
                         f"{self.firm}!!", cls="warn")
                yield from self._pause()
                result = yield from self._sea_battle(LI_YUEN, n)

        if result == BATTLE_WON:
            self.head("Captain's Report")
            self.say("We captured some booty.")
            self.say(f"It's worth {fancy(self.booty)}!")
            self.cash += self.booty
            yield from self._pause(2600)
        elif result == BATTLE_FLED:
            self.head("Captain's Report")
            self.say(f"We made it, {self.firm}!")
            yield from self._pause()
        elif result == BATTLE_LOST:
            self.head("Captain's Report")
            self.say(f"The buggers got us, {self.firm}!!!", cls="warn")
            self.say("It's all over, now!!!", cls="warn")
            yield from self._pause(3000)
            raise _GameOver

        # Storm (BASIC 3300)
        if self.r(10) == 0:
            self.say(f"Storm, {self.firm}!!", cls="warn")
            yield from self._pause()
            if self.r(30) == 0:
                self.say("   I think we're going down!!", cls="warn")
                yield from self._pause()
                if self.rand01() * (self.damage / self.capacity * 3) >= 1:
                    self.say("We're going down, Taipan!!", cls="warn")
                    yield from self._pause(3000)
                    raise _GameOver
            self.say("    We made it!!")
            yield from self._pause()
            if self.r(3) == 0:
                orig = self.dest
                while self.dest == orig:
                    self.dest = self.r(7) + 1
                self.say(f"We've been blown off course to "
                         f"{LOCATIONS[self.dest]}")
                yield from self._pause()

        self.month += 1
        if self.month == 13:
            self.month = 1
            self.year += 1
            self.ec += 10
            self.ed += 0.5
            # Base prices drift upward slowly over the years (BASIC 1020)
            for i in range(4):
                for p in range(1, 8):
                    self.base[i][p] += self.r(2)
        self.debt = int(self.debt + self.debt * 0.1)
        self.bank = int(self.bank + self.bank * 0.005)

        self.port = self.dest
        self.dest = 0
        self.say(f"Arriving at {LOCATIONS[self.port]}...")
        yield from self._pause(1400)
        return True

    # ------------------------------------------------------------------
    # Sea battle (BASIC 5000-5940 / C sea_battle())
    def _sea_battle(self, battle_id, num_ships):
        t = self.time
        self.booty = (self.r(t / 4 * 1000 * num_ships ** 1.05)
                      + self.r(1000) + 250)
        s0 = num_ships
        slots = [0] * 10
        self.battle = {"ships": num_ships, "slots": slots,
                       "orders_label": ""}
        ok, ik = 0.0, 1
        orders = 0

        def refill():
            on = sum(1 for hp in slots if hp > 0)
            for i in range(10):
                if slots[i] == 0 and on < min(self.battle["ships"], 10):
                    slots[i] = int(self.ec * self.rand01()) + 20
                    on += 1
                    self.fx("appear", i)

        def remove_excess():
            on = sum(1 for hp in slots if hp > 0)
            for i in range(9, -1, -1):
                if on > self.battle["ships"] and slots[i] > 0:
                    slots[i] = 0
                    on -= 1
                    self.fx("clear", i)

        while True:
            if 100 - int(self.damage / self.capacity * 100) <= 0:
                self.battle = None
                return BATTLE_LOST
            if self.battle["ships"] <= 0:
                break

            refill()
            prev_orders = orders

            opts = [{"key": "f", "label": "Fight"},
                    {"key": "r", "label": "Run"},
                    {"key": "t", "label": "Throw cargo"}]
            c = yield from self._ask_choice(
                f"{self.firm}, what shall we do??", opts,
                default="frt"[orders - 1] if orders else None)
            orders = "frt".index(c) + 1
            self.battle["orders_label"] = ["", "Fight", "Run",
                                           "Throw Cargo"][orders]

            escaped = False
            if orders == 1 and self.guns == 0:
                self.say(f"We have no guns, {self.firm}!!", cls="warn")
                yield from self._pause()
            elif orders == 1:
                ok, ik = 3.0, 1
                self.say(f"Aye, we'll fight 'em, {self.firm}.")
                self.say(f"We're firing on 'em, {self.firm}!")
                sunk = 0
                for shot in range(self.guns):
                    if self.battle["ships"] <= 0:
                        break
                    if not any(hp > 0 for hp in slots):
                        refill()
                    live = [i for i in range(10) if slots[i] > 0]
                    target = self.rng.choice(live)
                    self.fx("blast", target,
                            remaining=self.guns - shot - 1)
                    slots[target] -= self.r(30) + 10
                    if slots[target] <= 0:
                        slots[target] = 0
                        self.battle["ships"] -= 1
                        sunk += 1
                        self.fx("sink", target)
                if sunk > 0:
                    self.say(f"Sunk {sunk} of the buggers, {self.firm}!")
                else:
                    self.say(f"Hit 'em, but didn't sink 'em, "
                             f"{self.firm}!")
                yield from self._pause()
                # Some of the enemy may lose heart (BASIC 5360)
                n = self.battle["ships"]
                if (n >= 3 and n != s0
                        and self.r(s0) >= n * 0.6 / battle_id):
                    fled = self.r(n / 3 / battle_id) + 1
                    self.battle["ships"] -= fled
                    remove_excess()
                    self.say(f"{fled} ran away, {self.firm}!")
                    yield from self._pause()
                if self.battle["ships"] <= 0:
                    self.say(f"We got 'em all, {self.firm}!")
                    yield from self._pause()
                    self.battle = None
                    return BATTLE_WON
            elif orders == 3:
                if not (yield from self._throw_cargo()):
                    # Cancelled: back to the orders prompt; no round
                    # passes and the enemy holds fire.
                    orders = prev_orders
                    self.battle["orders_label"] = ["", "Fight", "Run",
                                                   "Throw Cargo"][orders]
                    continue
                if self._thrown > 0:
                    ok += self._thrown / 10
                    escaped = yield from self._try_escape(ok)
            if orders == 2:
                if prev_orders in (2, 3):
                    ok += ik
                    ik += 1
                else:
                    ok, ik = 3.0, 1
                self.say(f"Aye, we'll run, {self.firm}.")
                escaped = yield from self._try_escape(ok)

            if escaped:
                self.battle = None
                return BATTLE_FLED

            # The enemy fires (BASIC 5500)
            self.say(f"They're firing on us, {self.firm}!", cls="warn")
            self.fx("incoming")
            self.say(f"We've been hit, {self.firm}!!", cls="warn")
            i = min(self.battle["ships"], 15)
            pct = self.damage / self.capacity * 100
            if self.guns > 0 and (self.r(100) < pct or pct > 80):
                i = 1
                self.guns -= 1
                self.hold += 10
                self.say(f"The buggers hit a gun, {self.firm}!!",
                         cls="warn")
            self.damage += (self.rand01() * (self.ed * i * battle_id)
                            + i / 2)
            yield from self._pause()
            if battle_id == GENERIC and self.r(20) == 0:
                self.battle = None
                return BATTLE_INTERRUPTED

        self.battle = None
        return BATTLE_WON if orders == 1 else BATTLE_FLED

    def _try_escape(self, ok):
        n = self.battle["ships"]
        if self.r(ok) > self.r(n):
            self.say(f"We got away from 'em, {self.firm}!")
            yield from self._pause()
            return True
        self.say("Couldn't lose 'em.")
        yield from self._pause()
        if n > 2 and self.r(5) == 0:
            lost = self.r(n / 2) + 1
            self.battle["ships"] -= lost
            for i in range(9, -1, -1):
                on = sum(1 for hp in self.battle["slots"] if hp > 0)
                if on > self.battle["ships"] and self.battle["slots"][i] > 0:
                    self.battle["slots"][i] = 0
                    self.fx("clear", i)
            self.say(f"But we escaped from {lost} of 'em!")
            yield from self._pause()
        return False

    def _throw_cargo(self):
        """Returns False if the player cancelled, True otherwise."""
        self._thrown = 0
        options = ([{"key": k, "label": name}
                    for k, name in zip("osag", ITEMS)]
                   + [{"key": "*", "label": "All of it!"}])
        c = yield from self._ask_choice(
            f"What shall I throw overboard, {self.firm}?", options,
            cancellable=True)
        if c is None:
            return False
        if c == "*":
            total = sum(self.hold_)
            if total > 0:
                self._thrown = total
                self.hold += total
                self.hold_ = [0, 0, 0, 0]
        else:
            i = "osag".index(c)
            amount = yield from self._ask_num(
                f"How much, {self.firm}?",
                hint=f"You have {self.hold_[i]:,} aboard")
            if amount is None:
                return False
            if amount == -1 or amount > self.hold_[i]:
                amount = self.hold_[i]
            self._thrown = amount
            self.hold_[i] -= amount
            self.hold += amount
        if self._thrown > 0:
            self.say(f"Let's hope we lose 'em, {self.firm}!")
        else:
            self.say(f"There's nothing there, {self.firm}!")
        yield from self._pause(1200)
        return True

    # ------------------------------------------------------------------
    # Final stats (BASIC 20000)
    def _final_stats(self):
        self.ended = True
        net = self.cash + self.bank - self.debt
        score = int(net / 100 / self.time ** 1.1)
        years = self.time // 12
        months = self.time % 12
        if score >= 50000:
            rating = "Ma Tsu"
        elif score >= 8000:
            rating = "Master Taipan"
        elif score >= 1000:
            rating = "Taipan"
        elif score >= 500:
            rating = "Compradore"
        else:
            rating = "Galley Hand"
        self.head("Your final status:")
        self.say(f"Net cash:  {fancy(net)}")
        self.say(f"Ship size: {self.capacity} units with {self.guns} guns")
        self.say(f"You traded for {years} year{'s' if years != 1 else ''} "
                 f"and {months} month{'s' if months != 1 else ''}")
        self.say(f"Your score is {score:,}.", cls="big")
        self.say(f"Your rating: {rating}", cls="big")
        if 0 <= score < 100:
            self.say("Have you considered a land based job?")
        elif score < 0:
            self.say("The crew has requested that you stay on shore for "
                     "their safety!!")
        yield self._event({"kind": "end", "text": "Play again?",
                           "rating": rating, "score": score})
