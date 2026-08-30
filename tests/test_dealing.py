"""The same seed must deal the same cards.

This is the test the whole RNG effort was for. Getting every joker's
arithmetic right still leaves a simulator that plays a different game if it
deals a different hand, and a policy trained on it learns which cards turn up
in a world that does not exist -- the failure that is invisible because every
rule looks correct.

Three things have to line up, and each was wrong at first:

  build order   the game lays a deck out in the alphabetical order of its own
                card keys, which is not a playing order: suits run Clubs,
                Diamonds, Hearts, Spades, and within a suit the ranks run
                2..9 then Ace, Jack, King, Queen, Ten -- A, J, K, Q, T. The
                shuffle runs over that list, so a deck built any other way
                shuffles reproducibly into a different deck.
  pool name     the round's shuffle draws from "nr" .. ante, not from a name
                of our choosing. A different name is a different stream.
  draw end      cards are dealt from the *end* of the shuffled deck.

Reproducing the deal in Python -- rather than reading it off the engine --
is what makes a standalone simulator possible at all.
"""

import pytest

from balatro.cards import standard_deck
from balatro.rng import RunRng
from balatro_headless.runtime import HeadlessBalatro

SEEDS = ["TESTSEED", "ABCD1234", "7EVEN", "XYZZY", "VSEDGHNH"]

DECK_QUERY = '''(function()
    local t = {}
    for i, c in ipairs(%s) do
      t[#t+1] = string.format("%%d:%%s:%%s", c.sort_id,
                              tostring(c.base.value), tostring(c.base.suit))
    end
    return table.concat(t, " ") end)()'''


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


def _rows(engine, area):
    raw = engine.eval(DECK_QUERY % area)
    return [] if not raw.strip() else [r.split(":") for r in raw.split()]


def _start(engine, seed, deck="Red_Deck"):
    engine.execute('BOT.start_run({"%s","%s"}); api.pump(300)' % (seed, deck))


def _key(row):
    """A card as (rank, suit), which is how both sides can be compared."""
    return row[1], row[2]


_RANK_NAME = {"Two": "2", "Three": "3", "Four": "4", "Five": "5", "Six": "6",
              "Seven": "7", "Eight": "8", "Nine": "9", "Ten": "10",
              "Jack": "Jack", "Queen": "Queen", "King": "King", "Ace": "Ace"}


@pytest.mark.parametrize("seed", SEEDS)
def test_python_deals_the_hand_the_engine_deals(engine, seed):
    """From the seed alone -- nothing about the deck is read off the engine.

    This is the standalone claim. Building the deck in Python, shuffling it
    with the run's own generator and taking eight from the end has to produce
    the cards the real game put in the player's hand, or a simulator trained
    against is playing a different game however exact its scoring.
    """
    _start(engine, seed)
    engine.execute("api.select_blind(); api.pump(200)")
    hand = _rows(engine, "G.hand.cards")
    assert len(hand) == 8

    deck = standard_deck()
    RunRng(seed).shuffle(deck, "nr1")
    dealt = sorted((_RANK_NAME[c.rank.name.title()], c.suit.name.title())
                   for c in deck[-8:])
    assert dealt == sorted(_key(r) for r in hand)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_deck_is_built_in_the_games_order(engine, seed):
    """standard_deck must match the engine's deck in sort_id order.

    Without this the shuffle is applied to a different list and every hand of
    the run differs, however exact the generator is.
    """
    _start(engine, seed)
    ordered = sorted(_rows(engine, "G.deck.cards"), key=lambda r: int(r[0]))
    engine_deck = [_key(r) for r in ordered]

    ranks = {"Two": "2", "Three": "3", "Four": "4", "Five": "5", "Six": "6",
             "Seven": "7", "Eight": "8", "Nine": "9", "Ten": "10",
             "Jack": "Jack", "Queen": "Queen", "King": "King", "Ace": "Ace"}
    sim_deck = [(ranks[c.rank.name.title()], c.suit.name.title())
                for c in standard_deck()]
    assert sim_deck == engine_deck


def test_a_different_seed_deals_a_different_hand(engine):
    """Guard against the comparison passing because nothing is shuffled."""
    hands = []
    for seed in SEEDS[:3]:
        _start(engine, seed)
        engine.execute("api.select_blind(); api.pump(200)")
        hands.append(tuple(sorted(_key(r) for r in _rows(engine, "G.hand.cards"))))
    assert len(set(hands)) == len(hands), "seeds produced identical hands"


def test_the_same_seed_deals_the_same_hand_twice(engine):
    """And that the engine itself is deterministic, which the rest assumes."""
    seen = []
    for _ in range(2):
        _start(engine, "TESTSEED")
        engine.execute("api.select_blind(); api.pump(200)")
        seen.append(tuple(sorted(_key(r) for r in _rows(engine, "G.hand.cards"))))
    assert seen[0] == seen[1]
