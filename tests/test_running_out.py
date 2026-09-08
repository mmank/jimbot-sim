"""The two ways a run ends without the blind ever being scored.

Both were reported by Marcin from play and then measured on the engine, and
they turn out to be different mechanisms that only look alike:

  no hand size    GAME_OVER set directly, in draw_from_deck_to_hand. No target
                  check, no Mr. Bones, no cash-out. Suspended while an Arcana
                  or Spectral pack is open, because that pack deals you a hand
                  and may be how you fix it.

  no cards        end_round(), called from update_selecting_hand the moment
                  hand, deck and play area are all empty. Not itself a loss --
                  it is the ordinary end of a round, and a run that had
                  already met the target cashes out normally.

The simulator could reach neither. hand_size floored at one rather than zero,
so the first was unreachable by construction, and the second was a hard
GAME_OVER that ignored the score. Mr. Bones was worse than either: the spec
carried a prevents_death flag that nothing ever read.
"""

import pytest

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance
from jimbot_sim.shop import PackKind, PackSpec


def _run(*names, **kwargs):
    """A run standing in a round, holding the named jokers.

    They are given before the round starts, which is how a run that already
    holds four Stuntmen meets its next blind: the hand is never dealt at all.
    """
    game = GameState(seed="TESTSEED", deck="Red Deck", **kwargs)
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    return game


def _then_shrink(game, *names):
    """Shrink the hand *after* it has been dealt, as buying a joker does."""
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    return game


def _pack(kind):
    return PackSpec(kind=kind, size="normal", options=3, picks=1, cost=4)


# ------------------------------------------------------------------
# running out of hand size
# ------------------------------------------------------------------

def test_hand_size_can_reach_zero():
    """CardArea:update floors it at zero. Flooring at one hid the whole rule."""
    game = _run("Stuntman", "Stuntman", "Stuntman", "Stuntman")
    assert game.hand_size == 0


def test_no_hand_size_and_no_cards_ends_the_run():
    game = _run("Stuntman", "Stuntman", "Stuntman", "Stuntman")
    game.hand[:] = []
    game._draw_to_hand_size()
    assert game.phase is Phase.GAME_OVER


def test_no_hand_size_but_cards_still_held_is_survivable():
    """Engine: limit 0 with eight cards in hand stays in SELECTING_HAND."""
    game = _then_shrink(_run(), "Stuntman", "Stuntman", "Stuntman", "Stuntman")
    assert game.hand_size == 0 and game.hand
    game._draw_to_hand_size()
    assert game.phase is Phase.PLAYING


def test_a_round_that_starts_with_no_hand_size_ends_at_once():
    """There is no hand to hold on to: the deal itself is what kills it."""
    game = _run("Stuntman", "Stuntman", "Stuntman", "Stuntman")
    assert game.phase is Phase.GAME_OVER


def test_an_arcana_pack_suspends_it():
    game = _run("Stuntman", "Stuntman", "Stuntman", "Stuntman")
    game.hand[:] = []
    game.phase = Phase.PACK
    game.pack = _pack(PackKind.ARCANA)
    game._draw_to_hand_size()
    assert game.phase is Phase.PACK


def test_a_buffoon_pack_does_not_suspend_it():
    """Only the two that deal a hand. A Buffoon pack deals nothing."""
    game = _run("Stuntman", "Stuntman", "Stuntman", "Stuntman")
    game.hand[:] = []
    game.phase = Phase.PACK
    game.pack = _pack(PackKind.BUFFOON)
    game._draw_to_hand_size()
    assert game.phase is Phase.GAME_OVER


def test_beating_the_target_does_not_save_you_from_it():
    """Engine: target met, still GAME_OVER. It is not a round-end check."""
    game = _run("Stuntman", "Stuntman", "Stuntman", "Stuntman")
    game.hand[:] = []
    game.chips_scored = game.blind.target + 1
    game._draw_to_hand_size()
    assert game.phase is Phase.GAME_OVER


def test_mr_bones_does_not_save_you_from_it_either():
    """Engine: Mr. Bones held, still GAME_OVER, and he is not consumed."""
    game = _run("Stuntman", "Stuntman", "Stuntman", "Stuntman", "Mr. Bones")
    game.hand[:] = []
    game.chips_scored = game.blind.target // 2
    game._draw_to_hand_size()
    assert game.phase is Phase.GAME_OVER
    assert any(j.name == "Mr. Bones" for j in game.jokers)


# ------------------------------------------------------------------
# running out of cards
# ------------------------------------------------------------------

def test_an_empty_deck_and_an_empty_hand_ends_the_run():
    game = _run()
    game.hand[:] = []
    game.draw_pile[:] = []
    game._draw_to_hand_size()
    assert game.phase is Phase.GAME_OVER


def test_an_empty_deck_with_cards_still_in_hand_carries_on():
    game = _run()
    game.draw_pile[:] = []
    game._draw_to_hand_size()
    assert game.phase is Phase.PLAYING


def test_mr_bones_cannot_save_a_run_that_is_out_of_cards():
    """He fires, and it buys nothing.

    The engine dissolves him, then drops the run back into SELECTING_HAND
    with the hand and deck still empty, so end_round runs again immediately
    and there is nothing left to spend the second time.
    """
    game = _run("Mr. Bones")
    game.hand[:] = []
    game.draw_pile[:] = []
    game.chips_scored = game.blind.target      # as generous as it gets
    game._draw_to_hand_size()
    assert game.phase is Phase.GAME_OVER


# ------------------------------------------------------------------
# Mr. Bones on the ordinary loss
# ------------------------------------------------------------------

def _out_of_hands(game, fraction):
    game.chips_scored = int(game.blind.target * fraction)
    game.hands_left = 1
    game.hand[:] = [Card(Rank.TWO, Suit.CLUBS)]
    game.step(Action(ActionType.PLAY, cards=(0,)))


def test_mr_bones_saves_a_run_at_a_quarter_of_the_target():
    game = _run("Mr. Bones")
    _out_of_hands(game, 0.30)
    assert game.phase is Phase.ROUND_EVAL
    assert not any(j.name == "Mr. Bones" for j in game.jokers)


def test_mr_bones_does_nothing_below_a_quarter():
    game = _run("Mr. Bones")
    _out_of_hands(game, 0.10)
    assert game.phase is Phase.GAME_OVER
    assert any(j.name == "Mr. Bones" for j in game.jokers)


def test_a_run_without_mr_bones_simply_ends():
    game = _run()
    _out_of_hands(game, 0.30)
    assert game.phase is Phase.GAME_OVER


def test_a_saved_round_pays_everything_except_the_blind_reward():
    """Engine, from $10: saved cashes out at $12, beaten at $15."""
    saved = _run("Mr. Bones")
    reward = saved.blind.reward
    _out_of_hands(saved, 0.30)

    beaten = _run("Mr. Bones")
    beaten.chips_scored = beaten.blind.target
    beaten.hands_left = 1
    beaten.hand[:] = [Card(Rank.TWO, Suit.CLUBS)]
    beaten.step(Action(ActionType.PLAY, cards=(0,)))

    assert reward > 0
    assert saved.pending_payout == beaten.pending_payout - reward


def test_a_saved_round_moves_on_to_the_next_blind():
    """The engine marks the failed blind Defeated -- you do not replay it."""
    game = _run("Mr. Bones")
    index = game.blind_index
    _out_of_hands(game, 0.30)
    assert game.blind_index == index + 1


# ------------------------------------------------------------------
# the other two flags nothing was reading
# ------------------------------------------------------------------

def test_to_the_moon_doubles_interest_past_the_cap():
    """Engine at $100 against the $25 cap: base $5, one moon $10, two $15."""
    def paid(moons):
        game = _run(*["To the Moon"] * moons)
        game.money = 100
        game.chips_scored = game.blind.target
        game.hands_left = 1
        game.hand[:] = [Card(Rank.TWO, Suit.CLUBS)]
        reward = game.blind.reward
        game.step(Action(ActionType.PLAY, cards=(0,)))
        return game.pending_payout - reward   # the hand was spent

    assert (paid(0), paid(1), paid(2)) == (5, 10, 15)


def test_a_debuffed_to_the_moon_pays_nothing_extra():
    """Debuffing a joker runs remove_from_deck, which takes the counter with it.

    Measured: interest_amount reads 1 with no To the Moon, 2 while one is
    held, and 1 again the moment it is debuffed.
    """
    game = _run("To the Moon")
    game.jokers[0].debuffed = True
    game.money = 100
    game.chips_scored = game.blind.target
    game.hands_left = 1
    game.hand[:] = [Card(Rank.TWO, Suit.CLUBS)]
    reward = game.blind.reward
    game.step(Action(ActionType.PLAY, cards=(0,)))
    assert game.pending_payout - reward == 5
