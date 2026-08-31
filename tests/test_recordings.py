"""Replay real human games through the simulator, action for action.

These are the best test data in the project and they cost nothing to run: the
simulator is pure Python and a recording is a JSON file, so the whole set
replays in under two seconds with no engine involved.

Each recording carries, for every action, the state the player was looking at
when they chose it. So this is not "does a run finish" -- it is a step-by-step
comparison of money, chips, hands and discards left, the jokers and
consumables held, the blind and its target, and what the last hand was, at
several hundred points per recording, reached by someone actually playing.

Almost everything the simulator got wrong this month was found here rather
than by a differential against the engine, because a human does things a
scripted driver never does: drags cards, sells at odd moments, buys and uses
in one press, skips for tags, and walks into interactions nobody would think
to write a test for.

Two recordings do not replay to the end, and both are blocked on the
recording rather than on the simulator -- see REACHES.
"""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "ops"))

import sim_replay                                        # noqa: E402
from balatro.game import GameState                       # noqa: E402

RECORDINGS = pathlib.Path(__file__).resolve().parents[1] / "recordings"

# How far each recording gets. `None` means all the way; a number is a floor
# that must not regress, with the reason it stops.
#
#   6  Reaches 336 of 432.
#   8  Reaches 164 of 443. This one carries a reconstructed action -- see
#      ops/repair_recording.py -- because the recorder was not capturing the
#      boss reroll when it was made, and the engine's own replay of it now
#      runs through that point with nothing wrong.
REACHES = {1: None, 2: None, 3: None, 4: None, 5: None, 6: 336, 7: None, 8: 164}


def _replay(path):
    """Follow one recording, returning (steps reached, total, first problem)."""
    payload = json.loads(path.read_text())
    actions = sim_replay.merge_buy_and_use(payload["actions"])
    game = GameState(seed=payload["seed"], deck=payload["deck"],
                     stake=payload.get("stake") or 1)
    if payload.get("money") is not None:
        game.money = payload["money"]
    index = {card.uid: i for i, card in enumerate(game.full_deck)}

    for step, entry in enumerate(actions, start=1):
        recorded = entry.get("before") or {}
        sim_replay.match_hand_order(game, recorded.get("hand_ids"), index)
        sim_replay.match_joker_order(game, recorded.get("jokers"))
        problems = sim_replay.differences(recorded, sim_replay.sim_view(game))
        if problems:
            return step, len(actions), "\n".join(problems)
        reason = sim_replay.apply(game, entry["action"],
                                  entry.get("params") or {}, None)
        if reason is not None:
            return step, len(actions), reason
    return len(actions), len(actions), None


@pytest.mark.parametrize("number", sorted(REACHES))
def test_the_simulator_follows_a_real_game(number):
    path = RECORDINGS / ("%d.json" % number)
    if not path.exists():
        pytest.skip("%s is not checked in" % path.name)

    reached, total, problem = _replay(path)
    expected = REACHES[number]

    if expected is None:
        assert reached == total, (
            "recording %d used to replay end to end and now stops at step "
            "%d of %d:\n%s" % (number, reached, total, problem))
    else:
        assert reached >= expected, (
            "recording %d used to reach step %d and now stops at %d of "
            "%d:\n%s" % (number, expected, reached, total, problem))


def test_the_recordings_are_still_there():
    """A silent skip of every one of these would be easy to miss."""
    present = sorted(int(p.stem) for p in RECORDINGS.glob("*.json")
                     if p.stem.isdigit())
    assert len(present) >= 7, "only %d recordings found" % len(present)
    assert set(present) >= {1, 2, 3, 4, 5}
