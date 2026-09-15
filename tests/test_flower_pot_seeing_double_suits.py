"""Flower Pot and Seeing Double count suits the way card.lua:3808-3866 does.

Both tally the scoring hand into four suit counters, in two passes -- every
card that is not a Wild card first, then the Wild cards -- and the passes ask
Card:is_suit (card.lua:4064-4089) differently:

  Flower Pot, non-Wild   `is_suit(s, true)`, bypass_debuff, in an elseif
                         chain Hearts, Diamonds, Spades, Clubs that stops at
                         the first suit still at zero: a debuffed card counts
                         its suit, and under Smeared Joker a second Heart
                         fills Diamonds.
  Flower Pot, Wild       `is_suit(s)`, same chain: a Wild fills ONE empty
                         suit, and a debuffed Wild fills none.
  Seeing Double, non-Wild `is_suit(s)` for all four, no chain: under Smeared
                         Joker a Spade is a Club as well.
  Seeing Double, Wild    `is_suit(s)`, chain Clubs, Diamonds, Spades, Hearts.

The simulator asked `counts_as_suit` for every suit instead, which lets a
Wild card fill all four, refuses a debuffed card outright, and knows nothing
of Smeared Joker. The numbers are the headless engine's, measured with
Scenario on a fresh run (test_flower_pot_seeing_double_suits_engine).
"""

import pytest

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.hands import evaluate
from jimbot_sim.jokers import make
from jimbot_sim.scoring import score_hand

BY_NAME = {b.name: b for b in BOSSES}
_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT, "9": Rank.NINE,
          "T": Rank.TEN, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
          "A": Rank.ACE}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}


def sim_score(hand, play, jokers, boss=None, wild=(), stone=()):
    """Score `play` (1-based) out of `hand` with exactly these jokers."""
    game = GameState(seed=0)
    cards = []
    for i, code in enumerate(hand.split(), start=1):
        suit, rank = code.split("_")
        card = Card(_RANKS[rank], _SUITS[suit])
        if i in wild:
            card.enhancement = Enhancement.WILD
        if i in stone:
            card.enhancement = Enhancement.STONE
        cards.append(card)
    game.jokers = [make(name) for name in jokers]
    played = [cards[i - 1] for i in play]
    held = [c for i, c in enumerate(cards, start=1) if i not in play]
    game.hand = played + held
    game.full_deck = list(game.hand)
    if boss:
        game.blind = make_blind(BlindKind.BOSS, game.ante, BY_NAME[boss])
    game._apply_debuffs()
    result = evaluate(played, splash=game._splash(),
                      smeared=game.has_smeared(),
                      four_fingers=game._four_fingers(),
                      shortcut=game._shortcut())
    return score_hand(game, result, played, held).score


# label, hand, play, jokers (simulator names), boss, wild, engine score
CASES = [
    ("pot_wild_fills_one_suit", "H_7 S_7 C_7 D_2 D_3 S_4 H_5 C_9", (1, 2, 3),
     ["Flower Pot"], None, (3,), 153),
    ("pot_debuffed_diamond_counts", "H_9 D_9 S_4 C_4 H_2 S_3 C_6 S_8",
     (1, 2, 3, 4), ["Flower Pot"], "The Window", (), 222),
    ("pot_smeared_heart_fills_diamonds", "H_9 H_9 S_4 C_4 H_2 S_3 C_6 S_8",
     (1, 2, 3, 4), ["Smeared Joker", "Flower Pot"], None, (), 276),
    ("pot_four_plain_suits", "H_9 D_9 S_4 C_4 H_2 S_3 C_6 S_8", (1, 2, 3, 4),
     ["Flower Pot"], None, (), 276),
    ("double_smeared_spades_are_clubs", "S_9 S_9 D_4 H_5 H_2 D_3 H_6 D_8",
     (1, 2), ["Smeared Joker", "Seeing Double"], None, (), 112),
    ("double_club_and_wild", "C_9 H_9 D_4 H_5 H_2 D_3 H_6 D_8", (1, 2),
     ["Seeing Double"], None, (2,), 112),
    ("double_two_wilds", "C_9 H_9 D_4 H_5 H_2 D_3 H_6 D_8", (1, 2),
     ["Seeing Double"], None, (1, 2), 112),
    ("double_debuffed_diamond_does_not_count", "C_9 D_9 S_4 H_5 H_2 S_3 H_6 S_8",
     (1, 2), ["Seeing Double"], "The Window", (), 38),
]


@pytest.mark.parametrize("label, hand, play, jokers, boss, wild, engine",
                         CASES, ids=[c[0] for c in CASES])
def test_matches_the_engine(label, hand, play, jokers, boss, wild, engine):
    assert sim_score(hand, play, jokers, boss, wild) == engine


def test_flower_pot_ignores_a_debuffed_wild():
    """The Wild pass asks without bypass_debuff (card.lua:3824-3827), and the
    Wild card is skipped by the first pass, so a debuffed Wild -- The Window
    debuffs every Wild card (blind.lua:626, card.lua:4081) -- fills nothing.
    Hearts, Spades and Clubs are there; Diamonds never is."""
    hand = "H_7 S_7 C_7 D_7 D_3 S_4 H_5 C_9"
    with_pot = sim_score(hand, (1, 2, 3, 4), ["Flower Pot"], "The Window",
                         wild=(4,))
    without = sim_score(hand, (1, 2, 3, 4), [], "The Window", wild=(4,))
    assert with_pot == without


def test_flower_pot_stone_card_has_no_suit():
    """`if self.ability.effect == 'Stone Card' then return false end`
    (card.lua:4078), bypass or not."""
    hand = "H_7 S_7 C_7 D_7 D_3 S_4 H_5 C_9"
    with_pot = sim_score(hand, (1, 2, 3, 4), ["Flower Pot"], stone=(4,))
    without = sim_score(hand, (1, 2, 3, 4), [], stone=(4,))
    assert with_pot == without
