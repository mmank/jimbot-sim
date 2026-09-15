"""The Mouth zeroes every hand but the round's first type, however often played.

blind.lua:542-548, inside Blind:debuff_hand --

    if self.name == "The Mouth" then
        if self.only_hand and self.only_hand ~= handname then
            self.triggered = true
            return true
        end
        if not check then self.only_hand = handname end
    end

`only_hand` is the first hand's name and nothing else: a debuffed hand
returns before the assignment, so a zeroed type never becomes allowed. It is
cleared by set_blind (blind.lua:173-175) and is not written by the `check`
call cardarea.lua:168 makes while cards are only highlighted.

The simulator asked "was this type played this round" instead, and a zeroed
hand still counts as played. Seed Q4BAHUP3, Nebula Deck, stake 8, on the
headless engine at decision 43: a Pair scored 1634, a Straight and a Three of
a Kind were zeroed, and the second Three of a Kind -- by then "played this
round" -- scored 5544 here and beat a 6400 blind the game left standing.
"""

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import Action, ActionType, GameState

MOUTH = next(b for b in BOSSES if b.name == "The Mouth")

PAIR = ((Rank.NINE, Suit.HEARTS), (Rank.NINE, Suit.DIAMONDS),
        (Rank.FIVE, Suit.HEARTS), (Rank.FOUR, Suit.CLUBS),
        (Rank.TWO, Suit.CLUBS))
STRAIGHT = ((Rank.ACE, Suit.SPADES), (Rank.KING, Suit.CLUBS),
            (Rank.QUEEN, Suit.HEARTS), (Rank.JACK, Suit.CLUBS),
            (Rank.TEN, Suit.DIAMONDS))
TRIPS = ((Rank.SEVEN, Suit.HEARTS), (Rank.SEVEN, Suit.CLUBS),
         (Rank.SEVEN, Suit.DIAMONDS), (Rank.FIVE, Suit.DIAMONDS),
         (Rank.FOUR, Suit.SPADES))


def _mouth():
    game = GameState(seed="Q4BAHUP3", deck="Red Deck")
    game.ante_boss = ""
    game.blind = make_blind(BlindKind.BOSS, game.ante, MOUTH)
    game._start_round()
    game.blind.target = 10 ** 12       # never cleared, so the round goes on
    game.hands_left = 10
    return game


def _deal(game, specs):
    game.hand[:] = [Card(rank, suit) for rank, suit in specs]
    return tuple(range(len(specs)))


def _play(game, specs):
    """Chips the play adds to the round."""
    before = game.chips_scored
    game.step(Action(ActionType.PLAY, cards=_deal(game, specs)))
    return game.chips_scored - before


def test_the_q4bahup3_round():
    """Pair, Straight, Three of a Kind, Three of a Kind: only the Pair scores."""
    game = _mouth()
    assert _play(game, PAIR) > 0
    assert _play(game, STRAIGHT) == 0
    assert _play(game, TRIPS) == 0
    assert _play(game, TRIPS) == 0, (
        "a zeroed Three of a Kind does not become the round's hand")
    assert _play(game, PAIR) > 0


def test_preview_sees_the_zero():
    """The policy values plays with preview_score, so the zero must show there."""
    game = _mouth()
    _play(game, PAIR)
    _play(game, TRIPS)
    assert game.preview_score(_deal(game, TRIPS)) == 0
    assert game.preview_score(_deal(game, PAIR)) > 0


def test_previewing_does_not_pick_the_hand():
    """`check` mode: highlighting a hand never sets only_hand."""
    game = _mouth()
    assert game.preview_score(_deal(game, TRIPS)) > 0
    assert _play(game, PAIR) > 0
    assert _play(game, TRIPS) == 0


def test_a_new_blind_clears_the_lock():
    """set_blind: `if self.name == 'The Mouth' and not reset then only_hand = false`."""
    game = _mouth()
    _play(game, PAIR)
    game.blind = make_blind(BlindKind.BOSS, game.ante, MOUTH)
    game._start_round()
    game.blind.target = 10 ** 12
    game.hands_left = 10
    assert _play(game, TRIPS) > 0
    assert _play(game, PAIR) == 0


def test_a_disabled_mouth_zeroes_nothing():
    """`if self.disabled then return end` comes before the lock."""
    game = _mouth()
    game.blind.disabled = True
    assert _play(game, PAIR) > 0
    assert _play(game, TRIPS) > 0
    assert _play(game, PAIR) > 0
