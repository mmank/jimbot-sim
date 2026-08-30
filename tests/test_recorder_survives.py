"""A recording must survive the game going away.

Quitting Balatro is the ordinary way a session ends, and the socket reports it
however it feels like -- ConnectionResetError on Windows, a plain OSError
elsewhere, sometimes a decode failure on a half-written reply. The recorder
used to let that escape the polling loop, which killed the script before it
wrote anything, so closing the window threw away everything the player had
just done. The action log lives inside the game process, so there is nothing
to recover afterwards.

These tests kill the bridge in the middle of a session and check the file is
written anyway, with the actions that had already arrived.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, "scripts")

import record_replay as rr


class DyingBridge:
    """Answers a few polls, then behaves like a game that has been closed."""

    def __init__(self, actions, error):
        self.actions = actions
        self.error = error
        self.polls = 0
        self.stopped = False

    def command(self, name, *args):
        if name == "start_run":
            return {}
        if name == "set_money":
            return {}
        if name == "start_recording":
            return {"seed": "TESTSEED", "deck": "Red Deck", "start": {}}
        if name == "stop_recording":
            self.stopped = True
            return {}
        if name == "recording":
            self.polls += 1
            if self.polls > 2:
                raise self.error
            offset, count = args
            if count == 1:
                return {"total": len(self.actions)}
            return {"entries": self.actions[offset - 1:offset - 1 + count]}
        raise AssertionError("unexpected command %r" % name)

    def wait_until(self, *_args, **_kwargs):
        return {}


def _args(tmp_path):
    return SimpleNamespace(seed="TESTSEED", deck="Red Deck", stake=None,
                           money=None, launch=False,
                           out=tmp_path / "recording.json")


@pytest.mark.parametrize("error", [
    ConnectionResetError(10054, "closed by the remote host"),
    OSError("socket gone"),
    ValueError("Expecting value: line 1 column 1"),   # a half-written reply
])
def test_the_recording_is_saved_when_the_game_dies(monkeypatch, tmp_path, error):
    actions = [{"n": 1, "action": "select_blind", "params": {}, "before": {}},
               {"n": 2, "action": "play_cards_from_highlighted",
                "params": {"cards": [1]}, "before": {}}]
    bridge = DyingBridge(actions, error)
    monkeypatch.setattr(rr, "_connect", lambda _args: bridge)
    monkeypatch.setattr(rr.time, "sleep", lambda _s: None)

    args = _args(tmp_path)
    rr.do_record(args)

    assert args.out.exists(), "the recording was not written"
    payload = json.loads(args.out.read_text())
    assert payload["seed"] == "TESTSEED"
    assert len(payload["actions"]) == 2, "actions already received were lost"


def test_ctrl_c_still_saves(monkeypatch, tmp_path):
    """The path that already worked must keep working."""
    actions = [{"n": 1, "action": "select_blind", "params": {}, "before": {}}]
    bridge = DyingBridge(actions, KeyboardInterrupt())
    monkeypatch.setattr(rr, "_connect", lambda _args: bridge)

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(rr.time, "sleep", interrupt)
    args = _args(tmp_path)
    rr.do_record(args)

    assert args.out.exists()
    assert json.loads(args.out.read_text())["seed"] == "TESTSEED"
