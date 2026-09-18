"""A difference is re-read until the game settles before it is believed.

A live run stopped on SUPMAN01 after buying a pack: the game's consumables read
[] while Hallucination's Wheel of Fortune was on screen, still being made in a
queued event (card.lua:2336-2348). The recording replay read the same way,
once, straight after the previous action returned.
"""

from jimbot_sim.bridge import BalatroBridge


class Ticking(BalatroBridge):
    """A bridge whose game finishes making a card after a few pauses."""

    def __init__(self, arrives_after: int):
        super().__init__()
        self.pauses = 0
        self.arrives_after = arrives_after

    def _pause(self, seconds: float) -> None:
        self.pauses += 1

    def consumables(self) -> list:
        return ["c_wheel_of_fortune"] if self.pauses >= self.arrives_after else []


def test_a_card_still_being_made_is_waited_for():
    bridge = Ticking(arrives_after=3)
    got = bridge.settle(bridge.consumables,
                        lambda held: held == ["c_wheel_of_fortune"])
    assert got == ["c_wheel_of_fortune"]
    assert bridge.pauses == 3


def test_a_settled_read_does_not_wait():
    bridge = Ticking(arrives_after=0)
    bridge.settle(bridge.consumables, lambda held: bool(held))
    assert bridge.pauses == 0


def test_a_real_difference_is_still_reported():
    bridge = Ticking(arrives_after=10 ** 9)
    got = bridge.settle(bridge.consumables, lambda held: bool(held),
                        timeout=0.05, poll=0.0)
    assert got == []
