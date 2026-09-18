"""A pick waits for its pack to close only when the pack is going to.

`pick_pack` ends with a courtesy wait of up to eight seconds for the pack to
shut, so the card taken is not still flying over the next screen. A Mega pack
allows two picks and stays open after the first, so that wait ran its full
length every time: eight seconds of a live run standing still between the two
picks. The caller knows how many picks are left, and says so.

And a pick that leaves the pack open still waits for itself to finish. The
courtesy wait had been doing that too, by running out: The Hanged Man taken
from a Mega Arcana pack dissolves its two cards over the second after the pack
shrinks, and a live run stopped on a hand still holding them.
"""

from jimbot_sim.bridge.client import BalatroBridge, NotReady


class OpenPack(BalatroBridge):
    """A game that takes a pick at once, keeps its pack open, and stays busy
    for a few reads while the pick resolves."""

    def __init__(self, busy_reads: int = 0) -> None:
        super().__init__()
        self.picked = False
        self.busy_reads = busy_reads
        self.waited: list[float] = []

    def command(self, cmd, *args):
        if cmd == "pick_pack":
            self.picked = True
        return None

    def state(self) -> dict:
        busy = 0
        if self.picked and self.busy_reads > 0:
            self.busy_reads -= 1
            busy = 1
        pack = [{"center": 1}] if self.picked else [{"center": 1},
                                                    {"center": 2}]
        return {"ready": 1, "in_pack": 1, "pack": pack, "busy": busy,
                "jokers": [], "consumables": [], "deck_size": 52,
                "hand_levels": {}}

    def _pause(self, seconds: float) -> None:
        pass

    def wait_until(self, predicate, timeout: float = 30.0,
                   poll: float = 0.05) -> dict:
        # The pack never closes here, so the courtesy wait would run out its
        # clock; record that it was asked for and give up at once.
        self.waited.append(timeout)
        raise NotReady("the pack stays open")


def test_a_pick_that_leaves_the_pack_open_does_not_wait_for_it_to_close():
    bridge = OpenPack()
    bridge.pick_pack(1, closes=False)
    assert bridge.picked and bridge.waited == []


def test_a_pick_that_leaves_the_pack_open_still_waits_for_itself():
    bridge = OpenPack(busy_reads=5)
    state = bridge.pick_pack(1, closes=False)
    assert bridge.busy_reads == 0 and not state["busy"]


def test_a_pick_that_closes_the_pack_still_waits_for_it():
    for closes in (None, True):
        bridge = OpenPack()
        bridge.pick_pack(1, closes=closes)
        assert bridge.waited == [8.0]


class Counted(OpenPack):
    """The same game, reporting how many picks the pack has left."""

    def __init__(self, choices: int) -> None:
        super().__init__()
        self.choices = choices

    def state(self) -> dict:
        return dict(super().state(), pack_choices=self.choices)


def test_the_state_says_whether_a_pick_closes_the_pack():
    # The environments drive the engine without a shadow to ask, so the
    # game's own count (G.GAME.pack_choices) answers when the caller cannot.
    bridge = Counted(choices=2)
    bridge.pick_pack(1)
    assert bridge.waited == []
    bridge = Counted(choices=1)
    bridge.pick_pack(1)
    assert bridge.waited == [8.0]


class NextPack(BalatroBridge):
    """A pick that closes its pack, straight into another one: two pack tags
    open their packs back to back, and the game is never out of a pack."""

    def __init__(self) -> None:
        super().__init__()
        self.picked = False

    def command(self, cmd, *args):
        if cmd == "pick_pack":
            self.picked = True
        return None

    def state(self) -> dict:
        pack = ([{"center": 7}, {"center": 8}] if self.picked
                else [{"center": 1}, {"center": 2}])
        return {"ready": 1, "in_pack": 1, "pack": pack, "busy": 0,
                "pack_choices": 1,
                "jokers": [{"id": 1}] if self.picked else [],
                "consumables": [], "deck_size": 52, "hand_levels": {}}

    def _pause(self, seconds: float) -> None:
        pass


def test_a_pack_followed_by_another_has_closed():
    import time

    bridge = NextPack()
    started = time.perf_counter()
    state = bridge.pick_pack(1)
    # Not the eight seconds the wait for a pack that never closes runs.
    assert time.perf_counter() - started < 2.0
    assert [r["center"] for r in state["pack"]] == [7, 8]


def test_a_skip_into_another_pack_has_left_the_first():
    import time

    class SkipIntoNext(NextPack):
        def command(self, cmd, *args):
            if cmd == "skip_pack":
                self.picked = True          # the next pack is up
            return None

    bridge = SkipIntoNext()
    started = time.perf_counter()
    state = bridge.skip_pack()
    assert time.perf_counter() - started < 2.0
    assert [r["center"] for r in state["pack"]] == [7, 8]
