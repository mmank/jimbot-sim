"""Which joker the shop offers, checked draw for draw against the game.

This is the distribution half of fidelity, and the half that fails quietly.
A policy learns the odds it is trained against, so a shop that offers the
wrong joker at the wrong rate teaches a game that does not exist -- and every
scoring rule can be exactly right while it happens. It is also the specific
warning from people who have built Balatro bots before: the sim-to-real gap
they hit was distribution, not rules.

Two details in the game's algorithm change the odds and are easy to miss:

  blanking      entries that fail the filter are replaced by "UNAVAILABLE"
                rather than dropped, so the pool keeps its length and the draw
                is uniform over all entries rather than over the available
                ones. An unavailable joker costs a resample; it is not skipped.
  resampling    each resample draws from a *different* pool name, so it is a
                new draw rather than a retry of the same one.

Getting either wrong still produces a plausible shop, which is why these are
compared against the engine key by key instead of by distribution.
"""

import pytest

from balatro.joker_data import JOKER_DATA, pool_for_rarity
from balatro.rng import RunRng
from balatro.shop_pool import GATES, build_pool, draw_joker, roll_rarity
from balatro_headless.runtime import HeadlessBalatro

SEEDS = ["TESTSEED", "ABCD1234", "7EVEN", "XYZZY"]

POOLS_QUERY = ('local t = {} for k, v in pairs(G.GAME.pseudorandom) do '
               'if type(v) == "number" then '
               '  t[#t+1] = k .. "=" .. string.format("%.17g", v) end '
               'end return table.concat(t, " ")')


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


def _ev(engine, body):
    return engine.eval("(function() %s end)()" % body)


def _start(engine, seed):
    engine.execute('BOT.start_run({"%s","Red_Deck"}); api.pump(300)' % seed)


def _synced_rng(engine, seed):
    """A generator standing exactly where the engine's pools stand."""
    rng = RunRng(seed)
    for entry in _ev(engine, POOLS_QUERY).split():
        key, _, value = entry.partition("=")
        if key != "hashed_seed":
            rng.pools[key] = float(value)
    return rng


def _seen_jokers(engine):
    """G.GAME.used_jokers -- every joker the run has already produced.

    The game blanks these from the pool, so a prediction that ignores them
    stays right only until the first repeat would have come up.
    """
    raw = _ev(engine, 'local t = {} for k, _ in pairs(G.GAME.used_jokers or {}) '
                      'do t[#t+1] = k end return table.concat(t, " ")')
    return raw.split()


def _pool_flags(engine):
    """G.GAME.pool_flags -- history that decides Gros Michel vs Cavendish."""
    raw = _ev(engine, 'local t = {} for k, v in pairs(G.GAME.pool_flags or {}) '
                      'do if v then t[#t+1] = k end end '
                      'return table.concat(t, " ")')
    return raw.split()


def _engine_draws_a_joker(engine):
    return _ev(engine,
               'local c = create_card("Joker", G.jokers, nil, nil, true) '
               'return c.config.center.key')


@pytest.mark.parametrize("seed", SEEDS)
def test_python_predicts_the_joker_the_shop_offers(engine, seed):
    """Twenty consecutive draws, each compared by key.

    Consecutive matters: the pools advance with every draw, so a run of twenty
    only stays in step if the rarity roll, the pool order and the resampling
    are all right together.
    """
    _start(engine, seed)
    for index in range(20):
        rng = _synced_rng(engine, seed)
        predicted = draw_joker(rng, ante=1,
                               seen_jokers=_seen_jokers(engine),
                               pool_flags=_pool_flags(engine))
        assert predicted == _engine_draws_a_joker(engine), (
            "seed %s draw %d" % (seed, index))


def test_the_draws_are_not_all_the_same_joker(engine):
    """Guard against the comparison passing on a degenerate sequence."""
    _start(engine, "TESTSEED")
    drawn = {_engine_draws_a_joker(engine) for _ in range(20)}
    assert len(drawn) > 5, "the shop kept offering the same few jokers: %s" % drawn


def test_the_rarity_split_is_the_games(engine):
    """Roughly 70/25/5, from thresholds at 0.7 and 0.95.

    Checked loosely: the point is that the thresholds are the right way round
    and applied to the right roll, not that a sample of 400 lands on the mean.
    """
    rng = RunRng("TESTSEED")
    counts = {1: 0, 2: 0, 3: 0}
    for _ in range(400):
        counts[roll_rarity(rng, 1)] += 1
    assert counts[1] > counts[2] > counts[3]
    assert 0.55 < counts[1] / 400 < 0.85
    assert counts[3] / 400 < 0.15


def test_a_gated_joker_is_blanked_not_dropped(engine):
    """Filtering must preserve the pool's length, or every later draw shifts."""
    full = build_pool(2)
    gated = build_pool(2, owned_enhancements=("m_lucky",))
    assert len(full) == len(gated) == len(pool_for_rarity(2))
    lucky_cat = JOKER_DATA["Lucky Cat"][0]
    assert lucky_cat not in full, "Lucky Cat offered with no Lucky card owned"
    assert lucky_cat in gated, "Lucky Cat still withheld once one is owned"


def test_every_gate_names_a_real_enhancement():
    assert set(GATES.values()) <= {"m_glass", "m_gold", "m_lucky", "m_steel",
                                   "m_stone"}
    assert len(GATES) == 5


def test_a_joker_already_seen_is_withheld_unless_showman():
    seen = [JOKER_DATA["Banner"][0]]
    assert JOKER_DATA["Banner"][0] not in build_pool(1, seen_jokers=seen)
    assert JOKER_DATA["Banner"][0] in build_pool(1, seen_jokers=seen,
                                                 showman=True)


def test_the_pools_are_the_games_sizes():
    assert [len(pool_for_rarity(r)) for r in (1, 2, 3, 4)] == [61, 64, 20, 5]
    assert sum(len(pool_for_rarity(r)) for r in (1, 2, 3, 4)) == 150


def test_gros_michel_and_cavendish_swap_places(engine):
    """The pair the pool flag exists for, and it is not in either joker's text.

    Before the flag is set the pool can offer Gros Michel and not Cavendish;
    after it, the other way round. A simulator ignoring pool flags offers both
    at once, which is a shop the real game never builds.
    """
    gros = JOKER_DATA["Gros Michel"][0]
    cavendish = JOKER_DATA["Cavendish"][0]

    fresh = build_pool(1)
    assert gros in fresh
    assert cavendish not in fresh

    extinct = build_pool(1, pool_flags=("gros_michel_extinct",))
    assert gros not in extinct
    assert cavendish in extinct
