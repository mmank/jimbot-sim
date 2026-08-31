"""Two bugs recording 8 found, pinned.

Both were invisible to every other test: they need a legendary joker and a
mid-shop Merry Andy, and nothing else in the suite arranges either. Recording
8 is a real run that does both, which is the argument for replaying real games
rather than only generated ones.
"""

import pytest

from balatro.game import GameState
from balatro.jokers import REGISTRY as JOKER_REGISTRY
from balatro.jokers import JokerInstance
from balatro.rng import RunRng
from balatro.shop_pool import draw_joker


# ------------------------------------------------------------------
# the legendary pool is one stream, not one per ante and source
# ------------------------------------------------------------------

def test_a_legendary_draw_ignores_the_append_and_the_ante():
    """get_current_pool drops both for a legendary:

        _pool_key = 'Joker'..rarity..((not _legendary and _append) or '')
        return _pool, _pool_key..(not _legendary and ante or '')

    so The Soul draws from "Joker4" wherever and whenever it is opened. The
    simulator was asking for "Joker4sou8", which is a perfectly good stream
    with a perfectly plausible legendary in it -- just not the right one.
    Recording 8 stopped on it at step 190 of 443, Chicot against Triboulet.
    """
    picks = set()
    for ante, append in ((1, "sou"), (8, "sou"), (4, "jud"), (2, ""), (8, "")):
        rng = RunRng("ABCD1234")
        picks.add(draw_joker(rng, ante, rarity=4, append=append))
    assert len(picks) == 1, (
        "a legendary draw varied with ante or source: %r" % sorted(picks))


def test_an_ordinary_draw_still_varies_with_the_ante_and_the_source():
    """The opposite has to stay true, or the fix has broken every other pool."""
    picks = set()
    for ante in (1, 2, 3, 4, 5, 6):
        rng = RunRng("ABCD1234")
        picks.add(draw_joker(rng, ante, rarity=3))
    assert len(picks) > 1, "rare draws stopped varying with the ante"

    by_source = set()
    for append in ("", "sou", "jud", "wra"):
        rng = RunRng("ABCD1234")
        by_source.add(draw_joker(rng, 3, rarity=3, append=append))
    assert len(by_source) > 1, "rare draws stopped varying with the source"


# ------------------------------------------------------------------
# a joker that gives discards gives them now, not next round
# ------------------------------------------------------------------

def _in_a_round(deck="Red Deck"):
    game = GameState(seed="TESTSEED", deck=deck)
    game._next_blind()
    game._start_round()
    return game


def test_merry_andy_hands_over_its_discards_on_arrival():
    """Card:add_to_deck does it immediately:

        if self.ability.d_size > 0 then
            G.GAME.round_resets.discards = ... + self.ability.d_size
            ease_discard(self.ability.d_size)
        end

    The round allowance already counted the joker, so the discards were not
    lost -- they arrived a round late, which looks right everywhere except the
    shop the joker was bought in. Recording 8 stopped on it at step 206: five
    discards recorded against two simulated.
    """
    game = _in_a_round()
    before = game.discards_left
    game.gain_joker(JokerInstance(JOKER_REGISTRY["Merry Andy"]))
    assert game.discards_left == before + 3, (
        "expected %d discards, got %d" % (before + 3, game.discards_left))


def test_selling_it_takes_them_back():
    from balatro.game import Action, ActionType

    game = _in_a_round()
    before = game.discards_left
    game.gain_joker(JokerInstance(JOKER_REGISTRY["Merry Andy"]))
    game.step(Action(ActionType.SELL_JOKER, index=len(game.jokers) - 1))
    assert game.discards_left == before


def test_the_giving_back_is_clamped_at_zero():
    """ease_discard is `mod = math.max(-discards_left, mod)`, so losing the
    joker after the discards are spent cannot push the count negative."""
    from balatro.game import Action, ActionType

    game = _in_a_round()
    game.gain_joker(JokerInstance(JOKER_REGISTRY["Merry Andy"]))
    game.discards_left = 1
    game.step(Action(ActionType.SELL_JOKER, index=len(game.jokers) - 1))
    assert game.discards_left == 0


def test_a_joker_with_no_discards_moves_nothing():
    game = _in_a_round()
    before = game.discards_left
    game.gain_joker(JokerInstance(JOKER_REGISTRY["Joker"]))
    assert game.discards_left == before
