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

All eight now replay to the end. The last to get there was 8, which spent a
long time stopping at 190 of 443 and took seven fixes to finish: the legendary
pool key, Merry Andy handing over its discards on arrival, and five separate
places where a copier was not treated as the joker it copies -- in scoring, in
held retriggers, in the before-hand hooks -- plus the ordering of the bosses
that move money, and DNA's copy counting as a held card.
"""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "ops"))

import sim_replay                                        # noqa: E402
from jimbot_sim.game import GameState                       # noqa: E402

RECORDINGS = pathlib.Path(__file__).resolve().parents[1] / "recordings"

# How far each recording gets. `None` means all the way; a number would be a
# floor that must not regress, with the reason it stops.
#
# All thirteen go the whole way. 8 is the one that took the longest -- it
# carries a reconstructed action, see ops/repair_recording.py, because the
# recorder was not capturing the boss reroll when it was made -- and it is
# worth keeping the entry shape around: a recording that stops part way is a
# perfectly good regression test for the part it does reach.
#
# This table is what the suite replays, not the directory, so a recording
# checked in without an entry here proves nothing. 9 to 12 sat that way for
# some time; they are listed now, with 14.
REACHES = {1: None, 2: None, 3: None, 4: None, 5: None, 6: None, 7: None,
           8: None, 9: None, 10: None, 11: None, 12: None, 14: None}


def _replay(path):
    """Follow one recording, returning (steps reached, total, first problem)."""
    payload = json.loads(path.read_text())
    actions = sim_replay.merge_buy_and_use(payload["actions"])
    # Endless, because a human does not stop at the win: 11, 12 and 14 all
    # reach ante nine. A run built without it is one the simulator believes
    # should be over, and 12 stopped at 361 of 362 on "the simulator's shop
    # offers no voucher" -- the last shop, past ante eight, where a finished
    # run no longer stocks one. ops/sim_replay.py and
    # ops/explain_divergence.py have always built it this way, and that flag
    # is the whole of why they disagreed with this test about that recording.
    game = GameState(seed=payload["seed"], deck=payload["deck"],
                     stake=payload.get("stake") or 1, endless=True)
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
