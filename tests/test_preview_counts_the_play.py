"""A preview counts the hand as played before it scores, as the play does.

evaluate_play increments `G.GAME.hands[text].played` at state_events.lua:574,
before the jokers' context.before pass (card.lua:3411), and Obelisk decides
there whether the hand just played is now the most played (card.lua:3543).
`_play` has always counted first; `preview_play` did not, so it saw the count
one play late. With two hands tied, playing one of them makes it the most
played and Obelisk resets -- and the preview said it grew. On seed PLOQ83ZX
that was seven of twenty-six plays, two of them previewed as clearing a blind
they did not clear.
"""

import copy

from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.jokers import REGISTRY, JokerInstance

HAND = ((Rank.KING, Suit.SPADES), (Rank.KING, Suit.HEARTS),
        (Rank.NINE, Suit.CLUBS), (Rank.SEVEN, Suit.DIAMONDS),
        (Rank.FIVE, Suit.SPADES), (Rank.THREE, Suit.HEARTS),
        (Rank.TWO, Suit.CLUBS), (Rank.FOUR, Suit.DIAMONDS))
PAIR = (0, 1)


def _tied_with_obelisk():
    game = GameState(seed="PLOQ83ZX", deck="Blue Deck")
    obelisk = JokerInstance(REGISTRY["Obelisk"])
    obelisk.counter = 2.0
    game.gain_joker(obelisk)
    game._start_round()
    game.hand[:] = [Card(r, s) for r, s in HAND]
    pair = game.evaluate_selection([game.hand[i] for i in PAIR]).hand
    other = next(h for h in game.hand_levels.plays if h is not pair)
    game.hand_levels.plays[pair] = 2
    game.hand_levels.plays[other] = 2
    return game, pair


def _played(game, cards):
    fork = copy.deepcopy(game)
    before = fork.chips_scored
    fork.step(Action(ActionType.PLAY, cards=cards))
    return fork.chips_scored - before, fork


def test_the_preview_scores_what_the_play_scores_when_obelisk_resets():
    game, _ = _tied_with_obelisk()
    scored, fork = _played(game, PAIR)
    assert fork.jokers[0].counter == 1.0, "the play should reset Obelisk"
    assert game.preview_score(PAIR) == scored


def test_the_preview_puts_the_count_back():
    game, pair = _tied_with_obelisk()
    game.preview_score(PAIR)
    assert game.hand_levels.plays[pair] == 2
    assert game.jokers[0].counter == 2.0
