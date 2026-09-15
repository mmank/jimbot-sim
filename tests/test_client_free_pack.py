"""Buying a free booster pack has no money to watch.

Card:set_cost zeroes a Celestial booster while Astronomer is held
(card.lua:380), and Astronomer re-costs every card already on the shelf the
moment it arrives (card.lua:616-621). BalatroBridge.buy_pack waited for the
pack to open *and* for the balance to change, so a $0 pack opened, sat in
PLANET_PACK, and the client reported "consequence not observed within 30.0s
(state PLANET_PACK)". Seed H7NS6Y2Y, Checkered Deck, stake 1: Astronomer
bought, then the Celestial Pack beside it.

The client is driven here against a scripted state, with a clock that jumps a
second per reading so a wait that can never succeed fails at once instead of
after thirty real seconds.
"""

import itertools

import pytest

from jimbot_sim.bridge import client as client_module
from jimbot_sim.bridge.client import BalatroBridge


class _Scripted(BalatroBridge):
    def __init__(self, cost: int, money: int = 19) -> None:
        super().__init__()
        self.cost, self.money, self.bought = cost, money, False

    def _pause(self, seconds: float) -> None:
        pass

    def command(self, cmd, *args):
        assert cmd == "buy"
        self.bought = True
        return {"bought": True}

    def state(self) -> dict:
        if not self.bought:
            return {"state_name": "SHOP", "shop_settled": True, "ready": True,
                    "in_pack": False, "pack": [], "dollars": self.money,
                    "shop": [{"area": "shop_booster", "index": 2,
                              "cost": self.cost, "center": "p_celestial_normal_1"}]}
        return {"state_name": "PLANET_PACK", "shop_settled": False,
                "in_pack": True, "dollars": self.money - self.cost,
                "pack": [{"center": "c_jupiter"}, {"center": "c_neptune"},
                         {"center": "c_earth"}],
                "shop": []}


@pytest.fixture(autouse=True)
def _fast_clock(monkeypatch):
    ticks = itertools.count()
    monkeypatch.setattr(client_module.time, "time", lambda: float(next(ticks)))


def test_a_free_celestial_pack_counts_as_bought_once_it_opens():
    bridge = _Scripted(cost=0)
    state = bridge.buy_pack("shop_booster", 2)
    assert state["in_pack"] and state["pack"]


def test_a_paid_pack_still_counts_as_bought_once_it_opens():
    bridge = _Scripted(cost=4)
    state = bridge.buy_pack("shop_booster", 2)
    assert state["in_pack"] and state["dollars"] == 15
