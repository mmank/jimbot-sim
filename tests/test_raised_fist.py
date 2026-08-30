"""Raised Fist, which depends on where a card sits rather than what it is.

It pays double the nominal value of the lowest card held in hand, and every
interesting part of that is in the details:

  ties          the game walks the hand front to back keeping any card whose
                id is at most the best so far, so the *rightmost* of the
                equal-lowest wins. Taking the first minimum gives the same
                number until the two tied cards differ.
  stone         skipped entirely, having no rank, so the next lowest is used.
  steel         not skipped. A Steel card can be the lowest, and then it both
                points Raised Fist at itself and multiplies the mult.
  debuffed      if the chosen card is debuffed the joker pays nothing at all;
                it does not fall through to the next lowest.
  Mime          this is a held-card trigger, not an independent one, so Mime
                repeats it -- which is only true because of where the effect
                is attached, and would not follow from the joker's text.

These are hand-checkable: two kings score (10 + 20) x 2 = 60 on their own, so
every expectation below is that baseline plus a stated amount of mult.
"""

from dataclasses import dataclass

import pytest

from balatro.cards import Card, Rank, Suit, standard_deck
from balatro.game import GameState
from balatro.hands import evaluate
from balatro.jokers import make
from balatro.rng import RunRng
from balatro.scoring import score_hand
from balatro_headless.runtime import HeadlessBalatro
from balatro_headless.scenario import Scenario

_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT, "9": Rank.NINE,
          "T": Rank.TEN, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
          "A": Rank.ACE}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}
_ENH = {"steel": "m_steel", "stone": "m_stone", "gold": "m_gold"}

PLAY = (1, 2)          # two kings, in every case below


def _card(code, enhancement=""):
    from balatro.cards import Enhancement
    suit, rank = code.split("_")
    card = Card(_RANKS[rank], _SUITS[suit])
    if enhancement:
        card.enhancement = getattr(Enhancement, enhancement.upper())
    return card


@dataclass(frozen=True)
class Case:
    name: str
    hand: str                  # eight codes; the first two are played
    enhance: tuple = ()        # (index, enhancement) pairs, 1-based
    jokers: tuple = ("Raised Fist",)
    boss: str = ""


CASES = [
    Case("lone lowest card", "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4"),
    # Two 2s, so the tie-break decides which card is pointed at. The score is
    # the same either way; test_mime_doubles_the_same_card is what separates
    # them, and the boss case below.
    Case("tied lowest cards", "S_K H_K D_2 C_2 H_7 S_9 D_3 C_4"),
    Case("lowest is a steel card", "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4",
         enhance=((3, "steel"),)),
    # A stone card has no rank, so Raised Fist should look past it to the 3.
    Case("lowest is a stone card", "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4",
         enhance=((3, "stone"),)),
    Case("every low card is stone", "S_K H_K D_2 C_3 H_7 S_9 D_4 C_5",
         enhance=((3, "stone"), (4, "stone"), (7, "stone"))),
    Case("with Mime", "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4",
         jokers=("Raised Fist", "Mime")),
    Case("steel lowest, with Mime", "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4",
         enhance=((3, "steel"),), jokers=("Raised Fist", "Mime")),
    Case("tied lowest, with Mime", "S_K H_K D_2 C_2 H_7 S_9 D_3 C_4",
         jokers=("Raised Fist", "Mime")),
    # The Club debuffs clubs; with a club as the equal-lowest card the
    # tie-break becomes visible, because a debuffed choice pays nothing.
    Case("tied lowest, one debuffed", "S_K H_K D_2 C_2 H_7 S_9 D_3 C_4",
         boss="bl_club"),
    Case("lowest is debuffed", "S_K H_K C_2 D_5 H_7 S_9 D_3 C_4",
         boss="bl_club"),
    # The same two tied 2s with their positions swapped. Under The Club the
    # club one is debuffed, so which of them the joker points at decides
    # whether it pays at all -- and the pair differ only in order.
    Case("tie broken to the right", "S_K H_K D_2 C_2 H_7 S_9 D_3 C_4",
         boss="bl_club"),
    Case("tie broken to the left", "S_K H_K C_2 D_2 H_7 S_9 D_3 C_4",
         boss="bl_club"),
]

JOKER_KEYS = {"Raised Fist": "j_raised_fist", "Mime": "j_mime"}


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


def _engine_score(engine, case):
    scene = Scenario(engine).start()
    if case.boss:
        scene.boss(case.boss)
    scene.hand(case.hand)
    for index, enhancement in case.enhance:
        scene.enhance(index, enhancement=_ENH[enhancement])
    scene.jokers(" ".join(JOKER_KEYS[n] for n in case.jokers))
    scene.select(PLAY)
    read = lambda e: int(engine.eval("(function() return %s end)()" % e))
    state = {
        "discards_left": read("G.GAME.current_round.discards_left"),
        "hands_left": read("G.GAME.current_round.hands_left") - 1,
        "money": read("G.GAME.dollars"),
        "draw_pile": read("#G.deck.cards"),
        "hands_played": read("G.GAME.hands_played"),
        "rng_seed": engine.eval(
            "(function() return tostring(G.GAME.pseudorandom.seed) end)()"),
    }
    return scene.play(), state


def _sim_score(case, state):
    game = GameState(seed=0)
    game.rng = RunRng(state["rng_seed"])
    game.jokers = [make(n) for n in case.jokers]
    codes = case.hand.split()
    enhancements = dict(case.enhance)
    cards = [_card(c, enhancements.get(i, ""))
             for i, c in enumerate(codes, 1)]
    played = [cards[i - 1] for i in PLAY]
    held = [c for i, c in enumerate(cards, 1) if i not in PLAY]
    game.hand = played + held
    game.discards_left = state["discards_left"]
    game.hands_left = state["hands_left"]
    game.money = state["money"]
    game.hands_played = state["hands_played"]
    game.draw_pile = standard_deck()[:state["draw_pile"]]
    game.full_deck = game.hand + game.draw_pile
    if case.boss:
        from balatro.blinds import BOSSES, Blind, BlindKind
        effect = {b.name: b for b in BOSSES}[
            "The Club" if case.boss == "bl_club" else case.boss]
        game.blind = Blind(BlindKind.BOSS, ante=1, target=999999999,
                           reward=5, boss=effect)
    game._apply_debuffs()
    result = evaluate(played)
    game.hand_levels.plays[result.hand] = 1
    return score_hand(game, result, played, held).score


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_engines_agree(engine, case):
    expected, state = _engine_score(engine, case)
    assert expected == _sim_score(case, state), (
        "%s: engine %d, simulator %d"
        % (case.name, expected, _sim_score(case, state)))


def test_it_pays_double_the_lowest_nominal(engine):
    """(10 + 20) x (2 + 2*2) = 180 with a 2 as the lowest held card."""
    score, _ = _engine_score(engine, CASES[0])
    assert score == (10 + 20) * (2 + 2 * 2)


def test_a_stone_card_is_skipped(engine):
    """With the 2 turned to stone the 3 becomes lowest: 2*3 mult, not 2*2."""
    stone = next(c for c in CASES if c.name == "lowest is a stone card")
    score, _ = _engine_score(engine, stone)
    assert score == (10 + 20) * (2 + 2 * 3)


def test_a_steel_card_can_be_the_lowest(engine):
    """Steel is not skipped, and it multiplies *before* Raised Fist adds.

    Both effects fire on the same held card, and the card's own enhancement
    goes first: 2 mult x 1.5 for the Steel, then +4 for Raised Fist, giving
    (10 + 20) x 7 = 210. Applying the joker first and the Steel afterwards
    reads just as plausibly and gives 270, which is the sort of ordering a
    reimplementation gets wrong and a test of averages never catches.
    """
    steel = next(c for c in CASES if c.name == "lowest is a steel card")
    score, _ = _engine_score(engine, steel)
    assert score == int((10 + 20) * (2 * 1.5 + 2 * 2))


def test_mime_doubles_the_same_card(engine):
    """Mime repeats the held trigger, so the mult is added twice."""
    plain, _ = _engine_score(engine, CASES[0])
    mimed, _ = _engine_score(
        engine, next(c for c in CASES if c.name == "with Mime"))
    assert plain == (10 + 20) * (2 + 2 * 2)
    assert mimed == (10 + 20) * (2 + 2 * 2 + 2 * 2)


def test_a_debuffed_lowest_card_pays_nothing(engine):
    """It does not fall through to the next lowest card."""
    case = next(c for c in CASES if c.name == "lowest is debuffed")
    score, _ = _engine_score(engine, case)
    # The club 2 is debuffed and still chosen, so no Raised Fist mult at all.
    assert score == (10 + 20) * 2


def test_the_rightmost_of_the_tied_lowest_cards_wins(engine):
    """Two 2s, swapped, with The Club debuffing one of them.

    This is the whole tie-break in one comparison. The hands are identical
    apart from the order of the two tied cards, so only the direction of the
    tie-break can change the score -- and it changes it by a factor of three.
    Reading the game's loop as "first minimum" rather than "last" produces
    exactly these two numbers the other way round.
    """
    right = next(c for c in CASES if c.name == "tie broken to the right")
    left = next(c for c in CASES if c.name == "tie broken to the left")
    # Club 2 on the right: it is chosen, it is debuffed, nothing is paid.
    assert _engine_score(engine, right)[0] == (10 + 20) * 2
    # Club 2 on the left: the diamond 2 is chosen instead and pays 2 x 2.
    assert _engine_score(engine, left)[0] == (10 + 20) * (2 + 2 * 2)
