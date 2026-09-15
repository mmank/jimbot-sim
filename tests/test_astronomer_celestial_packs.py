"""Astronomer makes a Celestial pack free, and the offer has to know it.

Card:set_cost zeroes a Celestial booster while Astronomer is held, exactly as
it zeroes a Planet (card.lua:380). Buying already charged nothing; the legal
action list asked whether the run afforded the *list* price, so a run holding
Astronomer and $3 was never offered the free pack at all. Seed HELLO123,
Blue Deck, stake 1 stood in that shop with a Celestial pack on the shelf.
"""

from jimbot_sim.game import ActionType, GameState
from jimbot_sim.jokers import REGISTRY, JokerInstance
from jimbot_sim.shop import PackKind, PackSpec


def _shop(money, astronomer=True):
    game = GameState(seed="HELLO123", deck="Blue Deck")
    game._open_shop()
    game.money = money
    if astronomer:
        game.gain_joker(JokerInstance(REGISTRY["Astronomer"]))
    game.shop.packs = [
        PackSpec(PackKind.CELESTIAL, "normal", 3, 1, 4, "p_celestial_normal_1"),
        PackSpec(PackKind.ARCANA, "normal", 3, 1, 4, "p_arcana_normal_1"),
    ]
    return game


def _pack_buys(game):
    return sorted(a.index for a in game.legal_actions()
                  if a.type is ActionType.BUY_PACK)


def test_the_free_pack_is_offered_below_its_list_price():
    game = _shop(3)
    assert game.pack_price(game.shop.packs[0]) == 0
    assert game.pack_price(game.shop.packs[1]) == 4
    assert _pack_buys(game) == [0], "only the Celestial pack is free at $3"


def test_buying_it_costs_nothing():
    game = _shop(3)
    action = next(a for a in game.legal_actions()
                  if a.type is ActionType.BUY_PACK and a.index == 0)
    game.step(action)
    assert game.money == 3


def test_without_astronomer_it_costs_the_list_price():
    game = _shop(3, astronomer=False)
    assert game.pack_price(game.shop.packs[0]) == 4
    assert _pack_buys(game) == []
