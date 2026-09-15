"""A play names its cards in the order they score.

The game scores a play left to right on screen: play_cards_from_highlighted
sorts the selected cards by position (state_events.lua:463), and a player
drags cards to choose that order. Hanging Chad retriggers the first card
scored and Photograph pays on the first face card scored, so with both held
an Ace-high flush that leads with the Ace spends the retriggers on the Ace --
unless the Queen is dragged in front of it. BotAPI.swap_card_left is that
drag; here a play in any order of a legal selection is legal, and playing it
first moves the named cards to the front in that order.
"""

import copy

from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.jokers import REGISTRY, JokerInstance

FLUSH = (0, 1, 2, 3, 4)
QUEEN_FIRST = (1, 0, 2, 3, 4)


def _game(*jokers):
    game = GameState(seed="AWEFRTUZ", deck="Blue Deck")
    for name in jokers:
        game.gain_joker(JokerInstance(REGISTRY[name]))
    game._start_round()
    game.hand[:] = [Card(Rank.ACE, Suit.HEARTS), Card(Rank.QUEEN, Suit.HEARTS),
                    Card(Rank.NINE, Suit.HEARTS), Card(Rank.SIX, Suit.HEARTS),
                    Card(Rank.TWO, Suit.HEARTS), Card(Rank.FIVE, Suit.CLUBS),
                    Card(Rank.FOUR, Suit.CLUBS), Card(Rank.THREE, Suit.CLUBS)]
    return game


def test_any_order_of_a_legal_selection_is_legal():
    game = _game()
    assert game.is_legal(Action(ActionType.PLAY, cards=QUEEN_FIRST))
    assert not game.is_legal(Action(ActionType.PLAY, cards=(1, 1, 2)))
    assert not game.is_legal(Action(ActionType.PLAY, cards=(1, 0, 9)))


def test_the_named_cards_go_to_the_front_in_that_order():
    game = _game()
    ace, queen = game.hand[0], game.hand[1]
    flush, rest = game.hand[2:5], game.hand[5:]
    assert game._arrange_play(QUEEN_FIRST) == FLUSH
    assert game.hand[:2] == [queen, ace]
    assert game.hand[2:5] == flush and game.hand[5:] == rest


def test_a_play_in_hand_order_is_left_alone():
    game = _game()
    before = list(game.hand)
    assert game._arrange_play((0, 2, 4)) == (0, 2, 4)
    assert game.hand == before


def test_left_swaps_reach_the_same_arrangement():
    direct, swapped = _game(), _game()
    direct._arrange_play((3, 1, 0))
    for at in (3, 2, 1, 2):     # the Six to the front, then the Queen, the Ace
        swapped.swap_card_left(at)
    assert [repr(c) for c in swapped.hand] == [repr(c) for c in direct.hand]


def test_a_preview_counts_the_money_a_play_earns_while_it_scores():
    """A Gold Seal is $3 a trigger (card.lua, Card:get_p_dollars), and
    Hanging Chad retriggers the first card scored twice: $9 at the front,
    $3 behind, the same chips either way."""
    from jimbot_sim.cards import Seal

    game = GameState(seed="AWEFRTUZ", deck="Blue Deck")
    game.gain_joker(JokerInstance(REGISTRY["Hanging Chad"]))
    game._start_round()
    gold = Card(Rank.SEVEN, Suit.SPADES)
    gold.seal = Seal.GOLD
    game.hand[:] = [gold, Card(Rank.SEVEN, Suit.HEARTS),
                    Card(Rank.TWO, Suit.CLUBS), Card(Rank.THREE, Suit.CLUBS)]
    front, behind = game.preview_value((0, 1)), game.preview_value((1, 0))
    assert front[1] == 9 and behind[1] == 3
    assert front[0] == game.preview_score((0, 1))
    assert game.money == GameState(seed="AWEFRTUZ", deck="Blue Deck").money


def test_the_queen_first_scores_the_photograph_three_times():
    game = _game("Hanging Chad", "Photograph")
    assert game.preview_score(QUEEN_FIRST) > game.preview_score(FLUSH)
    played = copy.deepcopy(game)
    before = played.chips_scored
    played.step(Action(ActionType.PLAY, cards=QUEEN_FIRST))
    assert played.chips_scored - before == game.preview_score(QUEEN_FIRST)
