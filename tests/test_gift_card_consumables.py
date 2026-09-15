"""A held consumable sells for what Card:set_cost says, Gift Card included.

Gift Card's end_of_round walks the consumables as well as the jokers
(card.lua:2993-3005):

    for k, v in ipairs(G.consumeables.cards) do
        if v.set_cost then
            v.ability.extra_value = (v.ability.extra_value or 0) + self.ability.extra
            v:set_cost()

and a consumable's sell price is the same formula a joker's is
(card.lua:369-385):

    self.extra_cost = 0 + G.GAME.inflation  (+ the edition's 2/3/5/5)
    self.cost = max(1, floor((base_cost + extra_cost + 0.5)*(100-discount)/100))
    Planet and Astronomer held (find_joker, not debuffed): self.cost = 0
    self.sell_cost = max(1, floor(self.cost/2)) + (self.ability.extra_value or 0)

set_cost is rerun on every card whenever the discount (card.lua:1917-1923) or
Astronomer (619, 676) changes, so the price follows the run. G.FUNCS.sell_card
pays `sell_cost` (card.lua:1608).

The simulator raised only the jokers' extra_sell_value and sold every
consumable for `max(1, spec.cost // 2)`: no Gift Card money, no edition, no
discount. And Perkeo's copy is copy_card, which copies the whole ability
table, extra_value with it (common_events.lua:2161-2167), before the Negative
edition's set_cost.
"""

import pytest

from jimbot_sim.blinds import BlindKind, make_blind
from jimbot_sim.cards import Edition
from jimbot_sim.consumables import REGISTRY as CONSUMABLES
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance
from jimbot_sim.shop import VOUCHER_BY_KEY

try:
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
    from jimbot_sim.headless.scenario import Scenario
except ImportError:          # no lupa: the simulator half still runs
    HeadlessBalatro, engine_available = None, lambda: False


def _run(*names):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    return game


def _hold(game, name, edition=Edition.NONE):
    held = game.hold_consumable(CONSUMABLES[name], edition)
    game.consumables.append(held)
    return held


def _end_round(game):
    game.blind = make_blind(BlindKind.SMALL, game.ante)
    game._beat_blind()


def _sale(game, index=0):
    """What selling the consumable at `index` pays, on the cash-out screen."""
    before = game.money
    game.step(Action(ActionType.SELL_CONSUMABLE, index=index))
    return game.money - before


def test_gift_card_adds_a_dollar_to_a_held_consumable():
    """card.lua:3000-3005, once per round held."""
    game = _run("Gift Card")
    _hold(game, "The Fool")
    _end_round(game)
    assert game.consumables[0].extra_sell_value == 1
    assert _sale(game) == 1 + 1


def test_the_value_accumulates_over_rounds():
    game = _run("Gift Card")
    _hold(game, "The Fool")
    for _ in range(4):
        _end_round(game)
    assert _sale(game) == 1 + 4


def test_a_debuffed_gift_card_adds_nothing():
    """calculate_joker returns nil for a debuffed joker (card.lua:2291-2292)."""
    game = _run("Gift Card")
    game.set_joker_debuff(game.jokers[0], True)
    _hold(game, "The Fool")
    _end_round(game)
    assert _sale(game) == 1


def test_a_negative_consumable_sells_for_its_edition():
    """extra_cost takes negative's 5 (card.lua:372-373): The Fool costs
    floor(3 + 5 + 0.5) = 8 and sells for 4."""
    game = _run()
    _hold(game, "The Fool", Edition.NEGATIVE)
    _end_round(game)
    assert _sale(game) == 4


def test_a_spectral_sells_through_the_discount():
    """Clearance Sale: Hex costs floor((4 + 0.5) * 0.75) = 3 and sells for 1,
    not 4 // 2 = 2 (card.lua:375, 382)."""
    game = _run()
    game.vouchers.append(VOUCHER_BY_KEY["v_clearance_sale"])
    _hold(game, "Hex")
    _end_round(game)
    assert _sale(game) == 1


def test_astronomer_makes_a_negative_planet_worth_a_dollar():
    """card.lua:380 zeroes a Planet's cost before sell_cost reads it."""
    game = _run("Astronomer")
    _hold(game, "Mercury", Edition.NEGATIVE)
    _end_round(game)
    assert _sale(game) == 1
    game = _run()
    _hold(game, "Mercury", Edition.NEGATIVE)
    _end_round(game)
    assert _sale(game) == 4


def test_perkeos_copy_keeps_the_value():
    """copy_card copies ability.extra_value (common_events.lua:2161-2167)."""
    game = _run("Perkeo")
    held = _hold(game, "The Fool")
    held.extra_sell_value = 2
    JOKERS["Perkeo"].on_shop_end(game.jokers[0], game)
    copy = game.consumables[1]
    assert copy is not held
    assert (copy.edition, copy.extra_sell_value) == (Edition.NEGATIVE, 2)
    assert held.extra_sell_value == 2


# -- the engine ---------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


@pytest.mark.slow
@pytest.mark.skipif(not engine_available(),
                    reason="no Balatro engine: need vendor/balatro_src")
def test_the_engine_prices_them_the_same(engine):
    """A Fool and a Negative Hex held through one round beside Gift Card."""
    Scenario(engine).start().jokers("j_gift")
    engine.execute(
        'for _, k in ipairs({"c_fool", "c_hex"}) do '
        'local c = create_card("Tarot", G.consumeables, nil, nil, nil, nil, k); '
        'if k == "c_hex" then c:set_edition({negative = true}, true, true) end; '
        'c:add_to_deck(); G.consumeables:emplace(c) end; api.pump(60)')
    engine.execute("G.GAME.blind.chips = 1; api.highlight({1}); "
                   "G.FUNCS.play_cards_from_highlighted()")
    engine.execute("api.pump_until(function() "
                   "return G.STATE == G.STATES.ROUND_EVAL end); api.pump(600)")
    seen = [int(engine.eval("G.consumeables.cards[%d].sell_cost" % i))
            for i in (1, 2)]
    assert seen == [2, 5]

    game = _run("Gift Card")
    _hold(game, "The Fool")
    _hold(game, "Hex", Edition.NEGATIVE)
    _end_round(game)
    assert [_sale(game, 1), _sale(game, 0)] == [seen[1], seen[0]]
