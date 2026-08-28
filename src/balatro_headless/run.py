"""Drive a complete Balatro run, blind 1 through the ante 8 boss.

`HeadlessRun` owns the state machine: it advances the game to whatever point a
decision is actually needed, hands that decision to a policy, applies the
answer, and repeats. A policy therefore never has to know about DRAW_TO_HAND,
event pumping or cash-out screens -- it only ever sees a decision point.

    run = HeadlessRun(seed="ABCDEFGH")
    result = run.play(GreedyPolicy())
    print(result.ante, result.won)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .runtime import HeadlessBalatro

# Decision points a policy is asked about.
BLIND_SELECT = "BLIND_SELECT"
SELECTING_HAND = "SELECTING_HAND"
SHOP = "SHOP"
PACK = "PACK"

# States the driver resolves on its own, without consulting the policy.
AUTO_STATES = {"DRAW_TO_HAND", "HAND_PLAYED", "NEW_ROUND", "PLAY_TAROT"}
# Frames to advance per poll while the game resolves a state on its own.
AUTO_PUMP_STEP = 12
PACK_STATES = {"TAROT_PACK", "PLANET_PACK", "SPECTRAL_PACK", "STANDARD_PACK",
               "BUFFOON_PACK"}
TERMINAL_STATES = {"GAME_OVER", "MENU", "SPLASH"}

WIN_ANTE = 8


class Policy(Protocol):
    def blind(self, run: "HeadlessRun", state: dict) -> tuple[str, Any]: ...
    def hand(self, run: "HeadlessRun", state: dict) -> tuple[str, Any]: ...
    def shop(self, run: "HeadlessRun", state: dict) -> tuple[str, Any]: ...
    def pack(self, run: "HeadlessRun", state: dict) -> tuple[str, Any]: ...


@dataclass
class RunResult:
    seed: str
    won: bool
    ante: int
    round: int
    dollars: int
    jokers: list[str]
    decisions: int
    stopped: str
    log: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        outcome = "WON" if self.won else f"died on ante {self.ante}"
        return (f"seed {self.seed}: {outcome} after {self.round} rounds, "
                f"${self.dollars}, {len(self.jokers)} jokers ({self.stopped})")


class HeadlessRun:
    """One run of the real game, driven headlessly."""

    def __init__(self, seed: str | None = None, deck: str | None = None,
                 stake: int = 1, game: HeadlessBalatro | None = None,
                 verbose: bool = False, max_decisions: int = 4000,
                 starting_money: int | None = None,
                 money_per_shop: int = 0) -> None:
        # Sandbox knobs, for isolating whether a policy is money-starved or
        # strategy-starved. Not a real run: anything measured with these set is
        # a diagnostic, never a baseline.
        self.starting_money = starting_money
        self.money_per_shop = money_per_shop
        self.game = game or HeadlessBalatro().boot()
        self.seed = seed
        self.deck = deck
        self.stake = stake
        self.verbose = verbose
        self.max_decisions = max_decisions
        self.log: list[str] = []
        self._started = False

    # ------------------------------------------------------------------

    def _lua(self, expr: str):
        return self.game.eval(expr)

    def _exec(self, chunk: str):
        return self.game.execute(chunk)

    def note(self, message: str) -> None:
        self.log.append(message)
        if self.verbose:
            print(message)

    def start(self) -> None:
        # Game:start_run reads the deck from G.GAME.viewed_back *before* it
        # replaces G.GAME, so setting it here is how the deck is chosen.
        # Stake is 1..8 (white..gold).
        if self.deck:
            known = self._lua("(function() local d = get_deck_from_name('%s') "
                              "return d and d.name or nil end)()" % self.deck)
            if not known:
                raise ValueError(f"unknown deck {self.deck!r}")
            self._exec(f"G.GAME.viewed_back = {{name = '{self.deck}'}}")

        # Tear the previous run down first. G.FUNCS.start_run (the game's own
        # entry point) queues G:delete_run() before G:start_run(), and skipping
        # it leaks the whole run: measured at 53 cards, ~2.5 UIBoxes and ~1
        # queued event per episode, which is what made a long training run get
        # steadily slower -- 447 steps/s decaying to 184 within minutes.
        self._exec("if G.STAGE == G.STAGES.RUN then G:delete_run() end")

        args = []
        if self.seed:
            args.append(f"seed = '{self.seed}'")
        if self.stake and self.stake != 1:
            args.append(f"stake = {self.stake}")
        self._exec("G:start_run({%s})" % ", ".join(args))
        if self.starting_money is not None:
            # ease_dollars is the game's own money path, so jokers and unlocks
            # that watch the balance still see a consistent value.
            delta = self.starting_money - self._lua("G.GAME.dollars")
            self._exec(f"ease_dollars({delta}, true)")
        if self.seed is None:
            self.seed = self._lua("G.GAME.pseudorandom.seed")
        self._started = True

    # ------------------------------------------------------------------
    # inspection, forwarded to the Lua side

    def snapshot(self) -> dict:
        return dict(self._lua("api.snapshot()"))

    def hand(self) -> list[dict]:
        return [dict(c) for c in self._lua("api.hand_cards()").values()]

    def jokers(self) -> list[str]:
        return [c["key"] for c in self._lua("api.jokers()").values()]

    def shop_contents(self) -> list[dict]:
        return [dict(i) for i in self._lua("api.shop_contents()").values()]

    def pack_contents(self) -> list[dict]:
        return [dict(i) for i in self._lua("api.pack_contents()").values()]

    def consumables(self) -> list[dict]:
        return [dict(i) for i in self._lua("api.consumables()").values()]

    def hand_info(self, indices) -> dict:
        return dict(self._lua("api.hand_info({%s})"
                              % ",".join(str(i) for i in indices)))

    def best_play(self) -> tuple[list[int], float]:
        packed = self._lua("(function() local p, s = api.best_play() "
                           "return table.concat(p, ',') .. '|' .. s end)()")
        idx, _, score = packed.partition("|")
        return [int(i) for i in idx.split(",") if i], float(score)

    def best_discard(self) -> list[int]:
        packed = self._lua("table.concat(api.best_discard(), ',')")
        return [int(i) for i in packed.split(",") if i]

    # ------------------------------------------------------------------
    # actions

    def select_blind(self):
        return self._lua("api.select_blind()")

    def skip_blind(self):
        return self._lua("api.skip_blind()")

    def play(self, indices):
        return self._lua("api.play({%s})" % ",".join(str(i) for i in indices))

    def discard(self, indices):
        return self._lua("api.discard({%s})" % ",".join(str(i) for i in indices))

    def cash_out(self):
        return self._lua("api.cash_out()")

    def buy(self, area: str, index: int):
        return self._lua(f"api.buy('{area}', {index})")

    def sell(self, area: str, index: int):
        return self._lua(f"api.sell('{area}', {index})")

    def can_sell(self, area: str, index: int) -> bool:
        return bool(self._lua(f"api.can_sell('{area}', {index})"))

    def reroll(self):
        return self._lua("api.reroll()")

    def leave_shop(self):
        return self._lua("api.leave_shop()")

    def skip_pack(self):
        return self._lua("api.skip_pack()")

    def pick_pack(self, index: int):
        return self._lua(f"api.pick_pack({index})")

    def buy_and_use(self, area: str, index: int):
        return self._lua(f"api.buy_and_use('{area}', {index})")

    def use_consumable(self, index: int, cards=None):
        targets = "{%s}" % ",".join(str(i) for i in (cards or []))
        return self._lua(f"api.use_consumable({index}, {targets})")

    def move_joker(self, from_index: int, to_index: int):
        return self._lua(f"api.move_joker({from_index}, {to_index})")

    def reorder_jokers(self, order):
        return self._lua("api.reorder_jokers({%s})"
                         % ",".join(str(i) for i in order))

    def sort_hand(self, by: str = "value"):
        return self._lua(f"api.sort_hand('{by}')")

    def pump(self, frames: int = 120):
        return self._exec(f"api.pump({frames})")

    # ------------------------------------------------------------------

    def _apply(self, phase: str, choice: tuple[str, Any]) -> None:
        action, arg = choice if isinstance(choice, tuple) else (choice, None)
        if action == "select":
            self.select_blind()
        elif action == "skip":
            self.skip_blind()
        elif action == "play":
            self.play(arg)
        elif action == "discard":
            self.discard(arg)
        elif action == "buy":
            self.buy(*arg)
        elif action == "sell":
            self.sell(*arg)
        elif action == "reroll":
            self.reroll()
        elif action == "leave":
            self.leave_shop()
        elif action == "use":
            if isinstance(arg, tuple):
                self.use_consumable(*arg)
            else:
                self.use_consumable(arg)
        elif action == "pick":
            self.pick_pack(arg)
        elif action == "skip_pack":
            self.skip_pack()
        else:
            raise ValueError(f"policy returned unknown action {action!r} in {phase}")

    def play_run(self, policy: Policy) -> RunResult:
        """Advance until the run ends, asking `policy` at each decision point."""
        if not self._started:
            self.start()

        decisions = 0
        stopped = "finished"
        last_round = -1
        # A policy that keeps choosing an action the game refuses would spin
        # forever, so track whether anything actually changes and bail out.
        last_signature = None
        stalled = 0
        shop_waits = 0
        last_funded_round = None

        while decisions < self.max_decisions:
            state = self.snapshot()
            name = state["state_name"]

            if state["won"] or state["ante"] > WIN_ANTE:
                stopped = "won"
                break
            if name in TERMINAL_STATES:
                stopped = "game over" if name == "GAME_OVER" else name.lower()
                break

            if state["round"] != last_round:
                last_round = state["round"]
                self.note(f"  ante {state['ante']} round {state['round']}: "
                          f"${state['dollars']}, jokers {self.jokers()}")

            if name in AUTO_STATES:
                # Small steps, re-checking between them. A fixed pump(120) here
                # burned ~2400 frames a run waiting on transitions that usually
                # take a handful, and pump frames cost ~145us during live play.
                self.pump(AUTO_PUMP_STEP)
                continue
            if name == "ROUND_EVAL":
                self.cash_out()
                continue
            if name in PACK_STATES:
                # Packs can also be opened by tags, not only by purchase.
                self._lua("api.settle_pack()")
                self._apply(PACK, policy.pack(self, state))
            elif name == "BLIND_SELECT":
                self._apply(BLIND_SELECT, policy.blind(self, state))
            elif name == "SELECTING_HAND":
                self._apply(SELECTING_HAND, policy.hand(self, state))
            elif name == "SHOP":
                if self.money_per_shop and state["round"] != last_funded_round:
                    last_funded_round = state["round"]
                    self._exec(f"ease_dollars({self.money_per_shop}, true)")
                # Cards arrive a few frames after the state flips. Bounded:
                # an unbounded wait here does not increment the decision count
                # and so would hang the run if the shop never stocks.
                if not self._lua("api.shop_ready()") and shop_waits < 60:
                    shop_waits += 1
                    self.pump(AUTO_PUMP_STEP)
                    continue
                shop_waits = 0
                self._apply(SHOP, policy.shop(self, state))
            else:
                # Unknown state: pump and let the game settle rather than guess.
                self.pump(120)
                continue

            signature = (name, state["ante"], state["round"], state["dollars"],
                         state["chips"], state["hands_left"],
                         state["discards_left"], state["joker_count"],
                         state["consumable_count"])
            if signature == last_signature:
                stalled += 1
                if stalled >= 12:
                    # Break the deadlock with the one action always available,
                    # and give up if even that changes nothing.
                    if name == "SHOP":
                        self.leave_shop()
                    elif name in PACK_STATES:
                        self.skip_pack()
                    elif name == "SELECTING_HAND":
                        best, _ = self.best_play()
                        self.play(best or [1])
                    else:
                        stopped = f"stalled in {name}"
                        break
                    stalled = 0
            else:
                stalled = 0
            last_signature = signature
            decisions += 1
        else:
            stopped = "decision limit"

        final = self.snapshot()
        return RunResult(
            seed=self.seed or "?",
            won=bool(final["won"]) or final["ante"] > WIN_ANTE,
            ante=int(final["ante"]),
            round=int(final["round"]),
            dollars=int(final["dollars"]),
            jokers=self.jokers(),
            decisions=decisions,
            stopped=stopped,
            log=self.log,
        )

    # convenience alias
    play_with = play_run
