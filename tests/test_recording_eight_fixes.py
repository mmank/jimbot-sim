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


# ------------------------------------------------------------------
# which joker a random draw lands on
# ------------------------------------------------------------------

def test_a_random_joker_draw_goes_by_age_not_by_row_position():
    """The Wheel of Fortune, Ectoplasm and Hex all pick a joker out of an
    ordered pool, and the game orders it with pseudorandom_element:

        if keys[1].v.sort_id then
            table.sort(keys, function (a, b) return a.v.sort_id < b.v.sort_id end)

    sort_id is creation order (Card:init, `G.sort_id = (G.sort_id or 0) + 1`),
    so the draw depends on how old a joker is and never on where it sits.
    Drawing from row order returns the wrong joker the moment anything has
    been dragged -- reproducibly, out of the right stream, which is what kept
    it hidden.
    """
    from balatro.consumables import _editionless

    game = GameState(seed="TESTSEED", deck="Red Deck")
    first = JokerInstance(JOKER_REGISTRY["Joker"])
    second = JokerInstance(JOKER_REGISTRY["Misprint"])
    game.gain_joker(first)
    game.gain_joker(second)
    assert first.uid < second.uid, "the row did not stamp ages in order"

    # Dragging the row must not change what a draw sees.
    game.jokers[:] = [second, first]
    assert [j.name for j in _editionless(game)] == ["Joker", "Misprint"]


def test_the_row_stamps_age_not_the_shop():
    """A shop builds a joker for every shelf slot and most are never bought,
    so a JokerInstance on its own has no age yet -- it gets one when it joins
    the row. Stamping at construction ordered the jokers by which shelf they
    sat on, which is not the order the game's counter gives them."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    shelved = JokerInstance(JOKER_REGISTRY["Joker"])
    assert shelved.uid == 0, "an unbought joker should not have an age"
    game.gain_joker(shelved)
    assert shelved.uid > 0


def test_a_copy_is_younger_than_its_original():
    """Duplication in the game runs Card:init, so the copy takes the next
    sort_id and sorts after the original. Two jokers at the same age would
    leave the draw depending on how table.sort breaks ties."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    original = JokerInstance(JOKER_REGISTRY["Joker"])
    game.gain_joker(original)
    game.add_joker_copy(original)
    copy = game.jokers[-1]
    assert copy is not original
    assert copy.uid > original.uid


# ------------------------------------------------------------------
# a copier retriggers held cards too
# ------------------------------------------------------------------

def test_a_brainstorm_copying_a_mime_retriggers_held_cards():
    """held_triggers read each joker's own spec, so a copier offered no
    retrigger and the count came out one short. It failed quietly -- nothing
    raised, the number was merely smaller -- and it is the same count the
    end-of-round pass uses, so a held gold card paid less and a blue seal made
    fewer Planets.

    Recording 8 stopped on it: two gold Kings with red seals, held under a
    Mime with a Brainstorm copying it, paid $18 against the game's $24.
    """
    from balatro.cards import Card, Enhancement, Rank, Seal, Suit
    from balatro.scoring import held_triggers

    game = GameState(seed="TESTSEED", deck="Red Deck")
    card = Card(Rank.KING, Suit.DIAMONDS, enhancement=Enhancement.GOLD,
                seal=Seal.RED)

    game.gain_joker(JokerInstance(JOKER_REGISTRY["Mime"]))
    # base 1 + red seal 1 + Mime 1
    assert held_triggers(game, card) == 3

    game.gain_joker(JokerInstance(JOKER_REGISTRY["Brainstorm"]))
    # ...and the Brainstorm copies the leftmost joker, which is the Mime
    assert held_triggers(game, card) == 4, (
        "the copier did not retrigger; a gold card here pays $12, not $9")


def test_a_copier_with_nothing_to_copy_adds_no_retrigger():
    from balatro.cards import Card, Rank, Seal, Suit
    from balatro.scoring import held_triggers

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKER_REGISTRY["Brainstorm"]))
    card = Card(Rank.KING, Suit.DIAMONDS, seal=Seal.NONE)
    assert held_triggers(game, card) == 1
