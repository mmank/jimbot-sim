"""Vagabond reads the money the hand was played with, not what it pays.

card.lua:3743-3744 asks `G.GAME.dollars <= extra` in joker_main, and every
payout the hand makes -- a Gold Seal's $3, Matador's $8 -- is an
`ease_dollars` event still queued at that point. Read after the hand, a
Matador's $8 stopped the tarot the game made: seed FATMAN06, Yellow Deck,
decision 23, a lone Ace of Clubs into The Club with $0 -- the game made The
Devil and the simulator nothing.
"""

from __future__ import annotations

from jimbot_sim.cards import Seal
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance


def _game(money):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Vagabond"]))
    game._start_round()
    game.money = money
    game.consumables = []
    return game


def test_a_gold_seal_paid_in_the_hand_does_not_stop_the_tarot():
    game = _game(2)
    game.hand[0].seal = Seal.GOLD
    game._play((0,))
    assert game.money == 5
    assert len(game.consumables) == 1


def test_five_dollars_held_makes_none():
    game = _game(5)
    game._play((0,))
    assert len(game.consumables) == 0
