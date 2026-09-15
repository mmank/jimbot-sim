"""A preview counts the Lucky rolls a play makes.

Lucky Cat gains X0.25 each time a Lucky card triggers and one of its rolls
hits (card.lua:3076-3081). The hit is random; the trigger is not. Hanging
Chad retriggers the first card scored twice, so a Lucky card at the front of
a play rolls three times and anywhere else once -- and that count is what a
policy farming a Lucky Cat can compare plays by, where a preview's score only
says how one draw of the rolls came out.
"""

from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY, JokerInstance


def _pair(*jokers):
    game = GameState(seed="AWEFRTUZ", deck="Blue Deck")
    for name in jokers:
        game.gain_joker(JokerInstance(REGISTRY[name]))
    game._start_round()
    lucky = Card(Rank.QUEEN, Suit.HEARTS)
    lucky.enhancement = Enhancement.LUCKY
    game.hand[:] = [Card(Rank.QUEEN, Suit.SPADES), lucky,
                    Card(Rank.TWO, Suit.CLUBS), Card(Rank.THREE, Suit.CLUBS)]
    return game


def test_a_lucky_card_rolls_once_a_trigger():
    game = _pair()
    assert game.preview_outcome((0, 1))[2] == 1
    assert game.preview_outcome((2, 3))[2] == 0


def test_hanging_chad_on_a_lucky_card_is_three_rolls():
    game = _pair("Hanging Chad")
    assert game.preview_outcome((1, 0))[2] == 3
    assert game.preview_outcome((0, 1))[2] == 1


def test_the_outcome_agrees_with_the_other_previews():
    game = _pair("Hanging Chad", "Lucky Cat")
    score, dollars, _ = game.preview_outcome((1, 0))
    assert (score, dollars) == game.preview_value((1, 0))
    assert score == game.preview_score((1, 0))
