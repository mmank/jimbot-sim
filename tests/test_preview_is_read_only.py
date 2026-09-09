"""preview_score is a question, not a move.

It runs the whole scoring pipeline, and the pipeline writes: Space Joker
levels the played hand one time in four, 8 Ball makes a Tarot for a scored
eight, Hiker adds five chips to every scored card for good, Vampire and
Midas Touch change enhancements. The preview copied the jokers and put back
the played cards' enhancements and left the rest where scoring had moved it.

A bot that previews every subset of its hand asks two hundred times a
decision. The first one to do so took High Card to level 140 through Space
Joker alone and won a run at ante eleven on hands the game never dealt --
which is how this was found, and why the whole of what scoring can touch is
snapshotted now.
"""

import itertools

from jimbot_sim.cards import Rank
from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance


def _run(*joker_names, seed="PREVIEW1"):
    game = GameState(seed=seed, deck="Red Deck")
    for name in joker_names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game.step(Action(ActionType.SELECT_BLIND))
    assert game.phase is Phase.PLAYING
    return game


def _fingerprint(game):
    return {
        "cards": [(c.uid, c.rank, c.suit, c.enhancement, c.edition, c.seal,
                   c.extra_chips) for c in game.full_deck],
        "hand": [c.uid for c in game.hand],
        "levels": dict(game.hand_levels.levels),
        "plays": dict(game.hand_levels.plays),
        "consumables": [c.name for c in game.consumables],
        "jokers": [(j.name, j.counter, j.secondary) for j in game.jokers],
        "money": game.money,
        "pools": dict(game.rng.pools),
    }


def _every_subset(game):
    for size in range(1, 6):
        for combo in itertools.combinations(range(len(game.hand)), size):
            game.preview_score(combo)


def test_space_joker_does_not_level_through_a_preview():
    """The preview's throwaway stream is named off the seed, so whether the
    one-in-four fires is a property of the seed. On SPACEHU8 it does, in the
    first hand's subsets, and the unfixed preview levelled a hand for good;
    on most seeds it does not and the test would pass for nothing."""
    game = _run("Space Joker", seed="SPACEHU8")
    before = _fingerprint(game)
    for _ in range(3):
        _every_subset(game)
    assert _fingerprint(game) == before


def test_eight_ball_makes_no_tarot_through_a_preview():
    game = _run("8 Ball")
    for card in game.hand[:3]:
        card.rank = Rank.EIGHT
    before = _fingerprint(game)
    _every_subset(game)
    assert _fingerprint(game) == before
    assert not game.consumables


def test_hiker_adds_no_chips_through_a_preview():
    game = _run("Hiker")
    before = _fingerprint(game)
    _every_subset(game)
    assert _fingerprint(game) == before
    assert all(c.extra_chips == 0 for c in game.hand)


def test_a_preview_is_what_the_play_then_scores():
    """Without randomness in the way, the answer is the move, to the chip."""
    game = _run("Joker", "Hiker")
    best = max((combo for size in range(1, 6)
                for combo in itertools.combinations(range(len(game.hand)),
                                                    size)),
               key=game.preview_score)
    predicted = game.preview_score(best)
    before = game.chips_scored
    game.step(Action(ActionType.PLAY, cards=best))
    assert game.chips_scored - before == predicted
