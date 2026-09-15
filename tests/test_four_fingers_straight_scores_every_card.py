"""A Four Fingers straight scores every card of every rank in the run.

get_straight (functions/misc_functions.lua:548) buckets the hand by rank and,
for each rank the run passes through, adds *all* of that rank's cards:

    if IDS[j == 1 and 14 or j] then
        straight_length = straight_length + 1
        for k, v in ipairs(IDS[j == 1 and 14 or j]) do
            t[#t+1] = v
        end

With Four Fingers a five-card straight can hold a pair -- 9 8 7 7 6 -- and
both sevens score. This kept one card per rank, so the second seven played
and scored nothing.

Two seeds on the headless engine stopped on it:

  64PUKM3K, Erratic Deck, stake 3, decision 78: 9H 8D 7S 7C 6H under Even
  Steven, Blue Joker, Four Fingers, holo Loyalty Card and The Duo. The game
  scored 195 x 44 = 8580, this 188 x 44 = 8272 -- the 7C's seven chips.

  U1AYP8BC, Yellow Deck, stake 5, decision 53: 7D 6S 6C 5D 4H, level-2
  Straight. The game scored 226 x 14 = 3164, this 220 x 14 = 3080 -- the
  6C's six chips.

get_straight reads get_id, which does not look at debuff (card.lua:957),
so a debuffed card holds its place in a straight the way it holds its suit in
a flush (see test_flush_reads_debuffed_suits); it simply scores nothing when
evaluate_play reaches it (state_events.lua:655).

And a Straight Flush is any hand with both parts (misc_functions.lua:428):

    if next(parts._flush) and next(parts._straight) then

with no test that the flush and the straight are the same cards. With Four
Fingers they need not be: 2S 3S 4S 5H 9S is a four-card straight and a
four-card flush, and the game calls it a Straight Flush and scores all five.
"""

from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.hands import HandType, evaluate
from jimbot_sim.scoring import score_hand

S, H, D, C = Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS


def _hand(*specs):
    return [Card(rank, suit) for rank, suit in specs]


def test_both_sevens_score_in_a_four_fingers_straight():
    """64PUKM3K decision 78: 9 8 7 7 6."""
    cards = _hand((Rank.NINE, H), (Rank.EIGHT, D), (Rank.SEVEN, S),
                  (Rank.SEVEN, C), (Rank.SIX, H))
    result = evaluate(cards, four_fingers=True)
    assert result.hand is HandType.STRAIGHT
    assert [c.uid for c in result.scoring] == [c.uid for c in cards]


def test_both_sixes_score_in_a_four_fingers_straight():
    """U1AYP8BC decision 53: 7 6 6 5 4, which scores 3164 in the game."""
    cards = _hand((Rank.SEVEN, D), (Rank.SIX, S), (Rank.SIX, C),
                  (Rank.FIVE, D), (Rank.FOUR, H))
    result = evaluate(cards, four_fingers=True)
    assert result.hand is HandType.STRAIGHT
    assert len(result.scoring) == 5


def test_the_duplicate_adds_its_chips_to_the_score():
    """Level-1 Straight, no jokers: 30 + 9 + 8 + 7 + 7 + 6 = 67 chips x 4."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    cards = _hand((Rank.NINE, H), (Rank.EIGHT, D), (Rank.SEVEN, S),
                  (Rank.SEVEN, C), (Rank.SIX, H))
    result = evaluate(cards, four_fingers=True)
    ctx = score_hand(game, result, cards, [])
    assert (ctx.chips, ctx.mult, ctx.score) == (67, 4, 268)


def test_shortcut_straight_keeps_its_duplicate_too():
    """2 4 4 6 8 with Shortcut and Four Fingers: the run is 2 4 6 8."""
    cards = _hand((Rank.TWO, S), (Rank.FOUR, H), (Rank.FOUR, C),
                  (Rank.SIX, D), (Rank.EIGHT, S))
    result = evaluate(cards, four_fingers=True, shortcut=True)
    assert result.hand is HandType.STRAIGHT
    assert len(result.scoring) == 5


def test_a_card_off_the_run_still_does_not_score():
    """9 8 7 6 and a 2: the 2 breaks nothing and joins nothing."""
    cards = _hand((Rank.NINE, H), (Rank.EIGHT, D), (Rank.SEVEN, S),
                  (Rank.SIX, H), (Rank.TWO, C))
    result = evaluate(cards, four_fingers=True)
    assert result.hand is HandType.STRAIGHT
    assert [c.rank for c in result.scoring] == [
        Rank.NINE, Rank.EIGHT, Rank.SEVEN, Rank.SIX]


def test_a_debuffed_card_holds_its_place_in_a_straight():
    """get_id ignores debuff: 5 6 7 8 9 with the 7 debuffed is a Straight."""
    cards = _hand((Rank.FIVE, S), (Rank.SIX, H), (Rank.SEVEN, C),
                  (Rank.EIGHT, D), (Rank.NINE, S))
    cards[2].debuffed = True
    result = evaluate(cards)
    assert result.hand is HandType.STRAIGHT
    assert len(result.scoring) == 5


def test_four_fingers_straight_flush_needs_no_overlap():
    """2S 3S 4S 5H 9S: a straight of four and a flush of four, not the same
    four cards, is a Straight Flush in the game and all five score."""
    cards = _hand((Rank.TWO, S), (Rank.THREE, S), (Rank.FOUR, S),
                  (Rank.FIVE, H), (Rank.NINE, S))
    result = evaluate(cards, four_fingers=True)
    assert result.hand is HandType.STRAIGHT_FLUSH
    assert len(result.scoring) == 5
