"""A debuffed joker answers nothing, measured on the engine.

Card:calculate_joker opens `if self.debuff then return nil end`
(card.lua:2291-2292). The simulator side of each case is also covered, with
more jokers, by test_debuffed_jokers_answer_nothing; these pin the engine to
the same numbers so the rule is measured rather than read.

A debuff is set with Card:set_debuff(true) (card.lua:526-538), which is what
Crimson Heart's pick calls (blind.lua:588-600); on a small blind the
defeated blind's set_blind(nil) gives it back (blind.lua:211-213, 333-337),
as it does for Crimson Heart.
"""

import pytest

pytest.importorskip("lupa")

from jimbot_sim.blinds import BlindKind, make_blind  # noqa: E402
from jimbot_sim.game import GameState  # noqa: E402
from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available  # noqa: E402
from jimbot_sim.headless.scenario import Scenario  # noqa: E402
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance  # noqa: E402

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not engine_available(),
                       reason="no Balatro engine: need vendor/balatro_src"),
]

HAND = "S_A S_K S_Q S_J S_9 H_2 H_3 D_4"


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


def _read(engine, expr):
    return float(engine.eval("(function() return %s end)()" % expr))


def _sim(*names):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    return game


def _engine_debuff(engine, index):
    engine.execute("G.jokers.cards[%d]:set_debuff(true); api.pump(10)" % index)


def _win_the_round(engine):
    engine.execute("G.GAME.blind.chips = 1; api.highlight({1}); "
                   "G.FUNCS.play_cards_from_highlighted()")
    engine.execute("api.pump_until(function() "
                   "return G.STATE == G.STATES.ROUND_EVAL end); api.pump(600)")


def test_ramen_discard(engine):
    """context.discard (state_events.lua:404) reaches Ramen (card.lua:2757)
    only through the guard. RRT5KY7W stopped on this at decision 188."""
    seen = []
    for debuffed in (True, False):
        Scenario(engine).start().jokers("j_ramen")
        if debuffed:
            _engine_debuff(engine, 1)
        engine.execute("api.discard({1, 2, 3, 4, 5}); api.pump(60)")
        seen.append(round(_read(engine, "G.jokers.cards[1].ability.x_mult"), 2))

    sim = []
    for debuffed in (True, False):
        game = _sim("Ramen")
        if debuffed:
            game.set_joker_debuff(game.jokers[0], True)
        game._discard((0, 1, 2, 3, 4))
        sim.append(round(game.jokers[0].counter, 2))

    assert seen == [2.0, 1.95]
    assert sim == seen


def test_popcorn_round_end(engine):
    """end_of_round (state_events.lua:101) reaches Popcorn (card.lua:2945)
    only through the guard; the debuff is given back after, by defeat."""
    seen = []
    for debuffed in (True, False):
        Scenario(engine).start().jokers("j_popcorn")
        if debuffed:
            _engine_debuff(engine, 1)
        _win_the_round(engine)
        seen.append((_read(engine, "G.jokers.cards[1].ability.mult"),
                     engine.eval("G.jokers.cards[1].debuff and 1 or 0")))

    sim = []
    for debuffed in (True, False):
        game = _sim("Popcorn")
        if debuffed:
            game.set_joker_debuff(game.jokers[0], True)
        game.blind = make_blind(BlindKind.SMALL, game.ante)
        game._beat_blind()
        sim.append((game.jokers[0].counter, int(game.jokers[0].debuffed)))

    assert seen == [(20.0, 0), (16.0, 0)]
    assert sim == seen


def test_golden_joker_row(engine):
    """calculate_dollar_bonus returns early for a debuffed joker (card.lua:1656)
    at state_events.lua:1176, before defeat's queued set_blind(nil) frees it."""
    paid = []
    for debuffed in (True, False):
        Scenario(engine).start().jokers("j_golden")
        if debuffed:
            _engine_debuff(engine, 1)
        _win_the_round(engine)
        before = _read(engine, "G.GAME.dollars")
        engine.execute("api.cash_out()")
        paid.append(_read(engine, "G.GAME.dollars") - before)
    assert paid[1] - paid[0] == 4

    sim = []
    for debuffed in (True, False):
        game = _sim("Golden Joker")
        if debuffed:
            game.set_joker_debuff(game.jokers[0], True)
        game.blind = make_blind(BlindKind.SMALL, game.ante)
        game._beat_blind()
        game._cash_out()
        sim.append([line for line in game.logs
                    if line.startswith("Golden Joker: +$")])
    assert sim[0] == [] and sim[1] != []


def _engine_score(engine, keys, debuff_at=None):
    scene = Scenario(engine).start().hand(HAND).jokers(keys)
    if debuff_at is not None:
        _engine_debuff(engine, debuff_at)
    return scene.play([1, 2, 3, 4, 5])


def test_copiers_beside_a_debuffed_joker(engine):
    """Blueprint and Brainstorm call other_joker:calculate_joker
    (card.lua:2304-2330), nil for a debuffed one. A row of one Joker scores the
    same as Blueprint, debuffed Joker, Joker and as debuffed Joker, Joker,
    Brainstorm."""
    alone = _engine_score(engine, "j_joker")
    blueprint = _engine_score(engine, "j_blueprint j_joker j_joker", 2)
    brainstorm = _engine_score(engine, "j_joker j_joker j_brainstorm", 1)
    both = _engine_score(engine, "j_joker j_joker")
    assert both > alone
    assert blueprint == alone
    assert brainstorm == alone
