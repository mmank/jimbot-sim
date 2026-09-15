"""A debuffed Glass card never shatters, and never draws for it.

state_events.lua:961 --

    if scoring_hand[i].ability.name == 'Glass Card'
        and not scoring_hand[i].debuff
        and pseudorandom('glass') < G.GAME.probabilities.normal/... then

-- stops at the debuff, before `pseudorandom`, so the 'glass' stream is left
exactly where it was. Unreachable until a debuffed card could score, which
it can in a flush. Seed QWERTYUI, Blue Deck, stake 1 on the headless engine:
a Glass Ace of Spades debuffed by The Club under Smeared Joker broke here
and did not break in the game, and the decks parted at 51 against 52.
"""

from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.scoring import shattered_glass


def _glass(debuffed):
    card = Card(Rank.ACE, Suit.SPADES)
    card.enhancement = Enhancement.GLASS
    card.debuffed = debuffed
    return card


def test_a_debuffed_glass_card_does_not_shatter():
    game = GameState(seed="QWERTYUI", deck="Blue Deck")
    for _ in range(40):                     # any draw at all would break one
        assert shattered_glass(game, (_glass(debuffed=True),)) == []


def test_it_does_not_move_the_glass_stream():
    game = GameState(seed="QWERTYUI", deck="Blue Deck")
    before = dict(game.rng.state())
    shattered_glass(game, (_glass(debuffed=True),))
    assert dict(game.rng.state()) == before


def test_a_live_glass_card_still_rolls():
    game = GameState(seed="QWERTYUI", deck="Blue Deck")
    before = dict(game.rng.state())
    broke = 0
    for _ in range(200):
        broke += len(shattered_glass(game, (_glass(debuffed=False),)))
    assert dict(game.rng.state()) != before
    assert 0 < broke < 200, "one in four, not never and not always"
