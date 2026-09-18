"""One way to drive a run, whichever backend plays it.

Three things play Balatro here: the simulator (`GameState`), the game's own
Lua run headless (`headless.runtime`), and the game itself with a window
(`bridge.client`). The last two already share a client. The simulator spoke
its own language -- `Action`s in, a `GameState` out -- and every tool that
drove one against the other translated between them on its own: the policy's
live run, the trained policy's player, the recording replay. Each grew its own
waits and its own comparison, so a wait added to one never reached the rest.

So there is one interface, and the backends implement it:

    start(seed, deck, stake)   a fresh run; returns its state
    state()                    the state, in the engine's shape, settled
    step(action)               an `Action` from jimbot_sim.game, carried out
    is_over                    whether the run has ended

`SimRun` is the simulator. `EngineRun` is the engine behind a bridge, headless
or visible: it turns an `Action` into the client's calls, which each wait for
their own consequence. `Mirrored` runs one of each in step, the engine for
real and the simulator as its shadow, and says where they part -- after
re-reading a difference until the engine settles, because the engine resolves
an action as a chain of queued events and a single read can land in the
middle of one.

States are all the engine's shape (`jimbot_sim.state`) and are compared with
`jimbot_sim.compare`.

What a player does that is not an `Action` is on the interface too, because a
replay has to do it: `arrange` puts the hand and the joker row in an order a
player dragged them into, `sort_hand` presses a sort button, `set_money`
restores the bankroll a recording was made with, and `fingerprint` is the
recorder's own snapshot (bot_api's `check`) for comparing against one. See
`jimbot_sim.replay`.
"""

from __future__ import annotations

from typing import Protocol

from .compare import Names, differences
from .game import Action, ActionType, GameState, Phase
from .hands import HandType
from .headless.driving import AUTO_STATES, advance, is_over
from .joker_data import JOKER_DATA
from .shop_pool import NAME_BY_CONSUMABLE_KEY
from .state import state_dict


class Run(Protocol):
    """What every backend answers to."""

    def start(self, seed: str, deck: str = "Red Deck",
              stake: int = 1) -> dict: ...

    def state(self) -> dict: ...

    def step(self, action: Action) -> None: ...

    @property
    def is_over(self) -> bool: ...

    def settle(self, done, timeout: float = 10.0, read=None) -> dict: ...

    def key(self, row: dict) -> str: ...

    def arrange(self, hand_ids=None, joker_ids=None,
                joker_keys=None) -> None: ...

    def sort_hand(self, by: str) -> None: ...

    def set_money(self, amount: int) -> None: ...

    def fingerprint(self) -> dict: ...


# Centre keys by the simulator's display names, for the recorder's snapshot.
KEY_BY_JOKER = {name: key for name, (key, *_r) in JOKER_DATA.items()}
KEY_BY_CONSUMABLE = {name: key
                     for key, name in NAME_BY_CONSUMABLE_KEY.items()}

_SIM_NAMES: Names | None = None


def _sim_names() -> Names:
    global _SIM_NAMES
    if _SIM_NAMES is None:
        _SIM_NAMES = Names()
    return _SIM_NAMES


# ----------------------------------------------------------------------
# the simulator
# ----------------------------------------------------------------------

class SimRun:
    """The simulator, as a backend. `game` is the `GameState` itself.

    Endless is off by default: the simulator would play on past ante eight
    while the game declares the run won, which is a divergence about the best
    thing that can happen.
    """

    def __init__(self, endless: bool = False) -> None:
        self.endless = endless
        self.game: GameState | None = None
        # Each starting card's place in the deck, which is the id the game
        # gives it -- the simulator builds its deck in the game's own order.
        self.deck_index: dict[int, int] = {}

    def start(self, seed: str, deck: str = "Red Deck",
              stake: int = 1) -> dict:
        self.game = GameState(seed=seed, deck=deck, stake=stake,
                              endless=self.endless)
        self.deck_index = {card.uid: i
                           for i, card in enumerate(self.game.full_deck)}
        return self.state()

    def state(self) -> dict:
        return state_dict(self.game)

    def legal_actions(self) -> list[Action]:
        return self.game.legal_actions()

    def step(self, action: Action) -> None:
        self.game.step(action)

    @property
    def is_over(self) -> bool:
        return self.game.is_over

    def settle(self, done, timeout: float = 10.0, read=None) -> dict:
        """Nothing is ever in flight here: every step completes."""
        return self.state()

    def key(self, row: dict) -> str:
        return _sim_names().key(row, live=False)

    def arrange(self, hand_ids=None, joker_ids=None,
                joker_keys=None) -> None:
        """The hand in the order of the game's ids, the row by joker keys.

        The ids line up because the simulator builds its deck in the game's
        own order; the simulator's jokers carry no game ids, so the row is
        ordered by key. See `match_hand_order`, `match_joker_order`.
        """
        match_hand_order(self.game, list(hand_ids or []), self.deck_index)
        match_joker_order(self.game, joker_keys)

    def sort_hand(self, by: str) -> None:
        self.game.sort_hand(by)

    def set_money(self, amount: int) -> None:
        self.game.money = amount

    def fingerprint(self) -> dict:
        return sim_fingerprint(self.game)

    def follow(self, state: dict) -> None:
        """Line this run up with an engine's before they are compared.

        Two things are aligned rather than compared: the hand's order, which
        decides which card an index names and which each side sorts its own
        way, and To Do List's hand, which the game cannot reproduce from its
        seed. See `match_hand_order` and `match_named_hands`.
        """
        match_hand_order(self.game, [int(c["id"]) for c in state.get("hand")
                                     or []], self.deck_index)
        match_named_hands(self.game, state.get("jokers") or [])


def match_hand_order(game: GameState, ids: list[int],
                     deck_index: dict[int, int]) -> None:
    """Hold the shadow's hand in the order the game holds it.

    `ops/sim_replay.py` does this from a recording's snapshots and for the
    same reason: the hand's order decides which card an index names, and both
    sides sort their own. Cards the run made -- a Death's copy, a card out of
    a standard pack -- are numbered by each side in creation order and matched
    by age, since neither side's numbering means anything to the other.
    """
    if not ids:
        return
    by_id: dict[int, list] = {}
    made = []
    for card in game.hand:
        index = deck_index.get(card.uid)
        if index is None:
            made.append(card)
        else:
            by_id.setdefault(index, []).append(card)
    made.sort(key=lambda card: card.uid)
    by_made = dict(zip(sorted(w for w in ids if w >= len(deck_index)), made))

    ordered, leftover = [], list(game.hand)
    for want in ids:
        pool = by_id.get(want)
        if pool:
            card = pool.pop(0)
        elif want in by_made:
            card = by_made.pop(want)
        else:
            continue                  # not held here; the comparison says so
        ordered.append(card)
        leftover.remove(card)
    game.hand[:] = ordered + leftover


def sim_fingerprint(game: GameState) -> dict:
    """The recorder's snapshot, as far as the simulator can say it.

    Card ids are not in it -- the simulator numbers its cards its own way --
    and nor is anything else it cannot report in the game's terms; a replay
    compares only the fields both sides have.
    """
    return {
        "dollars": game.money,
        "chips": game.chips_scored,
        "hands_left": game.hands_left,
        "discards_left": game.discards_left,
        "hand_size": len(game.hand),
        "ante": game.ante,
        "jokers": [KEY_BY_JOKER.get(j.name, j.name) for j in game.jokers],
        "consumables": [KEY_BY_CONSUMABLE.get(c.name, c.name)
                        for c in game.consumables],
        "last_hand": game.last_hand,
        "blind": game.blind_name,
        "blind_chips": game.blind_target,
        "round": game.round_number,
    }


def match_joker_order(game: GameState, recorded_keys) -> None:
    """Put the simulator's joker row into the order the recording shows.

    Jokers are dragged as often as cards are, and the order is not cosmetic:
    Blueprint copies the joker to its right, so the same five jokers in a
    different arrangement score differently. Like the hand, the arrangement
    is not a function call and cannot be recorded, but every snapshot carries
    the result.

    Only a reordering is applied. If the two sides hold different jokers that
    is a real divergence and the comparison should see it, so anything that
    does not line up is left where it is.
    """
    if not recorded_keys:
        return
    by_key: dict = {}
    for joker in game.jokers:
        by_key.setdefault(KEY_BY_JOKER.get(joker.name, joker.name),
                          []).append(joker)
    ordered, leftover = [], list(game.jokers)
    for key in recorded_keys:
        pool = by_key.get(key)
        if pool:
            joker = pool.pop(0)
            ordered.append(joker)
            leftover.remove(joker)
    game.jokers[:] = ordered + leftover


_HAND_BY_LABEL = {hand.label: hand for hand in HandType}


def match_named_hands(game: GameState, rows: list[dict]) -> None:
    """Give each To Do List the hand the game's copy names.

    Not a simulator gap that can be closed. The game draws the hand from a
    pool built by walking `pairs(G.GAME.hands)` (card.lua:313, 2977), and
    LuaJIT seeds its string hash per process, so the same draw from the same
    stream lands on a different name in a different process -- see
    jimbot_sim.hands. The shadow keeps the stream in step and takes the name
    from the game, so a policy prices the list on the hand it will pay for.
    Rows that do not line up are left alone for the comparison to report.
    """
    if len(rows) != len(game.jokers):
        return
    for joker, row in zip(game.jokers, rows):
        hand = _HAND_BY_LABEL.get(row.get("to_do_hand") or "")
        if hand is not None and joker.spec.rerolls_a_hand:
            joker.named_hand = hand


# ----------------------------------------------------------------------
# the engine, headless or visible
# ----------------------------------------------------------------------

# The client's shop areas, by the action that buys from them. The simulator
# keeps three lists and the engine three card areas, laid out in the same
# order -- jokers, then vouchers, then boosters (jimbot_sim.state.shop_rows)
# -- so an action's index is the position within its area.
SHOP_AREA = {ActionType.BUY: "shop_jokers",
             ActionType.BUY_AND_USE: "shop_jokers",
             ActionType.BUY_VOUCHER: "shop_vouchers",
             ActionType.BUY_PACK: "shop_booster"}


def shelf_of(action: Action) -> tuple[str, int]:
    """The client's (area, one-based index) for a buying action."""
    return SHOP_AREA[action.type], action.index + 1


class EngineRun:
    """The engine behind a bridge client, as a backend.

    The client does the waiting: every wrapper waits for the consequence its
    action is supposed to have, so an `Action` is one call or two -- `select`
    then `play`, a consumable with the cards it is aimed at. What the client
    cannot know it is told: whether a pick is a pack's last (`closes`), which
    otherwise costs a Mega pack eight seconds of waiting for a pack that stays
    open.
    """

    def __init__(self, bridge, endless: bool = False) -> None:
        self.bridge = bridge
        self.driver = _Driver(bridge)
        self._names: Names | None = None
        # Carry on past a win, as `SimRun(endless=True)` does: the game puts
        # its win screen up and waits, paused, for a button no recording has
        # (bot_api's continue_endless). A replay of a player who pressed it
        # presses it too.
        self.endless = endless
        self._carried_on = False

    @property
    def names(self) -> Names:
        if self._names is None:
            self._names = Names.of(self.bridge)
        return self._names

    def start(self, seed: str, deck: str = "Red Deck",
              stake: int = 1) -> dict:
        # in_run flips before the blind-select screen exists, and
        # select_blind is a no-op until it does.
        self.bridge.command("start_run", seed, deck.replace(" ", "_"), stake)
        return self.bridge.wait_until(
            lambda s: s.get("in_run") and s.get("ready"), timeout=60)

    def key(self, row: dict) -> str:
        return self.names.key(row, live=True)

    def set_money(self, amount: int) -> None:
        self.bridge.command("set_money", amount)

    def sort_hand(self, by: str) -> None:
        self.bridge.command("sort_hand", by)

    def fingerprint(self) -> dict:
        from .replay import normalise

        return normalise(self.bridge.command("check"))

    def arrange(self, hand_ids=None, joker_ids=None,
                joker_keys=None) -> None:
        """Put the hand and the jokers in an order a player dragged them into.

        Dragging is not a G.FUNCS call so it cannot be hooked, but the order
        it produces is observable and can be set directly. Joker order
        matters as much as hand order: effects resolve left to right, and a
        recording of this very project diverged by 434 chips on ordering
        alone. The hand is let finish dealing first -- an Arcana pack deals
        its targeting hand over several frames, and a partial hand cannot be
        put in any order.
        """
        if hand_ids:
            self.bridge.wait_hand_dealt(timeout=10.0)
            self._arrange_hand(list(hand_ids))
        self._arrange_jokers(list(joker_ids or []), list(joker_keys or []))

    def _arrange_hand(self, expected: list) -> bool:
        from .replay import normalise, ranked

        current = normalise(self.bridge.command("check").get("hand_ids")) or []
        if current == expected:
            return True
        if sorted(current) == sorted(expected):
            self.bridge.command("set_hand_order", *expected)
            return True
        # The numbers can drift apart while the hand is the same hand: the
        # run's card counter moves whenever the real game builds something
        # the engine does not (see replay.ranked), so a card is the same card
        # under a different number. Ranking both sides and mapping back gives
        # the reorder anyway.
        if len(current) != len(expected) or ranked(current) == ranked(expected):
            return ranked(current) == ranked(expected)
        by_rank = sorted(current)
        self.bridge.command("set_hand_order",
                            *[by_rank[r] for r in ranked(expected)])
        return True

    def _arrange_jokers(self, ids: list, keys: list) -> bool:
        """By ids where they line up, else by the recorded key order.

        Ids can be right about identity and still not match -- the card
        counter again -- and a recording made before joker ids existed
        carries only the keys. Jokers sharing a key are interchangeable for
        this purpose.
        """
        from .replay import normalise

        if ids:
            current = normalise(self.bridge.command("check")
                                .get("joker_ids")) or []
            if current == ids:
                return True
            if sorted(current) == sorted(ids):
                self.bridge.command("set_joker_order", *ids)
                return True
        if not keys:
            return True
        current_keys = normalise(self.bridge.command("check")
                                 .get("jokers")) or []
        if list(current_keys) == keys:
            return True
        if sorted(current_keys) != sorted(keys):
            return False
        live = normalise(self.bridge.command("state").get("jokers")) or []
        if len(live) != len(current_keys):
            return True
        remaining = dict(enumerate(current_keys))
        order = []
        for key in keys:
            for i, have in list(remaining.items()):
                if have == key:
                    order.append(live[i]["id"])
                    del remaining[i]
                    break
        if len(order) != len(live):
            return False
        self.bridge.command("set_joker_order", *order)
        return True

    def state(self) -> dict:
        state = self.bridge.wait_ready(timeout=60)
        if self.endless and not self._carried_on and state.get("won"):
            if (self.bridge.command("continue_endless") or {}).get("continued"):
                self._carried_on = True
                state = self.bridge.wait_ready(timeout=60)
        return state

    @property
    def is_over(self) -> bool:
        return is_over(self.bridge.state())

    def advance(self, state: dict, settled: int = 0) -> bool:
        """Move on through a phase no player is asked about; False if none.

        The same questions `jimbot_sim.headless.driving` asks of the engine
        for the environment, so which phases get offered at all is the same
        for every driver. A cash-out is one of them.
        """
        return advance(state, self.driver, settled)

    def settle(self, done, timeout: float = 10.0, read=None) -> dict:
        """The state once `done(read())` holds, or as it is after `timeout`.

        `read` is the state by default; a replay reads the recorder's
        snapshot (`fingerprint`) instead.

        For comparing against a state known to be settled -- a shadow's, a
        recording's -- where one read can land mid-flight: money paying out
        over several events, a hand still being dealt or dissolving, a
        joker's end-of-round count deferred by 0.2s, Hallucination's Tarot
        made in a queued event after the pack it came with has stocked. Only
        waits for the expected state to arrive, so a real difference is still
        there when it gives up. Then `state()`, so the answer is actionable.
        """
        self.bridge.settle(read or self.bridge.state, done, timeout=timeout)
        return self.state()

    def step(self, action: Action, *, closes: bool | None = None) -> None:
        """Carry out `action` on the engine. `closes`: see the class."""
        bridge = self.bridge
        t = action.type
        cards = [i + 1 for i in action.cards]

        def dragged_to_front():
            """Drag the named cards to the front in the order named.

            What the simulator's `_arrange_play` leaves behind, played out as
            the drags a player makes. Serves an ordered play, whose order is
            its scoring order, and a consumable whose targets are positional:
            Death converts the left selected card into the right one, so
            which of the two is dragged left decides which one is spent.
            BOT.swap_card_left takes a one-based position.
            """
            held = len(bridge.state().get("hand") or [])
            order = list(range(max(held, max(action.cards) + 1)))
            for place, want in enumerate(action.cards):
                at = order.index(want)
                while at > place:
                    bridge.command("swap_card_left", at + 1)
                    order[at - 1], order[at] = order[at], order[at - 1]
                    at -= 1
            return list(range(1, len(action.cards) + 1))

        if t is ActionType.SELECT_BLIND:
            bridge.select_blind()
        elif t is ActionType.SKIP_BLIND:
            # No wrapper: a skip lands back on the select screen for the next
            # blind, so there is no state change to wait for. The caller's
            # next `state()` covers the tag and any pack it opens.
            bridge.command("skip_blind")
        elif t is ActionType.REROLL_BOSS:
            bridge.command("reroll_boss")
        elif t is ActionType.PLAY:
            if list(action.cards) != sorted(action.cards):
                cards = dragged_to_front()
            bridge.select(cards)
            bridge.play()
        elif t is ActionType.DISCARD:
            bridge.select(cards)
            bridge.discard()
        elif t is ActionType.USE_CONSUMABLE:
            if list(action.cards) != sorted(action.cards):
                cards = dragged_to_front()
            bridge.use_consumable(action.index + 1, cards or None)
        elif t is ActionType.SELL_JOKER:
            bridge.sell("jokers", action.index + 1)
        elif t is ActionType.SWAP_JOKER_LEFT:
            # move_joker takes where from and where to, both one-based.
            bridge.command("move_joker", action.index + 1, action.index)
        elif t is ActionType.SELL_CONSUMABLE:
            bridge.sell("consumeables", action.index + 1)
        elif t is ActionType.REROLL:
            bridge.reroll()
        elif t is ActionType.LEAVE_SHOP:
            bridge.leave_shop()
        elif t is ActionType.CASH_OUT:
            bridge.cash_out()
        elif t is ActionType.PICK_PACK:
            bridge.pick_pack(action.index + 1, cards or None, closes=closes)
        elif t is ActionType.SKIP_PACK:
            bridge.skip_pack()
        elif t in SHOP_AREA:
            area, index = shelf_of(action)
            if t is ActionType.BUY_AND_USE:
                bridge.buy_and_use(area, index, cards or None)
            elif t is ActionType.BUY_PACK:
                # buy_pack waits for the pack to open *and stock itself*; buy
                # waits only for the money to move. Deciding in that window
                # read as a policy declining a pack.
                bridge.buy_pack(area, index)
            else:
                bridge.buy(area, index)
        else:
            raise ValueError("no engine action for %s" % t)


class _Driver:
    """Advancing the phases no player is asked about, through the client.

    The headless driver pumps frames; through a client time passes on its own
    and is waited for. Both answer the questions `advance` asks.
    """

    def __init__(self, bridge) -> None:
        self.bridge = bridge

    def wait(self) -> None:
        # Short on purpose: the caller advances repeatedly and gives up after
        # a bounded number of tries, so a long timeout here does not fail
        # faster, it only blocks.
        try:
            self.bridge.wait_for(
                lambda s: s["state_name"] not in AUTO_STATES
                and (s["state_name"] != "SHOP" or s.get("shop_ready")),
                timeout=0.5)
        except Exception:                                  # noqa: BLE001
            pass

    def cash_out(self) -> None:
        self.bridge.cash_out()

    def settle_pack(self) -> None:
        pass                    # the client's pick_pack waits for the pack


# ----------------------------------------------------------------------
# the two in step
# ----------------------------------------------------------------------

class ShelfMismatch(RuntimeError):
    """The engine's shelf does not hold what the shadow is about to buy."""

    def __init__(self, differences: list[str]) -> None:
        super().__init__("\n  ".join(differences))
        self.differences = differences


class Mirrored:
    """The engine played for real, the simulator beside it as its shadow.

    Every action goes to both: the engine first, and the shadow only once the
    engine has done it, so an action the engine refuses leaves the two on the
    same run. `check` says where they differ.
    """

    def __init__(self, engine: EngineRun, shadow: SimRun,
                 names: Names | None = None) -> None:
        self.engine = engine
        self.shadow = shadow
        self.names = names or Names.of(engine.bridge)

    @property
    def game(self) -> GameState:
        return self.shadow.game

    def start(self, seed: str, deck: str = "Red Deck",
              stake: int = 1) -> dict:
        state = self.engine.start(seed, deck, stake)
        self.shadow.start(seed, deck, stake)
        return state

    def look(self, state: dict) -> tuple[list[str], dict]:
        """Line the shadow up with `state` and say where they differ."""
        self.shadow.follow(state)
        shadow = self.shadow.state()
        return differences(state, shadow, self.names), shadow

    def check(self, state: dict, timeout: float = 10.0
              ) -> tuple[dict, list[str], dict]:
        """(state, differences, shadow state), a difference read twice.

        The first read after an action can catch the engine part-way through
        it -- see `EngineRun.settle` -- so a difference is re-read until it
        goes or `timeout` passes, and only what survives is reported.
        """
        found, shadow = self.look(state)
        if found:
            state = self.engine.settle(lambda s: not self.look(s)[0],
                                       timeout=timeout)
            found, shadow = self.look(state)
        return state, found, shadow

    def advance(self, state: dict, settled: int = 0) -> tuple[bool, bool]:
        """(advanced, cashed out): the engine through an unasked phase.

        A cash-out is made by the driver on the player's behalf, as the
        environment makes it, so the shadow is stepped here rather than
        asked.
        """
        if not self.engine.advance(ready_if_sold_out(state, self.game),
                                   settled):
            return False, False
        if (state["state_name"] == "ROUND_EVAL"
                and self.game.phase is Phase.ROUND_EVAL):
            self.shadow.step(Action(ActionType.CASH_OUT))
            return True, True
        return True, False

    def step(self, action: Action, state: dict) -> None:
        """`action` on the engine, then on the shadow.

        A buy is checked first: the comparison after every action already
        says the two shops agree, but this is the one place where being wrong
        spends money on the wrong card. `ShelfMismatch` if it would, and
        whatever the client raises if the engine refuses; the shadow is not
        stepped in either case.
        """
        if action.type in SHOP_AREA:
            self._identify(action, state)
        closes = (self.game.pack_picks_left <= 1
                  if action.type is ActionType.PICK_PACK else None)
        self.engine.step(action, closes=closes)
        self.shadow.step(action)

    def _identify(self, action: Action, state: dict) -> None:
        area, index = shelf_of(action)

        def key(rows, live):
            for row in rows or []:
                if row.get("area") == area and int(row.get("index") or 0) == index:
                    return self.names.key(row, live)
            return None

        want = key(self.shadow.state().get("shop"), False)
        got = key(state.get("shop"), True)
        if got is None:
            raise ShelfMismatch(["%s[%d] is not on offer in the game"
                                 % (area, index)])
        if got != want:
            raise ShelfMismatch(["%s[%d]      game %s\n%-14s shadow %s"
                                 % (area, index, got, "", want)])


def ready_if_sold_out(state: dict, game: GameState) -> dict:
    """The engine's state, with a shop that has nothing left counted as stocked.

    `shop_ready` is raised while any shelf holds a card, which is how the
    driver tells a shop still dealing from one it can decide in -- and a shop
    bought out of everything never raises it again, so `advance` waited for
    it to stock, half a second at a time, until its patience ran out: thirty
    seconds of a live run standing still. Marcin noticed it "when the shop is
    totally empty". The shadow knows the difference: while the game deals,
    the shadow's shelves are already full.
    """
    shop = game.shop
    if (state.get("state_name") == "SHOP" and not state.get("shop_ready")
            and game.phase is Phase.SHOP and shop is not None
            and not (shop.slots or shop.vouchers_on_offer() or shop.packs)):
        return dict(state, shop_ready=1)
    return state
