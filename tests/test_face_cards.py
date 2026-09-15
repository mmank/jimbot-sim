"""Which cards are face cards: Card:is_face, as the game asks it.

card.lua:964-970:

    function Card:is_face(from_boss)
        if self.debuff and not from_boss then return end
        local id = self:get_id()
        if id == 11 or id == 12 or id == 13 or next(find_joker("Pareidolia")) then
            return true
        end
    end

get_id answers a Stone card with `-math.random(100, 1000000)`
(card.lua:957-962), so a Stone King is no face card. The Pareidolia test never
looks at the id, so with Pareidolia held every card is one, Stone included --
and find_joker leaves out a debuffed Pareidolia (misc_functions.lua:903-907).
A debuffed card is no face card, unless it is a boss asking.

Who asks, and how:

  * The Plant: Blind:debuff_card, `card:is_face(true)` (blind.lua:630).
  * Scary Face and Smiley Face (card.lua:3136-3149), Business Card
    (3175-3177), Photograph (3093-3098), Reserved Parking (3302-3304), Sock and
    Buskin (3344-3345), Midas Mask (3443-3448), Ride the Bus (3525-3529), Canio
    (2626, 2676) and Faceless Joker (2861): plain `is_face()`.

The simulator said a Stone card was never a face card, Pareidolia or not; let
Midas Mask and Canio count a debuffed King; and had The Plant read the printed
rank, debuffing a Stone King.
"""

import pytest

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance, make
from jimbot_sim.scoring import score_hand

PLANT = next(b for b in BOSSES if b.name == "The Plant")


def _stone(rank=Rank.KING, suit=Suit.SPADES):
    card = Card(rank, suit)
    card.enhancement = Enhancement.STONE
    return card


def _game(jokers, cards, boss=None):
    game = GameState(seed=0)
    game.jokers = [make(name) for name in jokers]
    if boss is not None:
        game.blind = make_blind(BlindKind.BOSS, 1, boss)
    game.hand = list(cards)
    game.full_deck = list(cards)
    game._apply_debuffs()
    return game


def _score(game, played, held=()):
    result = game.evaluate_selection(list(played))
    return score_hand(game, result, list(played), list(held))


def _joker(game, name):
    return next(j for j in game.jokers if j.name == name)


# -- scoring: a Stone card is a face card beside Pareidolia ------------------

def test_scary_face_pays_a_stone_card_only_beside_pareidolia():
    """card.lua:3136-3137 `context.other_card:is_face()`. A Stone King and a
    King of Hearts, High Card: 5 + 50 + 10 chips and 30 for each face."""
    played = [_stone(), Card(Rank.KING, Suit.HEARTS)]
    alone = _game(("Scary Face",), played)
    assert _score(alone, played).score == 95
    played = [_stone(), Card(Rank.KING, Suit.HEARTS)]
    beside = _game(("Pareidolia", "Scary Face"), played)
    assert _score(beside, played).score == 125


def test_sock_and_buskin_retriggers_a_stone_card_beside_pareidolia():
    """card.lua:3344-3345: the Stone card's 50 chips come twice."""
    played = [_stone(), Card(Rank.SEVEN, Suit.HEARTS)]
    game = _game(("Pareidolia", "Sock and Buskin"), played)
    assert _score(game, played).score == 5 + 2 * 50 + 2 * 7


def test_photograph_takes_a_leading_stone_card_beside_pareidolia():
    """card.lua:3093-3098: the first scoring card that is_face() -- the Stone
    card, so the X2 lands before the Mult card's +4."""
    mult_king = Card(Rank.KING, Suit.HEARTS)
    mult_king.enhancement = Enhancement.MULT
    played = [_stone(), mult_king]
    game = _game(("Pareidolia", "Photograph"), played)
    assert _score(game, played).score == (5 + 50 + 10) * (1 * 2 + 4)


def test_ride_the_bus_resets_on_a_lone_stone_card_beside_pareidolia():
    """card.lua:3525-3532: any scoring card that is_face() resets it."""
    played = [_stone(Rank.TWO)]
    game = _game(("Pareidolia", "Ride the Bus"), played)
    _joker(game, "Ride the Bus").counter = 3.0
    ctx = _score(game, played)
    assert _joker(game, "Ride the Bus").counter == 0
    assert ctx.score == 5 + 50


def test_midas_mask_gilds_a_stone_card_beside_pareidolia():
    """card.lua:3443-3448: set_ability(m_gold) on every scoring is_face()."""
    played = [_stone(Rank.TWO), Card(Rank.SEVEN, Suit.HEARTS)]
    game = _game(("Pareidolia", "Midas Mask"), played)
    _score(game, played)
    assert [c.enhancement for c in played] == [Enhancement.GOLD] * 2


def test_midas_mask_without_pareidolia_leaves_a_stone_king():
    played = [_stone(), Card(Rank.KING, Suit.HEARTS)]
    game = _game(("Midas Mask",), played)
    _score(game, played)
    assert [c.enhancement for c in played] == [Enhancement.STONE,
                                               Enhancement.GOLD]


def test_midas_mask_leaves_the_kings_the_plant_debuffed():
    """card.lua:965: `if self.debuff and not from_boss then return end`, and
    Midas Mask's is_face() is not from a boss."""
    played = [Card(Rank.KING, Suit.SPADES), Card(Rank.KING, Suit.HEARTS)]
    game = _game(("Midas Mask",), played, boss=PLANT)
    assert all(c.debuffed for c in played)
    _score(game, played)
    assert [c.enhancement for c in played] == [Enhancement.NONE] * 2


def _rolls(game):
    """Record each listed probability rolled, and let every one succeed."""
    keys = []
    game.rng.chance = lambda key, numerator, denominator: (
        keys.append(key) or True)
    return keys


def test_business_card_rolls_for_a_stone_card_beside_pareidolia():
    """card.lua:3175-3177: `is_face() and pseudorandom('business') < ...`,
    so the roll -- and its draw from the stream -- happens only for a face."""
    played = [_stone(Rank.TWO)]
    game = _game(("Pareidolia", "Business Card"), played)
    keys = _rolls(game)
    assert _score(game, played).money_gained == 2
    assert keys == ["business"]


def test_reserved_parking_rolls_for_a_held_stone_card_beside_pareidolia():
    """card.lua:3302-3304, the same shape for a card held in hand."""
    played = [Card(Rank.TWO, Suit.SPADES)]
    held = [_stone(Rank.SEVEN, Suit.HEARTS)]
    game = _game(("Pareidolia", "Reserved Parking"), played + held)
    keys = _rolls(game)
    assert _score(game, played, held).money_gained == 1
    assert keys == ["parking"]


# -- The Plant: is_face(true) --------------------------------------------------

def test_the_plant_does_not_debuff_a_stone_king():
    """blind.lua:630 `card:is_face(true)`; get_id for Stone, card.lua:958."""
    cards = [_stone(), Card(Rank.KING, Suit.HEARTS), Card(Rank.SEVEN,
                                                          Suit.CLUBS)]
    _game((), cards, boss=PLANT)
    assert [c.debuffed for c in cards] == [False, True, False]


def test_the_plant_beside_pareidolia_debuffs_every_card_stone_included():
    """card.lua:967: the Pareidolia test does not look at get_id."""
    cards = [_stone(), Card(Rank.KING, Suit.HEARTS), Card(Rank.SEVEN,
                                                          Suit.CLUBS)]
    _game(("Pareidolia",), cards, boss=PLANT)
    assert [c.debuffed for c in cards] == [True, True, True]


def test_the_plant_ignores_a_debuffed_pareidolia():
    """find_joker skips a debuffed joker (misc_functions.lua:907)."""
    cards = [_stone(), Card(Rank.KING, Suit.HEARTS), Card(Rank.SEVEN,
                                                          Suit.CLUBS)]
    game = GameState(seed=0)
    game.jokers = [make("Pareidolia")]
    game.set_joker_debuff(game.jokers[0], True)
    game.blind = make_blind(BlindKind.BOSS, 1, PLANT)
    game.full_deck = list(cards)
    game._apply_debuffs()
    assert [c.debuffed for c in cards] == [False, True, False]


# -- Canio: is_face() over the cards removed -----------------------------------

def _canio(*row):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in ("Canio",) + row:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    return game


@pytest.mark.parametrize("row,rank,stone,debuffed,gain", [
    ((), Rank.KING, False, False, 1.0),
    ((), Rank.KING, True, False, 0.0),               # card.lua:958-960
    ((), Rank.KING, False, True, 0.0),               # card.lua:965
    (("Pareidolia",), Rank.TWO, True, False, 1.0),   # card.lua:967
    (("Pareidolia",), Rank.TWO, False, True, 0.0),   # card.lua:965 first
])
def test_canio_counts_what_is_face_counts(row, rank, stone, debuffed, gain):
    """card.lua:2673-2679 `if val:is_face() then face_cards = face_cards + 1`."""
    game = _canio(*row)
    card = game.hand[0]
    card.rank = rank
    card.debuffed = debuffed
    if stone:
        card.enhancement = Enhancement.STONE
    joker = _joker(game, "Canio")
    before = joker.counter
    game.remove_card(card)
    assert joker.counter - before == pytest.approx(gain)
