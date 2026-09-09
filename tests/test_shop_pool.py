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

from jimbot_sim.joker_data import JOKER_DATA, pool_for_rarity
from jimbot_sim.rng import RunRng
from jimbot_sim.shop_pool import (GATES, build_consumable_pool, build_pool,
                               draw_consumable, draw_joker, roll_rarity)
from jimbot_sim.headless.runtime import HeadlessBalatro

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


def _played_hands(engine):
    """Hand types the run has actually made, which gate the Planet pool."""
    raw = _ev(engine, 'local t = {} for k, v in pairs(G.GAME.hands) do '
                      'if (v.played or 0) > 0 then t[#t+1] = k end end '
                      'return table.concat(t, "|")')
    return [h for h in raw.split("|") if h]


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


# --------------------------------------------------------------------------
# consumables
# --------------------------------------------------------------------------

@pytest.mark.parametrize("card_set", ["Tarot", "Planet", "Spectral"])
def test_python_predicts_the_consumable_the_shop_offers(engine, card_set):
    """Six consecutive draws per set, compared by key."""
    _start(engine, "TESTSEED")
    for index in range(6):
        rng = _synced_rng(engine, "TESTSEED")
        predicted = draw_consumable(rng, card_set, 1,
                                    played_hands=_played_hands(engine),
                                    seen=_seen_jokers(engine))
        actual = _ev(engine,
                     'local c = create_card("%s", G.consumeables, nil, nil, '
                     'true) return c.config.center.key' % card_set)
        assert predicted == actual, "%s draw %d" % (card_set, index)


def test_a_planet_is_locked_until_its_hand_is_played():
    """Planet X, Ceres and Eris are absent until the hand has been made.

    This is a real constraint on what a run can be offered, not a nicety: a
    simulator that ignores it hands out the card for Five of a Kind to a deck
    that has never made one.
    """
    common = ["High Card", "Pair", "Two Pair", "Three of a Kind", "Straight",
              "Flush", "Full House", "Four of a Kind", "Straight Flush"]
    without = build_consumable_pool("Planet", played_hands=common)
    assert "c_planet_x" not in without
    assert "c_ceres" not in without
    assert "c_eris" not in without

    with_all = build_consumable_pool("Planet", played_hands=common + [
        "Five of a Kind", "Flush House", "Flush Five"])
    assert "c_planet_x" in with_all
    assert len(without) == len(with_all) == 12


def test_the_special_planets_get_no_probability_boost():
    """Once unlocked they are drawn like any other Planet, not more often.

    Worth pinning: the centers carry a `freq` field that looks like a weight,
    and it is 1 for every planet and never read by the pool code.
    """
    from collections import Counter

    played = ["High Card", "Pair", "Two Pair", "Three of a Kind", "Straight",
              "Flush", "Full House", "Four of a Kind", "Straight Flush",
              "Five of a Kind", "Flush House", "Flush Five"]
    rng = RunRng("TESTSEED")
    counts = Counter(draw_consumable(rng, "Planet", 1, played_hands=played)
                     for _ in range(3000))
    special = sum(counts[k] for k in ("c_planet_x", "c_ceres", "c_eris"))
    assert 0.20 < special / 3000 < 0.30, "expected about 3 in 12"


def test_black_hole_and_the_soul_never_come_from_a_pool():
    """They have their own path; drawing them normally would be wrong."""
    assert "c_black_hole" not in build_consumable_pool("Spectral")
    assert "c_soul" not in build_consumable_pool("Spectral")


def test_a_shop_does_not_offer_the_same_consumable_twice():
    """A card blanks its own pool entry the moment it is built.

    Card:set_ability writes `G.GAME.used_jokers[k] = true` on creation
    (card.lua:352), and get_current_pool skips anything in there unless a
    Showman is held (common_events.lua:1987). So the second slot of a shop is
    drawn from a pool the first slot has already left.

    The shop was filled before the run could see it, so the exclusion never
    applied to the shop being built -- a live run against the real game found
    `c_star, c_star` on a shelf the game had stocked with `c_star,
    c_heirophant`.
    """
    from jimbot_sim.game import GameState

    for episode in range(60):
        game = GameState(seed="DUPES%03d" % episode, deck="Red Deck")
        game._open_shop()
        names = [s.consumable.name for s in game.shop.slots
                 if s.consumable is not None]
        assert len(names) == len(set(names)), (episode, names)
        jokers = [s.joker.name for s in game.shop.slots if s.joker is not None]
        assert len(jokers) == len(set(jokers)), (episode, jokers)


def _shops_offering(name, showman, episodes=80):
    """How many shops offer `name` while one is already held."""
    from jimbot_sim.consumables import REGISTRY as CONSUMABLES
    from jimbot_sim.game import GameState
    from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

    found = 0
    for episode in range(episodes):
        game = GameState(seed="SHOWMAN%03d" % episode, deck="Red Deck")
        if showman:
            game.gain_joker(JokerInstance(JOKERS["Showman"]))
        game.consumables.append(game.hold_consumable(CONSUMABLES[name]))
        game._open_shop()
        found += any(s.consumable is not None and s.consumable.name == name
                     for s in game.shop.slots)
    return found


def test_a_shop_withholds_what_the_run_already_holds():
    """Holding The Star takes it out of the shop's pool. It is the same
    `used_jokers` rule as the duplicate above, from the other direction."""
    assert _shops_offering("The Star", showman=False) == 0


def test_a_showman_puts_it_back():
    """Which is what the joker is for, and the shop draw never asked.

    `pack_contents` was passed `showman` and `draw_shop_card` was not, so a
    Showman widened a pack's pool and left the shop's alone -- and the shop
    is where a run buys the second copy it bought the Showman to allow.
    """
    assert _shops_offering("The Star", showman=True) > 0
