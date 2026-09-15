"""A card changed mid-round is re-debuffed on the spot, measured on the engine.

Card:set_ability (card.lua:365), Card:change_suit (card.lua:561) and
copy_card (common_events.lua:2178) each settle the changed card's debuff
there and then; test_debuff_recheck_on_card_change holds the simulator to
the rule case by case. These put the positions on the engine and read its
flags and Castle's chips, so the rule is measured rather than read.

Scenario.boss comes before Scenario.hand: set_blind debuffs every playing
card (blind.lua:207-210) and set_base re-asks each one (card.lua:143).
"""

import pytest

pytest.importorskip("lupa")

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind  # noqa: E402
from jimbot_sim.cards import Card, Rank, Suit  # noqa: E402
from jimbot_sim.consumables import REGISTRY as CONSUMABLES  # noqa: E402
from jimbot_sim.game import GameState  # noqa: E402
from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available  # noqa: E402
from jimbot_sim.headless.scenario import Scenario  # noqa: E402
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance  # noqa: E402

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not engine_available(),
                       reason="no Balatro engine: need vendor/balatro_src"),
]

BY_NAME = {b.name: b for b in BOSSES}


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


def _index(engine, value, suit):
    return int(engine.eval(
        "(function() for i, c in ipairs(G.hand.cards) do "
        "if c.base.value == '%s' and c.base.suit == '%s' then return i end "
        "end return 0 end)()" % (value, suit)))


def _debuffed(engine, value, suit):
    i = _index(engine, value, suit)
    assert i, "no %s of %s in hand" % (value, suit)
    return engine.eval("tostring(G.hand.cards[%d].debuff)" % i) == "true"


def _use(engine, key, targets):
    engine.execute(
        'local c = create_card("Tarot", G.consumeables, nil, nil, nil, nil, '
        '"%s"); c:add_to_deck(); G.consumeables:emplace(c); api.pump(30)' % key)
    engine.execute("api.use_consumable(1, {%s}); api.pump(120)"
                   % ",".join(str(t) for t in targets))


def _castle_chips(engine):
    return float(engine.eval("G.jokers.cards[1].ability.extra.chips"))


def _sim(boss, hand, *jokers):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in jokers:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game.ante_boss = ""
    game.blind = make_blind(BlindKind.BOSS, game.ante, BY_NAME[boss])
    game._start_round()
    game.blind.target = 10 ** 12
    game.hand[:] = list(hand)
    game.full_deck.extend(hand)
    game._apply_debuffs()
    return game


def test_the_lovers_under_the_window_then_castle(engine):
    """The 5LPYZ3QU stop: Lovers on a Club under The Window, then discard it
    with two more Clubs into a Clubs Castle."""
    Scenario(engine).start().boss("bl_window").hand(
        "C_8 C_6 C_5 H_T S_2 S_3 S_4 S_7").jokers("j_castle")
    engine.execute("G.GAME.current_round.castle_card.suit = 'Clubs'")
    _use(engine, "c_lovers", [_index(engine, "8", "Clubs")])
    wild = _debuffed(engine, "8", "Clubs")
    picks = [_index(engine, v, "Clubs") for v in ("8", "6", "5")]
    engine.execute("api.discard({%s}); api.pump(120)"
                   % ",".join(map(str, picks)))
    seen = (wild, _castle_chips(engine))

    eight, six, five = (Card(Rank.EIGHT, Suit.CLUBS), Card(Rank.SIX, Suit.CLUBS),
                        Card(Rank.FIVE, Suit.CLUBS))
    game = _sim("The Window", [eight, six, five, Card(Rank.TEN, Suit.HEARTS)],
                "Castle")
    game.castle_suit = Suit.CLUBS
    game.use_consumable(CONSUMABLES["The Lovers"], [eight])
    wild = eight.debuffed
    game._discard((0, 1, 2))
    castle = next(j.counter for j in game.jokers if j.name == "Castle")

    assert seen == (True, 6.0)
    assert (wild, castle) == seen


def test_the_sun_releases_a_diamond_under_the_window(engine):
    Scenario(engine).start().boss("bl_window").hand(
        "D_9 S_2 S_3 S_4 S_5 S_6 S_7 S_8")
    before = _debuffed(engine, "9", "Diamonds")
    _use(engine, "c_sun", [_index(engine, "9", "Diamonds")])
    seen = (before, _debuffed(engine, "9", "Hearts"))

    diamond = Card(Rank.NINE, Suit.DIAMONDS)
    game = _sim("The Window", [diamond, Card(Rank.TWO, Suit.SPADES)])
    before = diamond.debuffed
    game.use_consumable(CONSUMABLES["The Sun"], [diamond])

    assert seen == (True, False)
    assert (before, diamond.debuffed) == seen


def test_death_copies_a_debuffed_diamond_under_the_window(engine):
    """Death copies the rightmost of the two (card.lua:1117-1125); the
    Scenario hand is aligned, so array order is screen order."""
    Scenario(engine).start().boss("bl_window").hand(
        "C_5 D_9 S_2 S_3 S_4 S_6 S_7 S_8")
    _use(engine, "c_death", [1, 2])
    # Both are now the Nine of Diamonds; the copy is the first of them.
    first = engine.eval("tostring(G.hand.cards[1].debuff)") == "true"
    suits = [engine.eval("G.hand.cards[%d].base.suit" % i) for i in (1, 2)]
    seen = (suits, first)

    left, right = Card(Rank.FIVE, Suit.CLUBS), Card(Rank.NINE, Suit.DIAMONDS)
    game = _sim("The Window", [left, right])
    game.use_consumable(CONSUMABLES["Death"], [left, right])

    assert seen == (["Diamonds", "Diamonds"], True)
    assert left.suit is Suit.DIAMONDS and left.debuffed == seen[1]
