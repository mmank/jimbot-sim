"""`preview_play` is `preview_score` handing back the jokers it scored with.

A scaling joker grows while a hand scores, and `preview_score` restores the
row afterwards so a policy can preview two hundred plays a decision without
moving the run. That is right for the score and hides what the play would
make of the jokers, which is exactly what farming a Square Joker is about.
`preview_play` returns the copied row as scoring left it; the run's own row
is still restored.
"""

from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY, JokerInstance


def _game():
    game = GameState(seed="QWERTYUI", deck="Blue Deck")
    game.gain_joker(JokerInstance(REGISTRY["Square Joker"]))
    game._start_round()
    return game


def test_a_four_card_play_grows_the_returned_row():
    game = _game()
    score, row = game.preview_play((0, 1, 2, 3))
    assert row[0].counter == 4
    assert game.jokers[0].counter == 0, "the run's row must be untouched"


def test_a_five_card_play_does_not():
    game = _game()
    _, row = game.preview_play((0, 1, 2, 3, 4))
    assert row[0].counter == 0


def test_the_score_is_preview_score():
    game = _game()
    assert game.preview_play((0, 1, 2, 3))[0] == game.preview_score((0, 1, 2, 3))
