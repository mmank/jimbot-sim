"""The money a play earns in expectation, exact rather than rolled."""

from jimbot_sim.cards import Card, Enhancement, Rank, Seal, Suit
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance


def _hand(*jokers, card):
    game = GameState(seed="MONEY001", deck="Red Deck", stake=1)
    for name in jokers:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    game.hand[:] = [card, Card(Rank.TWO, Suit.CLUBS)]
    return game


def test_a_coin_counts_at_its_odds():
    king = Card(Rank.KING, Suit.HEARTS)
    assert _hand("Business Card", card=king).preview_money((0,))[1] == 1.0
    # Blueprint copying the Card flips a second coin.
    both = _hand("Blueprint", "Business Card", card=king)
    assert both.preview_money((0,))[1] == 2.0
    # And Hanging Chad scores the King three times.
    chad = _hand("Hanging Chad", "Blueprint", "Business Card", card=king)
    assert chad.preview_money((0,))[1] == 6.0


def test_certain_money_counts_in_full_and_a_lucky_card_at_one_in_fifteen():
    seal = Card(Rank.SIX, Suit.CLUBS)
    seal.seal = Seal.GOLD
    assert _hand(card=seal).preview_money((0,))[1] == 3.0
    lucky = Card(Rank.SIX, Suit.CLUBS, enhancement=Enhancement.LUCKY)
    assert abs(_hand(card=lucky).preview_money((0,))[1] - 20.0 / 15.0) < 1e-9


def test_the_rolled_preview_is_untouched():
    king = Card(Rank.KING, Suit.HEARTS)
    game = _hand("Business Card", card=king)
    assert game.preview_value((0,))[1] in (0, 2)
    game.preview_money((0,))
    assert game.money == GameState(seed="MONEY001", deck="Red Deck",
                                   stake=1).money
