"""A pick waits for its pack to close only when the pack is going to.

`pick_pack` ends with a courtesy wait of up to eight seconds for the pack to
shut, so the card taken is not still flying over the next screen. A Mega pack
allows two picks and stays open after the first, so that wait ran its full
length every time: eight seconds of a live run standing still between the two
picks. The caller knows how many picks are left, and says so.
"""

from jimbot_sim.bridge.client import BalatroBridge, NotReady


class OpenPack(BalatroBridge):
    """A game that takes a pick at once and keeps its pack open."""

    def __init__(self) -> None:
        super().__init__()
        self.picked = False
        self.waited: list[float] = []

    def command(self, cmd, *args):
        if cmd == "pick_pack":
            self.picked = True
        return None

    def state(self) -> dict:
        pack = [{"center": 1}] if self.picked else [{"center": 1},
                                                    {"center": 2}]
        return {"ready": 1, "in_pack": 1, "pack": pack, "busy": 0,
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


def test_a_pick_that_closes_the_pack_still_waits_for_it():
    for closes in (None, True):
        bridge = OpenPack()
        bridge.pick_pack(1, closes=closes)
        assert bridge.waited == [8.0]
