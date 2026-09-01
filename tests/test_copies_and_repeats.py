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

    right.played_this_ante, left.played_this_ante = True, False
    CONSUMABLES["Death"].apply(game, [left, right])
    assert left.played_this_ante, "copying a spent card should spend it"

    right.played_this_ante, left.played_this_ante = False, True
    CONSUMABLES["Death"].apply(game, [left, right])
    assert not left.played_this_ante, "copying a fresh card should clear it"


def test_changing_an_enhancement_launders_a_spent_card():
    """And changing anything else does not, which is the surprising half.

    set_ability rebuilds the whole ability table from the new centre and
    carries across exactly two things -- perma_bonus and forced_selection --
    so played_this_ante goes. Suit, rank, edition and seal are written
    somewhere else entirely and leave it alone. A card The Pillar has
    debuffed can therefore be freed with a Chariot and not with a Sun.
    """
    from balatro.cards import Edition, Enhancement, Seal, Suit

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._start_round()
    card = game.hand[0]

    card.played_this_ante = True
    card.seal = Seal.RED
    card.edition = Edition.FOIL
    card.suit = Suit.HEARTS
    assert card.played_this_ante, "a seal, an edition and a suit change it not"

    game.set_enhancement(card, Enhancement.STEEL)
    assert not card.played_this_ante


def test_an_enhancement_change_keeps_the_hiker_chips():
    """The two fields set_ability does carry across."""
    from balatro.cards import Enhancement

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._start_round()
    card = game.hand[0]
    card.extra_chips = 15

    game.set_enhancement(card, Enhancement.GLASS)
    assert card.extra_chips == 15


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


def test_a_copied_joker_shares_the_registrys_spec():
    """A copy carries the joker's state, not a second copy of the rules.

    `gain_joker(copy.deepcopy(joker))` is how a duplicate joins the row, and
    a spec is what a joker *is* -- built once at import, pointed at by every
    instance in every game. Deep-copying it made a private set of the rules
    per copy: harmless to arithmetic, since the hooks behave identically, but
    it broke identity, so `spec is REGISTRY[name]` stopped holding, and it
    made a run impossible to serialise at all -- the hooks are closures over
    the joker's numbers, which pickle refuses outright.

    Both specs answer __reduce__ with their own name and are restored by
    lookup, so a copy points back at the registry.
    """
    import copy
    import pickle

    from balatro.jokers import REGISTRY as JOKERS
    from balatro.jokers import make

    joker = make("Blueprint")
    joker.counter = 7.0
    for copied in (copy.deepcopy(joker), pickle.loads(pickle.dumps(joker, -1))):
        assert copied.spec is JOKERS["Blueprint"], "the rules were copied"
        assert copied.counter == 7.0, "the joker's own state was not"
        assert copied is not joker

    fool = CONSUMABLES["The Fool"]
    assert copy.deepcopy(fool) is fool
    assert pickle.loads(pickle.dumps(fool, -1)) is fool
