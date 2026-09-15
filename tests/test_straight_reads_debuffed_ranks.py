"""A debuffed card still counts towards a straight.

get_straight (misc_functions.lua:548-590) asks each card nothing but its id,

    local id = hand[i]:get_id()
    if id > 1 and id < 15 then ...

and Card:get_id (card.lua:957-962) never looks at the debuff. Only a Stone
card is left out, by the random negative id it returns. The flush half
already reads a debuffed card's printed suit (card.lua:4065-4075, see
test_flush_reads_debuffed_suits), so a debuffed run of one suit is a
Straight Flush.

The simulator's straight dropped debuffed cards. Seed N1OA90W1, Abandoned
Deck, stake 1, on the headless engine at decision 105: The Club debuffs
Clubs, and 6-5-4-3-2 of Clubs was a Straight Flush in the game --
(100 + 100 Devious + 50 foil) x (8 x 4.5 Madness x 3 Stencil) = 27000,
enough for the 22000 boss -- and a level-two Flush here, 100 x 81 = 8100.
"""

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Card, Edition, Enhancement, Rank, Suit
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.hands import HandType, evaluate
from jimbot_sim.jokers import REGISTRY, JokerInstance

CLUB = next(b for b in BOSSES if b.name == "The Club")


def _cards(*specs, debuffed=True):
    out = []
    for rank, suit in specs:
        card = Card(rank, suit)
        card.debuffed = debuffed
        out.append(card)
    return out


RUN_OF_CLUBS = ((Rank.SIX, Suit.CLUBS), (Rank.FIVE, Suit.CLUBS),
                (Rank.FOUR, Suit.CLUBS), (Rank.THREE, Suit.CLUBS),
                (Rank.TWO, Suit.CLUBS))


def test_five_debuffed_clubs_in_a_row_are_a_straight_flush():
    result = evaluate(_cards(*RUN_OF_CLUBS))
    assert result.hand is HandType.STRAIGHT_FLUSH
    assert len(result.scoring) == 5
    assert HandType.STRAIGHT in result.contains


def test_one_debuffed_card_still_completes_a_straight():
    cards = _cards((Rank.NINE, Suit.HEARTS), (Rank.EIGHT, Suit.CLUBS),
                   (Rank.SEVEN, Suit.SPADES), (Rank.SIX, Suit.DIAMONDS),
                   (Rank.FIVE, Suit.HEARTS), debuffed=False)
    cards[1].debuffed = True
    result = evaluate(cards)
    assert result.hand is HandType.STRAIGHT
    assert len(result.scoring) == 5


def test_a_stone_card_still_breaks_a_straight():
    cards = _cards(*RUN_OF_CLUBS, debuffed=False)
    cards[2].enhancement = Enhancement.STONE
    assert HandType.STRAIGHT not in evaluate(cards).contains


def test_the_n1oa90w1_play_scores_27000():
    game = GameState(seed="N1OA90W1", deck="Red Deck")
    madness = JokerInstance(REGISTRY["Madness"])
    madness.counter = 4.5
    devious = JokerInstance(REGISTRY["Devious Joker"])
    devious.edition = Edition.FOIL
    for joker in (madness, devious, JokerInstance(REGISTRY["Joker Stencil"])):
        game.gain_joker(joker)
    game.ante_boss = ""
    game.blind = make_blind(BlindKind.BOSS, game.ante, CLUB)
    game._start_round()
    game.hand_levels.levels[HandType.FLUSH] = 2
    game.hand_levels.levels[HandType.THREE_OF_A_KIND] = 3

    hand = [Card(Rank.TEN, Suit.DIAMONDS), Card(Rank.EIGHT, Suit.CLUBS),
            Card(Rank.SIX, Suit.CLUBS), Card(Rank.FIVE, Suit.CLUBS),
            Card(Rank.FOUR, Suit.HEARTS), Card(Rank.FOUR, Suit.CLUBS),
            Card(Rank.THREE, Suit.CLUBS), Card(Rank.TWO, Suit.CLUBS)]
    game.full_deck.extend(hand)
    game.hand[:] = hand
    game._apply_debuffs()
    play = (2, 3, 5, 6, 7)
    assert all(game.hand[i].debuffed for i in play)

    assert game.evaluate_selection(
        [game.hand[i] for i in play]).hand is HandType.STRAIGHT_FLUSH
    assert game.preview_score(play) == 27000
    game.step(Action(ActionType.PLAY, cards=play))
    assert game.chips_scored == 27000
