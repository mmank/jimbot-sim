"""Flower Pot and Seeing Double against the engine, hand for hand.

test_flower_pot_seeing_double_suits pins the simulator to numbers; this reads
them off the engine, so the numbers are measured rather than remembered. Each
case plays the same hand on a fresh run with the same jokers, and the two
scores must be equal. See that module for the rule (card.lua:3808-3866).
"""

import pytest

pytest.importorskip("lupa")

from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available  # noqa: E402
from jimbot_sim.headless.scenario import Scenario  # noqa: E402

from test_flower_pot_seeing_double_suits import CASES, sim_score  # noqa: E402

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not engine_available(),
                       reason="no Balatro engine: need vendor/balatro_src"),
]

_KEYS = {"Flower Pot": "j_flower_pot", "Seeing Double": "j_seeing_double",
         "Smeared Joker": "j_smeared"}
_BOSSES = {"The Window": "bl_window"}


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


@pytest.mark.parametrize("label, hand, play, jokers, boss, wild, recorded",
                         CASES, ids=[c[0] for c in CASES])
def test_simulator_scores_what_the_engine_scores(engine, label, hand, play,
                                                 jokers, boss, wild, recorded):
    scene = Scenario(engine).start()
    if boss:
        # Before the hand: set_blind debuffs every playing card
        # (blind.lua:207-210) and set_base asks each again (card.lua:143).
        scene.boss(_BOSSES[boss])
    scene.hand(hand)
    for index in wild:
        scene.enhance(index, "m_wild")
    scene.jokers(" ".join(_KEYS[name] for name in jokers))
    measured = scene.play(play)
    assert measured == recorded
    assert sim_score(hand, play, jokers, boss, wild) == measured
