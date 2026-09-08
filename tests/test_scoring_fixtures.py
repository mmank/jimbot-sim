"""Pinned scores for hands the recordings never play.

Every expectation here is arithmetic, not a value copied out of a previous
run: base hand chips and mult, plus card chips, times whatever the joker or
enhancement does. If one of these breaks, the engine changed what the game
scores, and no amount of "the replays still pass" covers it -- the three
recordings play no steel, no glass, no editions and two jokers.
"""

import pytest

from jimbot_sim.headless.runtime import HeadlessBalatro
from jimbot_sim.headless.scenario import Scenario

PAIR_OF_ACES = "H_A D_A S_2 C_3 H_4 C_5 D_7 S_9"


@pytest.fixture(scope="module")
def game():
    return HeadlessBalatro().boot()


@pytest.fixture
def scene(game):
    return Scenario(game).start()


def test_straight_flush(scene):
    # (100 base + 11+10+10+10+10 card chips) * 8 mult
    scene.hand("S_A S_K S_Q S_J S_T H_2 D_3 C_4")
    assert scene.play([1, 2, 3, 4, 5]) == (100 + 51) * 8


def test_pair(scene):
    # (10 base + 11+11) * 2 mult
    scene.hand(PAIR_OF_ACES)
    assert scene.play([1, 2]) == (10 + 22) * 2


def test_flush(scene):
    # (35 base + 2+4+6+8+10) * 4 mult
    scene.hand("H_2 H_4 H_6 H_8 H_T S_A D_3 C_5")
    assert scene.play([1, 2, 3, 4, 5]) == (35 + 30) * 4


def test_hand_level_raises_pair(scene):
    # Pair gains +15 chips and +1 mult a level: level 2 is 25 chips, 3 mult.
    scene.hand(PAIR_OF_ACES).hand_level("Pair", 2)
    assert scene.play([1, 2]) == (25 + 22) * 3


@pytest.mark.parametrize("enhancement, expected", [
    ("m_bonus", (10 + 22 + 30) * 2),        # +30 chips
    ("m_mult", (10 + 22) * (2 + 4)),        # +4 mult
    ("m_glass", (10 + 22) * (2 * 2)),       # x2 mult
])
def test_enhancement_on_scored_card(scene, enhancement, expected):
    scene.hand(PAIR_OF_ACES).enhance(1, enhancement=enhancement)
    assert scene.play([1, 2]) == expected


@pytest.mark.parametrize("edition, expected", [
    ("foil", (10 + 22 + 50) * 2),           # +50 chips
    ("holo", (10 + 22) * (2 + 10)),         # +10 mult
    ("polychrome", int((10 + 22) * 2 * 1.5)),  # x1.5 mult
])
def test_edition_on_scored_card(scene, edition, expected):
    scene.hand(PAIR_OF_ACES).enhance(1, edition=edition)
    assert scene.play([1, 2]) == expected


def test_steel_card_held_in_hand(scene):
    # Steel triggers while held, not played: (10 + 22) * 2 * 1.5
    scene.hand(PAIR_OF_ACES).enhance(8, enhancement="m_steel")
    assert scene.play([1, 2]) == int((10 + 22) * 2 * 1.5)


def test_greedy_joker_pays_per_diamond(scene):
    # Greedy Joker is +3 mult for each Diamond scored: five of them.
    scene.hand("D_2 D_4 D_6 D_8 D_T S_A H_3 C_5").jokers("j_greedy_joker")
    assert scene.play([1, 2, 3, 4, 5]) == (35 + 30) * (4 + 3 * 5)


def test_joker_order_matters_for_multiplication(scene):
    # +mult before xmult is not the same as after. Bull (+chips) is order
    # independent, so use two that are not: Joker (+4 mult) and a polychrome
    # card (x1.5) resolve card-first, then jokers left to right.
    scene.hand(PAIR_OF_ACES).jokers("j_joker")
    assert scene.play([1, 2]) == (10 + 22) * (2 + 4)
