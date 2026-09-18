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
