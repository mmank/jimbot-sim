import pytest

from balatro.cards import Card, Enhancement, Rank, Suit
from balatro.hands import HandLevels, HandType, evaluate

S, H, D, C = Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS


def hand(*specs):
    return [Card(r, s) for r, s in specs]


@pytest.mark.parametrize("cards, expected", [
    ([(Rank.TWO, S), (Rank.THREE, S), (Rank.FOUR, S), (Rank.FIVE, S), (Rank.SIX, S)],
     HandType.STRAIGHT_FLUSH),
    ([(Rank.ACE, S), (Rank.TWO, H), (Rank.THREE, S), (Rank.FOUR, S), (Rank.FIVE, S)],
     HandType.STRAIGHT),
    ([(Rank.TEN, S), (Rank.JACK, H), (Rank.QUEEN, S), (Rank.KING, S), (Rank.ACE, S)],
     HandType.STRAIGHT),
    ([(Rank.KING, S), (Rank.KING, H), (Rank.KING, D), (Rank.TWO, S), (Rank.TWO, H)],
     HandType.FULL_HOUSE),
    ([(Rank.KING, S), (Rank.KING, H), (Rank.KING, D), (Rank.KING, C), (Rank.TWO, H)],
     HandType.FOUR_OF_A_KIND),
    ([(Rank.NINE, S), (Rank.NINE, H), (Rank.THREE, S), (Rank.THREE, H), (Rank.TWO, S)],
     HandType.TWO_PAIR),
    ([(Rank.TWO, H), (Rank.FIVE, H), (Rank.NINE, H), (Rank.JACK, H), (Rank.KING, H)],
     HandType.FLUSH),
    ([(Rank.KING, S), (Rank.QUEEN, H)], HandType.HIGH_CARD),
])
def test_hand_detection(cards, expected):
    assert evaluate(hand(*cards)).hand is expected


def test_five_of_a_kind_needs_a_duplicate_deck():
    cards = [Card(Rank.KING, S) for _ in range(5)]
    assert evaluate(cards).hand is HandType.FLUSH_FIVE


def test_wild_card_completes_a_flush():
    cards = hand((Rank.TWO, H), (Rank.FIVE, H), (Rank.NINE, H), (Rank.JACK, H))
    cards.append(Card(Rank.KING, S, enhancement=Enhancement.WILD))
    assert evaluate(cards).hand is HandType.FLUSH


def test_four_fingers_allows_four_card_flush():
    cards = hand((Rank.TWO, H), (Rank.FIVE, H), (Rank.NINE, H), (Rank.JACK, H),
                 (Rank.KING, S))
    assert evaluate(cards).hand is HandType.HIGH_CARD
    assert evaluate(cards, four_fingers=True).hand is HandType.FLUSH


def test_shortcut_allows_gapped_straight():
    cards = hand((Rank.TWO, S), (Rank.FOUR, H), (Rank.SIX, S), (Rank.EIGHT, D),
                 (Rank.TEN, C))
    assert evaluate(cards).hand is HandType.HIGH_CARD
    assert evaluate(cards, shortcut=True).hand is HandType.STRAIGHT


def test_only_the_pair_scores():
    cards = hand((Rank.NINE, S), (Rank.NINE, H), (Rank.THREE, S), (Rank.FOUR, H),
                 (Rank.TWO, S))
    result = evaluate(cards)
    assert result.hand is HandType.PAIR
    assert [c.rank for c in result.scoring] == [Rank.NINE, Rank.NINE]


def test_stone_cards_always_score():
    cards = hand((Rank.NINE, S), (Rank.NINE, H), (Rank.THREE, S))
    cards.append(Card(Rank.TWO, S, enhancement=Enhancement.STONE))
    result = evaluate(cards)
    assert result.hand is HandType.PAIR
    assert len(result.scoring) == 3


def test_hand_levels_scale():
    levels = HandLevels.new()
    assert levels.values(HandType.PAIR) == (10, 2)
    levels.level_up(HandType.PAIR)
    assert levels.values(HandType.PAIR) == (25, 3)
    levels.level_up(HandType.PAIR, 2)
    assert levels.values(HandType.PAIR) == (55, 5)
