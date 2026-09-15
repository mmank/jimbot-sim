"""Shoot the Moon pays for a held Queen by id, and a Stone Queen has none.

card.lua:3272-3273 asks `context.other_card:get_id() == 12`, and get_id gives
a Stone card a random negative (card.lua:958-960), so a Stone Queen held in
hand adds no mult. The simulator matched on the printed rank and paid +13 for
it; Baron already had the Stone check for Kings.
"""

from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY, JokerInstance
from jimbot_sim.scoring import score_hand


def _score(held):
    game = GameState(seed="MOONSTNE", deck="Red Deck")
    game.gain_joker(JokerInstance(REGISTRY["Shoot the Moon"]))
    played = [Card(Rank.TWO, Suit.SPADES)]
    return score_hand(game, game.evaluate_selection(played), played, held).score


def test_a_held_queen_gives_thirteen_mult():
    assert _score([Card(Rank.QUEEN, Suit.HEARTS)]) > _score([])


def test_a_held_stone_queen_gives_nothing():
    stone = Card(Rank.QUEEN, Suit.HEARTS, enhancement=Enhancement.STONE)
    assert _score([stone]) == _score([])
