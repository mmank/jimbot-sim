"""Replay a recording through the Python simulator.

The recordings are the best test data in the project. They are real human
runs, and every action carries the state the player was looking at when they
chose it. That is hundreds of positions each, reached by someone actually
playing, which is a different distribution from anything a scripted driver or
a policy produces.

    python ops/sim_replay.py recordings/3.json
    python ops/sim_replay.py recordings/5.json --verbose

The replay itself is `jimbot_sim.replay` on a `SimRun` -- the same replayer
scripts/record_replay.py runs on the engine -- so what is compared and how a
recorded action becomes a move is the same on both. Card ids are not compared
here: the simulator numbers its cards its own way and nothing about the run
depends on the numbers matching.

Expect it to stop early on a new recording. Every stop is a thing the
simulator does not do yet, named precisely rather than guessed at.

The names below the CLI are kept for the tools that follow a recording step
by step on a `GameState` of their own (tests/test_recordings.py,
ops/money_ledger.py, ops/explain_divergence.py, and several in the training
repository); they are the shared replayer's parts.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimbot_sim.replay import (Recording, differences,  # noqa: E402,F401
                               merge_buy_and_use, replay, to_move)
from jimbot_sim.run import (SimRun, _sim_names,  # noqa: E402,F401
                            match_hand_order, match_joker_order,
                            sim_fingerprint)
from jimbot_sim.state import state_dict  # noqa: E402

RANK_CODE = {"Two": "2", "Three": "3", "Four": "4", "Five": "5", "Six": "6",
             "Seven": "7", "Eight": "8", "Nine": "9", "Ten": "10",
             "Jack": "Jack", "Queen": "Queen", "King": "King", "Ace": "Ace"}

# The recorder's snapshot, as far as the simulator can say it.
sim_view = sim_fingerprint


def apply(game, action, params, selected=None):
    """Do to `game` what the recording says the player did.

    Returns None when it worked, or a reason it could not, which is the
    finding rather than an error. `selected` is unused and kept for callers.
    """
    move = to_move({"action": action, "params": params},
                   lambda: state_dict(game),
                   lambda row: _sim_names().key(row, live=False))
    if isinstance(move, str):
        return move
    if move.sort:
        game.sort_hand(move.sort)
    elif move.action is not None:
        game.step(move.action)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("recording")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    recording = Recording.load(args.recording)
    print("replaying %d actions through the simulator (%s, %s)\n"
          % (len(recording.actions), recording.seed, recording.deck))

    # Endless, because a recording that carries on past ante eight *is*
    # the evidence that the player pressed continue. Without it the
    # simulator declares the run won, returns before `_open_shop`, and
    # every later action is refused against a shop that is not there --
    # which is where recording 12 stopped, one action from the end.
    run = SimRun(endless=True)

    def report(step, entry, snapshot, problems):
        if problems:
            print("step %d, before %s:" % (step, entry["action"]))
            for line in problems:
                print("  " + line)
        elif args.verbose:
            hand = " ".join("%s%s" % (RANK_CODE[c.rank.name.title()],
                                      c.suit.name[0])
                            for c in run.game.hand)
            print("  %3d %-30s chips %-6d %s"
                  % (step, entry["action"], run.game.chips_scored, hand))

    result = replay(run, recording, report=report)
    if result.problem is None:
        print("the simulator followed the whole recording")
    else:
        if not result.mismatches:
            print("step %d: %s" % (result.reached + 1, result.problem))
        print("\nreached step %d of %d" % (result.reached + 1, result.total))


if __name__ == "__main__":
    main()
