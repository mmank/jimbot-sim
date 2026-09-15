"""Astronomer makes a Celestial pack free, and the offer has to know it.

Card:set_cost zeroes a Celestial booster while Astronomer is held, exactly as
it zeroes a Planet (card.lua:380). Buying already charged nothing; the legal
action list asked whether the run afforded the *list* price, so a run holding
Astronomer and $3 was never offered the free pack at all. Seed HELLO123,
Blue Deck, stake 1 stood in that shop with a Celestial pack on the shelf.
"""

from jimbot_sim.game import Action, ActionType, GameState
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


def _legal_packs(game):
    return [i for i in range(len(game.shop.packs))
            if game.is_legal(Action(ActionType.BUY_PACK, index=i))]


def test_is_legal_takes_the_free_pack_below_its_list_price():
    """is_legal prices the pack as the offer does, not at its list price.

    The game's button is G.FUNCS.can_open (functions/button_callbacks.lua:111-119):

        if (e.config.ref_table.cost) > 0 and
           (e.config.ref_table.cost > G.GAME.dollars - G.GAME.bankrupt_at)

    and set_cost has zeroed a Celestial pack under Astronomer (card.lua:380).
    is_legal asked about the list price, so legal_actions offered the free
    pack and is_legal refused it: seeds 72JTCTMW (Magic Deck, stake 8, $0 by
    a $6 Jumbo), RCDKMIKP and 891F8AYE (Blue Deck, stake 3, $3 and $0 by a
    $4 pack) all bought Astronomer and crashed proposing the pack beside it.
    """
    game = _shop(3)
    assert _legal_packs(game) == _pack_buys(game) == [0]


def test_is_legal_takes_the_free_pack_in_debt():
    """can_open's `cost > 0` escape (button_callbacks.lua:112): a pack set_cost
    zeroed opens with any balance, a run already past its floor included."""
    game = _shop(-5)
    assert game.spendable < 0
    assert _legal_packs(game) == _pack_buys(game) == [0]


def test_is_legal_still_refuses_the_list_price_without_astronomer():
    """Without Astronomer set_cost leaves the list price (card.lua:369-380),
    and can_open (button_callbacks.lua:112) refuses $4 out of $3."""
    game = _shop(3, astronomer=False)
    assert _legal_packs(game) == _pack_buys(game) == []


def test_is_legal_takes_a_couponed_pack_at_no_money():
    """The Coupon Tag zeroes the boosters too, not only the shelf.

    tag.lua:448-459 sets G.GAME.shop_free and marks every card in
    G.shop_booster couponed, and set_cost zeroes a couponed card in
    G.shop_booster (card.lua:383). pack_price already knew it; is_legal
    did not.
    """
    game = _shop(0, astronomer=False)
    game.shop_free = True
    assert game.pack_price(game.shop.packs[1]) == 0
    assert _legal_packs(game) == _pack_buys(game) == [0, 1]
