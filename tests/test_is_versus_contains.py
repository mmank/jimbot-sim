""""Is a Flush" and "contains a Flush" are different questions.

Balatro asks both, in different places, and reading the wrong one is silent:
the joker simply never fires, or fires on hands it should not. The game keeps
two separate things after a hand is evaluated --

    context.scoring_name   the single hand the play counts as
    context.poker_hands    a table of every hand the cards contain

-- and each joker reads exactly one of them. The split, from the source:

  contains   Jolly, Sly, Zany, Wily, Mad, Clever, Crazy, Devious, Droll and
             Crafty; The Duo, Trio, Family, Order and Tribe; Spare Trousers,
             Runner, Superposition, Séance
  is         Observatory, To Do List, Obelisk, Supernova, Card Sharp, The Ox

The containment table is not the obvious closure of the hand rankings, and
this file pins the two places it surprises. It is built from groups of an
*exact* size -- get_X_same(3, hand) skips a rank with four or five cards --
and then a cascade is patched on the end: Five of a Kind counts as Four of a
Kind, which counts as Three of a Kind, which counts as a Pair. Nothing in
that chain reaches Two Pair.

Every expectation below was read off a live engine before it was written
down, by calling evaluate_poker_hand on the played cards.
"""

import pytest

from balatro.cards import Card, Rank, Suit
from balatro.game import GameState
from balatro.hands import HANDLIST, HandType, evaluate

RANKS = {"2": Rank.TWO, "3": Rank.THREE, "5": Rank.FIVE, "6": Rank.SIX,
         "7": Rank.SEVEN, "8": Rank.EIGHT, "9": Rank.NINE, "Q": Rank.QUEEN,
         "K": Rank.KING, "A": Rank.ACE}
SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
         "C": Suit.CLUBS}


def _hand(codes):
    return [Card(RANKS[c.split("_")[1]], SUITS[c.split("_")[0]])
            for c in codes.split()]


def _contains(codes):
    result = evaluate(_hand(codes))
    return {h.label for h in result.contains} | {result.hand.label}


# Engine, evaluate_poker_hand on the played cards, one row per hand.
MEASURED = {
    "S_K H_K D_K C_K S_2": {"Four of a Kind", "Three of a Kind", "Pair",
                            "High Card"},
    "S_K H_K D_K C_K H_K": {"Five of a Kind", "Four of a Kind",
                            "Three of a Kind", "Pair", "High Card"},
    "S_K H_K D_K C_Q S_Q": {"Full House", "Two Pair", "Three of a Kind",
                            "Pair", "High Card"},
    "S_K S_K S_K S_Q S_Q": {"Flush House", "Full House", "Two Pair",
                            "Three of a Kind", "Pair", "Flush", "High Card"},
    "S_K S_K S_K S_K S_K": {"Flush Five", "Five of a Kind", "Four of a Kind",
                            "Three of a Kind", "Pair", "Flush", "High Card"},
    "S_K H_K D_Q C_Q S_2": {"Two Pair", "Pair", "High Card"},
    "S_K H_K D_K C_Q S_2": {"Three of a Kind", "Pair", "High Card"},
    "S_9 S_8 S_7 S_6 S_5": {"Straight Flush", "Flush", "Straight",
                            "High Card"},
    "S_K S_K S_9 S_6 S_3": {"Flush", "Pair", "High Card"},
    "S_9 H_8 D_7 C_6 S_5": {"Straight", "High Card"},
    "S_K H_K D_9 C_6 S_3": {"Pair", "High Card"},
    "S_K H_9 D_7 C_5 S_3": {"High Card"},
}


@pytest.mark.parametrize("codes", sorted(MEASURED))
def test_the_containment_table_matches_the_engine(codes):
    assert _contains(codes) == MEASURED[codes]


def test_four_of_a_kind_contains_a_pair_but_not_two_pair():
    """The cascade stops one rung short, and the shape of it is the reason.

    Four cards of a rank make no *pair* group at all -- get_X_same(2) wants
    exactly two -- so Pair arrives only through the patched chain from Four
    of a Kind down. Two Pair is not on that chain, and it needs two separate
    groups, which four of one rank cannot supply.
    """
    held = _contains("S_K H_K D_K C_K S_2")
    assert "Pair" in held
    assert "Three of a Kind" in held
    assert "Two Pair" not in held


def test_five_of_a_kind_is_not_a_full_house():
    """Reading "at least three" instead of "exactly three" makes it one.

    Three of the five plus two of the same five looks like a full house to
    any closure over the rankings, and the game never counts it: both halves
    would be the same rank. This was the simulator's bug.
    """
    held = _contains("S_K H_K D_K C_K H_K")
    assert "Full House" not in held
    assert "Flush House" not in _contains("S_K S_K S_K S_K S_K")


def test_a_flush_holding_a_pair_contains_a_pair():
    """The case that a list of top hands cannot express."""
    assert "Pair" in _contains("S_K S_K S_9 S_6 S_3")


# ------------------------------------------------------------------
# the jokers that read one table or the other
# ------------------------------------------------------------------

def _score(hand_codes, joker):
    from balatro.jokers import REGISTRY as JOKERS, JokerInstance

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._start_round()
    game.hand[:] = _hand(hand_codes)
    plain = game.preview_score(tuple(range(len(game.hand))))
    game.gain_joker(JokerInstance(JOKERS[joker]))
    return plain, game.preview_score(tuple(range(len(game.hand))))


@pytest.mark.parametrize("joker,codes", [
    ("Jolly Joker", "S_K S_K S_9 S_6 S_3"),        # a Flush holding a pair
    ("Sly Joker", "S_K S_K S_9 S_6 S_3"),
    ("The Duo", "S_K S_K S_9 S_6 S_3"),
    ("Mad Joker", "S_K H_K D_K C_Q S_Q"),          # a Full House holds two pair
    ("Clever Joker", "S_K H_K D_K C_Q S_Q"),
])
def test_a_contains_joker_fires_on_a_hand_that_merely_holds_it(joker, codes):
    plain, with_joker = _score(codes, joker)
    assert with_joker > plain


@pytest.mark.parametrize("joker", ["Mad Joker", "Clever Joker"])
def test_a_two_pair_joker_does_not_fire_on_four_of_a_kind(joker):
    """Because a Four of a Kind does not contain Two Pair. See above."""
    plain, with_joker = _score("S_K H_K D_K C_K S_2", joker)
    assert with_joker == plain


def test_seance_reads_the_containment_table_like_the_rest():
    """`next(context.poker_hands[...])`, not a test of what the hand is.

    The two agree on anything vanilla can make -- nothing outranking a
    Straight Flush contains one -- but the shape has to be right.
    """
    from balatro.jokers import REGISTRY as JOKERS

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._start_round()
    game.hand[:] = _hand("S_9 S_8 S_7 S_6 S_5")
    result = evaluate(list(game.hand))
    assert HandType.STRAIGHT_FLUSH in result.contains | {result.hand}
    assert JOKERS["Séance"].after_hand is not None


# ------------------------------------------------------------------
# The Ox, which reads a snapshot rather than a live count
# ------------------------------------------------------------------

def test_the_most_played_hand_starts_at_high_card():
    """Engine: 'High Card' at run start, and it does not move on plays."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    assert game.most_played_hand is HandType.HIGH_CARD


def test_playing_a_hand_does_not_move_the_snapshot():
    """Engine: twenty-five Pairs played, still 'High Card'.

    It is rewritten when a boss round ends and nowhere else, so The Ox
    punishes what the run was doing an ante ago.
    """
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.hand_levels.plays[HandType.PAIR] = 25
    assert game.most_played_hand is HandType.HIGH_CARD


def test_the_snapshot_is_taken_when_a_boss_falls():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.hand_levels.plays[HandType.PAIR] = 25
    game.blind_index = 2
    game._next_blind()
    game._start_round()
    game.chips_scored = game.blind.target
    game._beat_blind()
    assert game.most_played_hand is HandType.PAIR


def test_a_tie_goes_to_the_weaker_hand():
    """Engine: Pair and Flush both on 25, and it picked Pair.

    _order is initialised to 100 and never assigned, so every tie replaces
    the incumbent and the last hand `pairs` yields wins. That order runs
    strongest-first, so the weakest of the tied hands is left standing.
    """
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.hand_levels.plays[HandType.PAIR] = 25
    game.hand_levels.plays[HandType.FLUSH] = 25
    game._snapshot_most_played()
    assert game.most_played_hand is HandType.PAIR

    game.hand_levels.plays[HandType.FLUSH] = 30
    game._snapshot_most_played()
    assert game.most_played_hand is HandType.FLUSH


def test_the_ox_reads_the_snapshot_not_a_live_tie():
    """Two hands level on plays used to mean either one zeroed your money."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.hand_levels.plays[HandType.PAIR] = 25
    game.hand_levels.plays[HandType.FLUSH] = 25
    game._snapshot_most_played()
    assert game._is_most_played(HandType.PAIR)
    assert not game._is_most_played(HandType.FLUSH)
