"""One interface for every backend: the simulator, the headless engine, the game.

`jimbot_sim.run` is what every driver uses -- the policy's live run, and in
time the recording replay and the trained policy's player -- so the waits and
the comparison live here once. These pin its behaviour without an engine; the
engine-against-simulator parity is the slow test in the training repository.

Two of these came from the live run, where Marcin found them: *"some actions
delay for a long time, many seconds. I noticed it happens when the shop is
totally empty, and when buying second card/joker/etc form a pack"*, and *"I
got a hand size difference error, I think the first action of the arcana pack
was use hanged man."*
"""

from types import SimpleNamespace

from jimbot_sim.bridge import BalatroBridge
from jimbot_sim.compare import Names, differences
from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.run import (EngineRun, Mirrored, ShelfMismatch, SimRun,
                            ready_if_sold_out, shelf_of)


# -- the simulator ----------------------------------------------------------

def test_a_sim_run_speaks_the_engines_shape():
    run = SimRun()
    state = run.start("ABCDEFGH", "Red Deck", 1)
    assert state["state_name"] == "BLIND_SELECT"
    assert not run.is_over
    run.step(Action(ActionType.SELECT_BLIND))
    assert run.state()["state_name"] == "SELECTING_HAND"
    assert len(run.state()["hand"]) == 8


def test_a_state_does_not_differ_from_itself():
    run = SimRun()
    run.start("ABCDEFGH", "Red Deck", 1)
    names = Names()
    assert differences(run.state(), run.state(), names) == []
    richer = dict(run.state(), dollars=run.state()["dollars"] + 2)
    found = differences(richer, run.state(), names)
    assert [line.split()[0] for line in found] == ["dollars"]


def test_a_highlight_is_held_by_card_as_the_game_holds_it():
    run = SimRun()
    run.start("ABCDEFGH", "Red Deck", 1)
    run.step(Action(ActionType.SELECT_BLIND))
    run.toggle(6)
    run.toggle(2)
    picked = {id(run.game.hand[2]), id(run.game.hand[6])}
    assert run.selection() == (2, 6)
    run.swap_card_left(2)
    assert run.selection() == (1, 6)
    run.sort_hand("suit")
    assert {id(run.game.hand[i]) for i in run.selection()} == picked
    assert run.state()["toggles_used"] == 2
    run.toggle(run.selection()[0])
    assert len(run.selection()) == 1
    run.clear()
    assert run.selection() == ()
    assert run.state()["toggles_used"] == 3        # a clear is not a toggle


def test_a_sixth_card_is_not_highlighted():
    run = SimRun()
    run.start("ABCDEFGH", "Red Deck", 1)
    run.step(Action(ActionType.SELECT_BLIND))
    for i in range(6):
        run.toggle(i)
    assert run.selection() == (0, 1, 2, 3, 4)
    assert run.state()["selection_size"] == 5
    assert run.state()["toggles_used"] == 6        # the refused one counts


def test_a_play_starts_the_highlight_over():
    run = SimRun()
    run.start("ABCDEFGH", "Red Deck", 1)
    run.step(Action(ActionType.SELECT_BLIND))
    run.toggle(0)
    run.step(Action(ActionType.PLAY, cards=run.selection()))
    assert run.selection() == ()
    assert run.state()["toggles_used"] == 0


def _in_a_round():
    run = SimRun()
    run.start("ABCDEFGH", "Red Deck", 1)
    run.step(Action(ActionType.SELECT_BLIND))
    return run


def test_each_sort_button_is_one_press_a_hand():
    run = _in_a_round()
    assert (run.state()["sorted_rank"], run.state()["sorted_suit"]) == (0, 0)
    run.sort_hand("suit")
    assert (run.state()["sorted_rank"], run.state()["sorted_suit"]) == (0, 1)
    run.toggle(0)
    run.step(Action(ActionType.DISCARD, cards=run.selection()))
    assert (run.state()["sorted_rank"], run.state()["sorted_suit"]) == (0, 0)


def test_the_joker_swap_budget_follows_the_round_and_the_row():
    from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

    run = _in_a_round()
    for name in ("Joker", "Blueprint"):
        run.game.gain_joker(JokerInstance(JOKERS[name]))
    run.step(Action(ActionType.SWAP_JOKER_LEFT, index=1))
    run.step(Action(ActionType.SWAP_JOKER_LEFT, index=1))
    assert run.state()["joker_swaps_used"] == 2
    run.game.gain_joker(JokerInstance(JOKERS["Jolly Joker"]))
    assert run.state()["joker_swaps_used"] == 0      # a new set of jokers


def test_a_round_end_is_cashed_out_by_advance():
    run = _in_a_round()
    run.game.blind.target = 1
    run.toggle(0)
    run.step(Action(ActionType.PLAY, cards=run.selection()))
    assert run.game.phase is Phase.ROUND_EVAL
    assert run.advance(run.state()) is True
    assert run.state()["state_name"] == "SHOP"
    assert run.advance(run.state()) is False


def test_a_patch_after_clicks_is_the_state_rebuilt():
    from jimbot_sim import run as run_module

    run = _in_a_round()
    state = run.state()
    was = run_module.VERIFY
    run_module.verify(True)                  # patched() checks itself too
    try:
        for i in (3, 1, 4, 1, 5):
            run.toggle(i)
            state = run.patched(state)
            assert state == run.state()
        run.clear()
        assert run.patched(state) == run.state()
    finally:
        run_module.verify(was)


def test_an_adopted_position_keeps_its_highlight():
    game = _in_a_round().game
    run = SimRun()
    run.adopt(game, selected=(2, 0, 9))      # 9 is past the hand
    assert run.selection() == (0, 2)
    assert run.state()["toggles_used"] == 2


# -- the engine's translation ------------------------------------------------

class Calls(BalatroBridge):
    """A client that writes down what it was asked to do."""

    def __init__(self, hand: int = 8) -> None:
        super().__init__()
        self.calls = []
        self.held = hand

    def state(self) -> dict:
        return {"hand": [{}] * self.held}

    def pick_pack(self, index, cards=None, closes=None):
        self.calls.append(("pick", index, cards, closes))

    def buy(self, area, index):
        self.calls.append(("buy", area, index))

    def buy_pack(self, area, index):
        self.calls.append(("buy_pack", area, index))

    def command(self, cmd, *args):
        self.calls.append((cmd,) + args)


def test_a_buy_names_the_shelf_the_engine_keeps():
    bridge = Calls()
    engine = EngineRun(bridge)
    engine.step(Action(ActionType.BUY, index=1))
    engine.step(Action(ActionType.BUY_PACK, index=0))
    assert bridge.calls == [("buy", "shop_jokers", 2),
                            ("buy_pack", "shop_booster", 1)]
    assert shelf_of(Action(ActionType.BUY_VOUCHER, index=0)) == (
        "shop_vouchers", 1)


class Highlights(BalatroBridge):
    """A client whose game keeps a highlight, as bot_api reports it."""

    def __init__(self, selected=()) -> None:
        super().__init__()
        self.selected = list(selected)
        self.calls = []

    def state(self) -> dict:
        return {"hand": [{}] * 8, "selected": sorted(self.selected)}

    def command(self, cmd, *args):
        self.calls.append((cmd,) + args)
        if cmd == "clear":
            self.selected = []
        elif cmd == "toggle":
            i = int(args[0])
            if i in self.selected:
                self.selected.remove(i)
            else:
                self.selected.append(i)


def test_the_engines_highlight_reads_as_hand_positions():
    engine = EngineRun(Highlights(selected=[5, 2]))
    assert engine.selection() == (1, 4)
    engine.clear()
    engine.swap_card_left(3)
    assert engine.bridge.calls == [("clear",), ("swap_card_left", 4)]


def test_waiting_lets_time_pass_even_in_a_settled_phase():
    # The use guard (stop_use) is waited out in a phase that is itself
    # settled; a wait that only checked the phase let no frame pass headless.
    from jimbot_sim.run import _Driver

    class Clock(BalatroBridge):
        def __init__(self):
            super().__init__()
            self.paused = 0

        def state(self):
            return {"state_name": "SELECTING_HAND"}

        def _pause(self, seconds):
            self.paused += 1

    bridge = Clock()
    _Driver(bridge).wait()
    assert bridge.paused >= 1


def test_cards_already_highlighted_are_not_picked_again():
    # A policy picks its own cards a toggle at a time; the game counts every
    # toggle against the hand, so selecting them over again would show it a
    # budget spent twice.
    bridge = Highlights(selected=[2, 5])
    bridge.select([2, 5])
    assert bridge.calls == []
    bridge.select([3])
    assert bridge.calls == [("clear",), ("toggle", 3)]


def test_a_pick_says_whether_it_closes_the_pack():
    """A Mega pack stays open after its first pick, and the client otherwise
    waits eight seconds for it to close. The shadow knows how many are left."""
    bridge = Calls()
    shadow = SimpleNamespace(game=SimpleNamespace(pack_picks_left=2),
                             step=lambda action: None)
    mirror = Mirrored(EngineRun(bridge), shadow, Names())
    mirror.step(Action(ActionType.PICK_PACK, index=0), {})
    shadow.game.pack_picks_left = 1
    mirror.step(Action(ActionType.PICK_PACK, index=2), {})
    assert bridge.calls == [("pick", 1, None, False), ("pick", 3, None, True)]


def test_a_shelf_that_does_not_hold_the_card_spends_nothing():
    bridge = Calls()
    stepped = []
    shadow = SimpleNamespace(
        game=None, step=stepped.append,
        state=lambda: {"shop": [{"area": "shop_jokers", "index": 1,
                                 "center": 1}]})
    mirror = Mirrored(EngineRun(bridge), shadow, Names())
    game_state = {"shop": [{"area": "shop_jokers", "index": 1, "center": 2}]}
    try:
        mirror.step(Action(ActionType.BUY, index=0), game_state)
    except ShelfMismatch:
        pass
    else:
        raise AssertionError("bought a card the shadow did not choose")
    assert bridge.calls == [] and stepped == []


class PackScreens(BalatroBridge):
    """A pack's screens in turn, the last one standing."""

    def __init__(self, screens):
        super().__init__()
        self.screens = list(screens)
        self.reads = 0
        self.dealt = 0

    def state(self):
        self.reads += 1
        return self.screens[min(self.reads, len(self.screens)) - 1]

    def _pause(self, seconds):
        pass

    def wait_hand_dealt(self, timeout=20.0):
        self.dealt += 1
        return self.screens[-1]


def test_a_pack_sliding_away_is_not_waited_on_for_cards():
    # After the last pick the game stays in the pack state, holding nothing,
    # until end_consumeable's events put it back in the shop.
    from jimbot_sim.run import _Driver

    closing = {"state_name": "TAROT_PACK", "in_pack": 1, "pack": []}
    shop = {"state_name": "SHOP", "in_pack": 0, "pack": []}
    bridge = PackScreens([closing, closing, shop])
    _Driver(bridge).settle_pack()
    assert bridge.reads == 3 and bridge.dealt == 0


def test_a_pack_opening_is_still_waited_for():
    from jimbot_sim.run import _Driver

    opening = {"state_name": "TAROT_PACK", "in_pack": 1, "pack": []}
    dealt = dict(opening, pack=[{"center": 1}])
    bridge = PackScreens([opening, opening, dealt])
    _Driver(bridge).settle_pack()
    assert bridge.reads == 3 and bridge.dealt == 1


def test_a_bought_out_shop_is_not_waited_on():
    """`shop_ready` is raised while any shelf holds a card, so a shop bought
    out of everything never raised it and `advance` waited thirty seconds."""
    game = GameState(seed="AWEFRTUZ", deck="Blue Deck", stake=1)
    game._open_shop()
    assert game.phase is Phase.SHOP
    stocking = {"state_name": "SHOP", "shop_ready": 0}
    # A shop still dealing is waited on: the shadow's shelves are full.
    assert ready_if_sold_out(stocking, game)["shop_ready"] == 0
    game.shop.slots = []
    game.shop.packs = []
    game.shop.vouchers = []
    assert ready_if_sold_out(stocking, game)["shop_ready"] == 1
    # And nothing else about the state is touched.
    assert ready_if_sold_out({"state_name": "SELECTING_HAND"},
                             game) == {"state_name": "SELECTING_HAND"}


# -- a difference read twice -------------------------------------------------

class Hands(BalatroBridge):
    """A game whose hand has these sizes, read by read; the last one stays."""

    def __init__(self, sizes) -> None:
        super().__init__()
        self.sizes = list(sizes)
        self.reads = 0

    def state(self) -> dict:
        self.reads += 1
        size = self.sizes.pop(0) if len(self.sizes) > 1 else self.sizes[0]
        return {"hand": [{}] * size}

    def wait_ready(self, timeout: float = 30.0, poll: float = 0.05) -> dict:
        return self.state()

    def _pause(self, seconds: float) -> None:
        pass


def _mirror(bridge, held: int) -> Mirrored:
    mirror = Mirrored(EngineRun(bridge), SimpleNamespace(), Names())
    mirror.look = lambda state: (
        (["hand"], {}) if len(state["hand"]) != held else ([], {}))
    return mirror


def test_cards_still_dissolving_are_waited_out():
    """The Hanged Man destroys its cards over a second of events
    (card.lua:1271-1291); the shadow drops them at once."""
    state, found, _ = _mirror(Hands([8, 8, 6]), 6).check({"hand": [{}] * 8})
    assert found == [] and len(state["hand"]) == 6


def test_a_hand_still_being_dealt_is_waited_for():
    state, found, _ = _mirror(Hands([3, 5, 8]), 8).check({"hand": [{}] * 3})
    assert found == [] and len(state["hand"]) == 8


def test_a_real_difference_is_still_reported():
    bridge = Hands([7])
    state, found, _ = _mirror(bridge, 8).check({"hand": [{}] * 7},
                                               timeout=0.05)
    assert found == ["hand"] and bridge.reads > 1


def test_a_bot_asks_for_speed_and_stillness_through_configure():
    # The game boots at 4 with the swirl moving, for a person recording;
    # configure's first argument is the speed, its second reduced motion.
    from jimbot_sim.bridge.headless import HeadlessBridge

    class Sent(BalatroBridge):
        def __init__(self):
            super().__init__()
            self.sent = []

        def command(self, cmd, *args):
            self.sent.append((cmd, args))
            return {}

    bridge = Sent()
    bridge.set_speed(16)
    bridge.set_speed(64, reduced_motion=False)
    assert bridge.sent == [("configure", (16, 1)), ("configure", (64, 0))]
    # The headless engine keeps its own speed and has no configure command.
    assert HeadlessBridge.set_speed(object(), 16) == {}
