"""Who may re-roll the boss blind, and how often.

Three different answers depending on what the run has redeemed, and the
simulator had none of them: the button was not modelled at all, so a
recording where the player pressed it diverged from that step on -- and the
engine's own replay of that recording diverged too, because the recorder was
not capturing the press either.

  no voucher        no reroll at all
  Director's Cut    one an ante; reset_blinds gives it back when a boss falls
  Retcon            any number

Affordability is measured against the debt floor rather than against zero,
so a Credit Card lets a broke run keep re-rolling.
"""

import pytest

from balatro.game import Action, ActionType, BOSS_REROLL_COST, GameState
from balatro.shop import VOUCHER_BY_KEY


def _run(*voucher_keys, money=100):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.money = money
    for key in voucher_keys:
        game.vouchers.append(VOUCHER_BY_KEY[key])
    return game


def test_without_a_voucher_there_is_no_reroll():
    assert not _run().can_reroll_boss


def test_directors_cut_allows_one_an_ante():
    game = _run("v_directors_cut")
    assert game.can_reroll_boss
    game.step(Action(ActionType.REROLL_BOSS))
    assert not game.can_reroll_boss, "Director's Cut re-rolled twice in an ante"


def test_retcon_allows_any_number():
    game = _run("v_retcon")
    for _ in range(4):
        assert game.can_reroll_boss
        game.step(Action(ActionType.REROLL_BOSS))
    assert game.can_reroll_boss


def test_the_reroll_costs_ten_and_changes_the_boss():
    game = _run("v_retcon")
    before, money = game.ante_boss, game.money
    seen = {before}
    for _ in range(6):
        game.step(Action(ActionType.REROLL_BOSS))
        seen.add(game.ante_boss)
    assert game.money == money - 6 * BOSS_REROLL_COST
    assert len(seen) > 1, "six re-rolls and the boss never changed"


def test_a_run_that_cannot_pay_cannot_reroll():
    assert not _run("v_retcon", money=BOSS_REROLL_COST - 1).can_reroll_boss
    assert _run("v_retcon", money=BOSS_REROLL_COST).can_reroll_boss


def test_beating_a_boss_gives_the_reroll_back():
    game = _run("v_directors_cut")
    game.step(Action(ActionType.REROLL_BOSS))
    assert not game.can_reroll_boss
    game.boss_rerolled = False          # what reset_blinds does at cash-out
    assert game.can_reroll_boss
