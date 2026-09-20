"""Carrying on past the win: pressed from inside the wait, and not the end.

A live endless run on WGRGWEFE beat the ante-8 boss and stopped dead. Two
things were wrong, and either alone was enough:

The game raises its win screen and pauses, and a paused game is never
`ready` -- the ROUND_EVAL underneath is waiting for a Cash Out button that
queued events build, and the pause holds those events. `state()` waited for
ready *before* pressing Continue, so it waited for a screen that only its own
press takes down. The run sat there until a human clicked it.

And then it stopped anyway: `won` is up for the rest of the run once the
final boss's round ends, so reading it as the end of the run ends an endless
one on the very blind it exists to play past -- and `advance` would not even
cash the winning round out.
"""

from jimbot_sim.bridge import BalatroBridge
from jimbot_sim.headless.driving import advance, is_over
from jimbot_sim.run import EngineRun


class WinScreen(BalatroBridge):
    """Won, paused on the win screen, and actionable once it is pressed."""

    def __init__(self) -> None:
        super().__init__()
        self.pauses = 0
        self.pressed = 0

    def _pause(self, seconds: float) -> None:
        self.pauses += 1

    def command(self, cmd: str, *args):
        if cmd == "state":
            return {"state_name": "ROUND_EVAL", "won": 1,
                    "ready": 1 if self.pressed else 0}
        if cmd == "continue_endless":
            self.pressed += 1
            return {"continued": True}
        raise AssertionError("unexpected command %r" % cmd)


class LostAtTheFinalBoss(BalatroBridge):
    """`won` is up and there is no run left to carry on. See driving.was_won."""

    def command(self, cmd: str, *args):
        if cmd == "state":
            return {"state_name": "GAME_OVER", "won": 1, "ready": 1}
        if cmd == "continue_endless":
            return {"continued": False}
        raise AssertionError("unexpected command %r" % cmd)


class Counting:
    """A driver that records what it was asked to do."""

    def __init__(self) -> None:
        self.cashed = 0

    def wait(self) -> None:
        pass

    def cash_out(self) -> None:
        self.cashed += 1

    def settle_pack(self) -> None:
        pass


def test_the_win_screen_is_pressed_while_the_game_is_not_ready():
    bridge = WinScreen()
    run = EngineRun(bridge, endless=True)
    state = run.state()
    assert bridge.pressed == 1
    assert state["ready"] == 1
    assert run.carried_on
    assert not run.is_over


def test_it_is_pressed_once():
    bridge = WinScreen()
    run = EngineRun(bridge, endless=True)
    run.state()
    run.state()
    run.state()
    assert bridge.pressed == 1


def test_a_run_that_stops_at_ante_eight_does_not_press_it():
    bridge = WinScreen()
    bridge.pressed = 1                 # ready, so the wait returns at once
    run = EngineRun(bridge, endless=False)
    run.state()
    assert bridge.pressed == 1
    assert run.is_over


def test_a_death_at_the_final_boss_is_still_over():
    run = EngineRun(LostAtTheFinalBoss(), endless=True)
    run.state()
    assert not run.carried_on
    assert run.is_over


def test_a_won_run_is_over_unless_it_is_endless():
    state = {"state_name": "ROUND_EVAL", "won": 1}
    assert is_over(state)
    assert not is_over(state, endless=True)
    assert is_over({"state_name": "GAME_OVER", "won": 1}, endless=True)


def test_the_winning_round_is_still_cashed_out():
    state = {"state_name": "ROUND_EVAL", "won": 1, "stop_use": 0,
             "shop_ready": 1}
    driver = Counting()
    assert not advance(state, driver)
    assert advance(state, driver, endless=True)
    assert driver.cashed == 1
