"""`preview_play` of a hand the boss refuses hands back the row `after` left.

evaluate_play skips the scoring block for a refused hand
(state_events.lua:614, 997-999) but asks every joker `context.after` for
every hand played, outside that `if` (state_events.lua:1068-1075). Ice Cream
(card.lua:3571) and Seltzer (card.lua:3601) answer it, so a hand The Mouth
zeroes still melts one and counts the other down -- which GameState._play
does. preview_play said 0 for such a hand and returned the copies untouched,
so a policy pricing the row a play leaves saw a refused hand as free.

The row that comes back must be the row a real step leaves, for a refused
hand and a scored one alike, and the run's own row must not move.
"""

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

MOUTH = next(b for b in BOSSES if b.name == "The Mouth")

PAIR = ((Rank.NINE, Suit.HEARTS), (Rank.NINE, Suit.DIAMONDS),
        (Rank.FIVE, Suit.HEARTS), (Rank.FOUR, Suit.CLUBS),
        (Rank.TWO, Suit.CLUBS))
TRIPS = ((Rank.SEVEN, Suit.HEARTS), (Rank.SEVEN, Suit.CLUBS),
         (Rank.SEVEN, Suit.DIAMONDS), (Rank.FIVE, Suit.DIAMONDS),
         (Rank.FOUR, Suit.SPADES))

ROW = ("Ice Cream", "Seltzer", "Green Joker", "Ride the Bus")


def _mouth(*names):
    game = GameState(seed="VJPW2C6Z", deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game.ante_boss = ""
    game.blind = make_blind(BlindKind.BOSS, game.ante, MOUTH)
    game._start_round()
    game.blind.target = 10 ** 12       # never cleared, so the round goes on
    game.hands_left = 10
    return game


def _deal(game, specs):
    game.hand[:] = [Card(rank, suit) for rank, suit in specs]
    return tuple(range(len(specs)))


def _row(jokers):
    return [(j.name, j.counter) for j in jokers]


def _preview_then_play(game, specs):
    """(preview score, preview row, real gain, real row), checking on the way
    that the preview left the run's own row alone."""
    picks = _deal(game, specs)
    real = list(game.jokers)
    before = _row(game.jokers)
    score, row = game.preview_play(picks)
    assert _row(game.jokers) == before
    assert all(a is b for a, b in zip(game.jokers, real))
    assert len(game.jokers) == len(real)
    assert not any(r is j for r in row for j in real)
    gained = game.chips_scored
    game.step(Action(ActionType.PLAY, cards=picks))
    return score, _row(row), game.chips_scored - gained, _row(game.jokers)


def test_a_refused_hand_previews_the_after_pass():
    """state_events.lua:1068-1075 under The Mouth: Ice Cream 95 -> 90,
    Seltzer 9 -> 8; Green Joker and Ride the Bus are `before` and stay."""
    game = _mouth(*ROW)
    game.step(Action(ActionType.PLAY, cards=_deal(game, PAIR)))
    score, row, gained, real = _preview_then_play(game, TRIPS)
    assert score == gained == 0
    assert row == real
    assert real == [("Ice Cream", 90), ("Seltzer", 8),
                    ("Green Joker", 1), ("Ride the Bus", 1)]


def test_a_refused_hand_previews_the_jokers_it_eats():
    """Ice Cream on 5 and Seltzer on 1 are both gone after the refused hand
    (card.lua:3572, 3602), in the preview's row and the real one alike."""
    game = _mouth("Ice Cream", "Seltzer", "Joker")
    game.step(Action(ActionType.PLAY, cards=_deal(game, PAIR)))
    game.jokers[0].counter, game.jokers[1].counter = 5, 1
    score, row, gained, real = _preview_then_play(game, TRIPS)
    assert score == gained == 0
    assert row == real == [("Joker", 0.0)]


def test_a_scored_hand_previews_the_same_row_as_the_step():
    game = _mouth(*ROW)
    score, row, gained, real = _preview_then_play(game, PAIR)
    assert score == gained > 0
    assert row == real
    assert real == [("Ice Cream", 95), ("Seltzer", 9),
                    ("Green Joker", 1), ("Ride the Bus", 1)]


def test_a_blueprint_does_not_melt_the_preview_twice():
    """Both `after` branches are `not context.blueprint`."""
    game = _mouth("Blueprint", "Ice Cream")
    game.step(Action(ActionType.PLAY, cards=_deal(game, PAIR)))
    score, row, gained, real = _preview_then_play(game, TRIPS)
    assert row == real
    assert real[1] == ("Ice Cream", 90)
