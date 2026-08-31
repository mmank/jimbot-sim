"""What a copy carries, and what two of the same joker do.

Both questions came from Marcin looking at a divergence, and both have
answers that are easy to assume wrongly in either direction.

`copy_card` does not copy the card you can see -- it walks every field of the
card's ability table. So a Death carries across things that are not printed
anywhere on the card, and a simulator that copies rank, suit, enhancement,
edition and seal is copying the picture rather than the card.

And two Misprints do not roll the same number. They share a pool *name*,
which reads like sharing a value, but pseudorandom advances the pool on every
call -- so the second Misprint draws from where the first left it.
"""

import pytest

from balatro.consumables import REGISTRY as CONSUMABLES
from balatro.game import Action, ActionType, GameState
from balatro.rng import RunRng
from balatro_headless.runtime import HeadlessBalatro

POOLS = ('local t = {} for k, v in pairs(G.GAME.pseudorandom) do '
         'if type(v) == "number" then '
         '  t[#t+1] = k .. "=" .. string.format("%.17g", v) end '
         'end return table.concat(t, " ")')


@pytest.fixture(scope="module")
def engine():
    game = HeadlessBalatro().boot()
    game.execute('BOT.start_run({"TESTSEED","Red_Deck"}); api.pump(300)')
    return game


def _synced(engine, seed="TESTSEED"):
    rng = RunRng(seed)
    raw = engine.eval("(function() %s end)()" % POOLS)
    for entry in raw.split():
        key, _, value = entry.partition("=")
        if key != "hashed_seed":
            rng.pools[key] = float(value)
    return rng


def test_two_misprints_do_not_roll_the_same_number(engine):
    """They share a pool name, which is not the same as sharing a value.

    pseudorandom advances the pool on every call, so the second Misprint of a
    hand draws from where the first left it. A simulator that cached the
    roll per hand -- an easy reading of "one pool, one number" -- would give
    a pair of Misprints twice the same mult and never the spread the game
    actually produces.
    """
    rng = _synced(engine)
    predicted = [rng.randint("misprint", 0, 23) for _ in range(4)]
    actual = [int(engine.eval(
        "(function() return pseudorandom('misprint', 0, 23) end)()"))
        for _ in range(4)]

    assert predicted == actual
    assert len(set(actual)) > 1, "four draws and the pool never moved"


def test_death_copies_the_chips_a_hiker_left():
    """perma_bonus lives in the ability table, so copy_card takes it."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._start_round()
    left, right = game.hand[0], game.hand[1]
    right.extra_chips = 5
    left.extra_chips = 0

    CONSUMABLES["Death"].apply(game, [left, right])
    assert left.extra_chips == 5


def test_death_copies_whether_the_card_has_been_played_this_ante():
    """Which is what The Pillar debuffs, so it is worth a hand either way."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._start_round()
    left, right = game.hand[0], game.hand[1]

    game.played_this_ante = {right.uid}
    CONSUMABLES["Death"].apply(game, [left, right])
    assert left.uid in game.played_this_ante, "a spent card should spend it"

    game.played_this_ante = {left.uid}
    CONSUMABLES["Death"].apply(game, [left, right])
    assert left.uid not in game.played_this_ante, "a fresh card should clear it"


def test_death_still_copies_what_is_printed_on_the_card():
    from balatro.cards import Edition, Enhancement, Seal

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._start_round()
    left, right = game.hand[0], game.hand[1]
    right.enhancement = Enhancement.GLASS
    right.edition = Edition.POLYCHROME
    right.seal = Seal.RED

    CONSUMABLES["Death"].apply(game, [left, right])
    assert (left.rank, left.suit) == (right.rank, right.suit)
    assert left.enhancement is Enhancement.GLASS
    assert left.edition is Edition.POLYCHROME
    assert left.seal is Seal.RED
