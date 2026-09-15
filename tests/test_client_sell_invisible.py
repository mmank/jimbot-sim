"""Selling a charged Invisible Joker leaves the joker count where it was.

Card:sell_card runs calculate_joker{selling_self = true} before the card
dissolves (card.lua:1599), and an Invisible Joker held for two rounds answers
it by copying a random other joker into the row at once (card.lua:2371-2390:
copy_card, add_to_deck, G.jokers:emplace). The sold card leaves and the copy
arrives, so the row is as long afterwards as before.

BalatroBridge.sell waited for the row to get *shorter*, which never happens
here: the money moved, the card was gone, and the client reported "the card
was never used". Seed 71AAZBQV, Painted Deck, stake 1: Invisible Joker at four
rounds, sold mid-round beside two other jokers.

The client is driven against a scripted state with a clock that jumps a second
per reading, as in test_client_free_pack.py.
"""

import itertools

import pytest

from jimbot_sim.bridge import BridgeError
from jimbot_sim.bridge import client as client_module
from jimbot_sim.bridge.client import BalatroBridge


def _joker(uid: int, center: int) -> dict:
    return {"id": uid, "center": center, "sellable": 1, "sell_cost": 3,
            "edition": 0, "eternal": 0, "debuffed": 0, "counter": 0,
            "secondary": 0}


class _Scripted(BalatroBridge):
    """A row before the sale, and whatever `after` says once it is issued."""

    def __init__(self, before: dict, after: dict) -> None:
        super().__init__()
        self.before, self.after, self.sold = before, after, False

    def _pause(self, seconds: float) -> None:
        pass

    def command(self, cmd, *args):
        assert cmd == "sell"
        self.sold = True
        return {"sold": True}

    def state(self) -> dict:
        return dict(self.after if self.sold else self.before,
                    state_name="SELECTING_HAND", busy=False)


@pytest.fixture(autouse=True)
def _fast_clock(monkeypatch):
    ticks = itertools.count()
    monkeypatch.setattr(client_module.time, "time", lambda: float(next(ticks)))


def test_a_sale_that_duplicates_a_joker_counts_as_sold():
    """The row 63 / 67 (Invisible) / 87 becomes 63 / 87 / 92, a copy of 87."""
    before = {"dollars": 10, "consumables": [],
              "jokers": [_joker(63, 174), _joker(67, 145), _joker(87, 181)]}
    after = {"dollars": 14, "consumables": [],
             "jokers": [_joker(63, 174), _joker(87, 181), _joker(92, 181)]}
    state = _Scripted(before, after).sell("jokers", 2)
    assert [j["id"] for j in state["jokers"]] == [63, 87, 92]


def test_an_ordinary_joker_sale_still_counts_as_sold():
    before = {"dollars": 10, "consumables": [],
              "jokers": [_joker(63, 174), _joker(72, 193)]}
    after = {"dollars": 13, "consumables": [], "jokers": [_joker(63, 174)]}
    state = _Scripted(before, after).sell("jokers", 2)
    assert state["dollars"] == 13


def test_a_sale_that_never_lands_is_still_refused():
    """Paid but the card is still in the row: not sold, whatever the count."""
    before = {"dollars": 10, "consumables": [],
              "jokers": [_joker(63, 174), _joker(67, 145)]}
    after = {"dollars": 14, "consumables": [],
             "jokers": [_joker(63, 174), _joker(67, 145)]}
    with pytest.raises(BridgeError):
        _Scripted(before, after).sell("jokers", 2)


def test_a_consumable_sale_still_counts_as_sold():
    """Consumable rows carry no id; the count is still what moves."""
    before = {"dollars": 5, "jokers": [],
              "consumables": [{"center": 1}, {"center": 2}]}
    after = {"dollars": 6, "jokers": [], "consumables": [{"center": 2}]}
    state = _Scripted(before, after).sell("consumeables", 1)
    assert len(state["consumables"]) == 1
