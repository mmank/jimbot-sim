"""The Flint halves the base a Space Joker has already levelled.

G.FUNCS.evaluate_play reads the played hand's base chips and mult twice:
once before the jokers' `before` pass (state_events.lua:615-616) and again
after it (state_events.lua:640-641). Only the second reading goes through
Blind:modify_hand (state_events.lua:645-646), where The Flint rounds both
halves (blind.lua:512-514):

    math.max(math.floor(mult*0.5 + 0.5), 1),
    math.max(math.floor(hand_chips*0.5 + 0.5), 0)

Space Joker's level-up is a `before` effect (card.lua:3420-3426, applied by
level_up_hand at state_events.lua:634-635), so the game halves the *new*
level. The simulator read and halved the old level first, then had Space
Joker add the level's gain whole -- so into The Flint every Space Joker hit
scored half a level of chips and mult too many, and the rounding of the
halves came out of the wrong numbers as well.

OCMTUFBK, Blue Deck, stake 3, ante 6: a Two Pair levelled from 9 to 10 by
Space Joker into The Flint. The game scored 214 x 74 = 15836 and the
simulator 224 x 74 = 16576: Two Pair gains 20 chips a level, and the game
halves it to 10.
"""

import pytest

from jimbot_sim.blinds import BOSSES, Blind, BlindKind
from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.hands import HandType, evaluate
from jimbot_sim.jokers import make
from jimbot_sim.scoring import score_hand

FLINT = next(b for b in BOSSES if b.name == "The Flint")

_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT, "9": Rank.NINE,
          "T": Rank.TEN, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
          "A": Rank.ACE}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}

TWO_PAIR = "S_K H_K D_9 C_9 H_7 S_2 D_3 C_4"
PAIR = "S_K H_K D_9 C_5 H_7 S_2 D_3 C_4"

# (hand codes, 1-based cards played, hand, level before, The Flint?,
#  Space Joker fires?, score, level after)
CASES = [
    # Level 10 is 200 x 11, halved to 100 x 6; K K 9 9 add 38 chips.
    # Halving level 9 and adding the gain gave (90 + 20 + 38) x (5 + 1) = 888.
    pytest.param(TWO_PAIR, (1, 2, 3, 4), HandType.TWO_PAIR, 9, True, True,
                 828, 10, id="two-pair-levelled-into-flint"),
    # Level 2 is 25 x 3, halved to 13 x 2 (12.5 rounds up); K K add 20.
    # Halving level 1 and adding the gain gave (5 + 15 + 20) x (1 + 1) = 80.
    pytest.param(PAIR, (1, 2), HandType.PAIR, 1, True, True,
                 66, 2, id="pair-levelled-into-flint"),
    # Unchanged: no level-up into The Flint, and a level-up with no Flint.
    pytest.param(TWO_PAIR, (1, 2, 3, 4), HandType.TWO_PAIR, 9, True, False,
                 640, 9, id="two-pair-flint-no-level"),
    pytest.param(TWO_PAIR, (1, 2, 3, 4), HandType.TWO_PAIR, 9, False, True,
                 2618, 10, id="two-pair-levelled-no-flint"),
]


def _cards(codes):
    out = []
    for code in codes.split():
        suit, rank = code.split("_")
        out.append(Card(_RANKS[rank], _SUITS[suit]))
    return out


def _sim(codes, play, hand, level, flint, fires):
    game = GameState(seed=0)
    game.jokers = [make("Space Joker")]
    if flint:
        game.blind = Blind(BlindKind.BOSS, ante=1, target=999999999,
                           reward=5, boss=FLINT)
    game.hand_levels.levels[hand] = level
    game.rng.chance = lambda key, numerator, denominator: (
        fires and key == "space")
    cards = _cards(codes)
    played = [cards[i - 1] for i in play]
    held = [c for i, c in enumerate(cards, start=1) if i not in play]
    result = evaluate(played)
    assert result.hand is hand
    ctx = score_hand(game, result, played, held)
    return ctx.score, game.hand_levels.levels[hand]


@pytest.mark.parametrize(
    "codes, play, hand, level, flint, fires, score, level_after", CASES)
def test_the_flint_halves_the_level_space_joker_left(
        codes, play, hand, level, flint, fires, score, level_after):
    assert _sim(codes, play, hand, level, flint, fires) == (score, level_after)


@pytest.fixture(scope="module")
def engine():
    from jimbot_sim.headless.runtime import HeadlessBalatro
    return HeadlessBalatro().boot()


_GAME_NAME = {HandType.TWO_PAIR: "Two Pair", HandType.PAIR: "Pair"}


@pytest.mark.slow
@pytest.mark.parametrize(
    "codes, play, hand, level, flint, fires, score, level_after", CASES)
def test_the_engine_agrees(engine, codes, play, hand, level, flint, fires,
                           score, level_after):
    """The same positions on the headless engine. G.GAME.probabilities.normal
    at 4 makes Space Joker's `pseudorandom('space') < normal/4` certain, and
    at 0 impossible (card.lua:3420)."""
    from jimbot_sim.headless.scenario import Scenario

    name = _GAME_NAME[hand]
    scene = Scenario(engine).start().hand(codes).jokers("j_space")
    if flint:
        scene.boss("bl_flint")
    scene.hand_level(name, level)
    engine.execute("G.GAME.probabilities.normal = %d" % (4 if fires else 0))
    got = scene.play(play)
    after = int(engine.eval(
        '(function() return G.GAME.hands["%s"].level end)()' % name))
    assert (got, after) == (score, level_after)
