"""Amber Acorn shuffles the joker row three times, each from id order.

blind.lua:195-201 queues three events, each `G.jokers:shuffle('aajk')`, and
CardArea:shuffle is pseudoshuffle, which sorts the list by sort_id before it
shuffles. So the order the player left the row in does not matter at all,
and the row that comes out is the third 'aajk' shuffle of the jokers in id
order. The simulator shuffled twice and never sorted; seed QWERTYUI, Blue
Deck, stake 1 reached an Amber Acorn on the headless engine at ante eight
and the game and the simulator held the same six jokers in two orders.
"""

from jimbot_sim.blinds import FINISHER_BOSSES, BlindKind, make_blind
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY, JokerInstance
from jimbot_sim.rng import RunRng

ACORN = next(b for b in FINISHER_BOSSES if b.name == "Amber Acorn")
NAMES = ["Joker", "Jolly Joker", "Zany Joker", "Mad Joker", "Crazy Joker"]


def _acorn_round(reverse_row: bool) -> GameState:
    game = GameState(seed="QWERTYUI", deck="Blue Deck", endless=True)
    for name in NAMES:
        game.gain_joker(JokerInstance(REGISTRY[name]))
    if reverse_row:
        game.jokers.reverse()
    game.blind = make_blind(BlindKind.BOSS, 8, ACORN)
    game._start_round()
    return game


def test_the_row_the_player_left_does_not_matter():
    as_bought = [j.name for j in _acorn_round(False).jokers]
    dragged = [j.name for j in _acorn_round(True).jokers]
    assert as_bought == dragged


def test_it_is_three_shuffles_of_the_id_order():
    game = _acorn_round(True)
    expected = sorted(game.jokers, key=lambda j: j.uid)
    rng = RunRng("QWERTYUI")
    for _ in range(3):
        expected.sort(key=lambda j: j.uid)
        rng.shuffle(expected, "aajk")
    assert [j.name for j in game.jokers] == [j.name for j in expected]
