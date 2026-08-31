"""Put back an action a recording never captured.

The recorder wraps the game's own button functions, so anything it does not
wrap leaves no trace -- and for a while reroll_boss was one of those. A
recording made then shows ten dollars leaving the bankroll and the boss blind
changing, with nothing in between to say why. The engine's own replay of such
a recording diverges from that point exactly as the simulator's does, which is
how you can tell it is the recording at fault and not the reader.

Writing the press back in is editing evidence, so it is done here rather than
by hand, it names what it inserted, and every inserted action carries
`reconstructed: true` in its params. Nothing downstream treats that specially
-- it is there so that anyone reading the file can see which actions the
player actually took and which one this script decided they must have.

    python ops/repair_recording.py recordings/8.json            # report only
    python ops/repair_recording.py recordings/8.json --write

What it looks for is narrow on purpose: a step on the blind select screen
where the bankroll has dropped by exactly the ten dollars a boss reroll costs,
with no action of any kind between the two snapshots to account for it.
"""

import argparse
import copy
import json
import shutil

BOSS_REROLL_COST = 10


def find_unexplained_reroll(actions):
    """Steps where ten dollars vanished with nothing to explain it.

    The pair to look at is an action and the one after it: the second
    snapshot is the state the first action left behind, so a drop between
    them that the first action cannot account for happened in the gap.

    Leaving the shop is the one action in that position that can be trusted
    to cost nothing, which makes it the only place this can be certain. A
    purchase or a reroll in the gap would explain the money by itself.
    """
    found = []
    for i, action in enumerate(actions[:-1]):
        before, after = action.get("before") or {}, actions[i + 1].get("before") or {}
        if action["action"] != "toggle_shop":
            continue
        if after.get("phase") != "BLIND_SELECT":
            continue
        spent = (before.get("dollars") or 0) - (after.get("dollars") or 0)
        if spent == BOSS_REROLL_COST:
            found.append((i + 1, before, after))
    return found


def repair(actions, at, before, after):
    """One reroll_boss, standing where the press must have been."""
    snapshot = copy.deepcopy(after)
    # The state the button was pressed in: on the blind select screen, with
    # the money still in the bank. Everything else a reroll leaves alone --
    # it changes the boss, and the boss is not in a snapshot taken on that
    # screen, where `blind` is empty.
    snapshot["dollars"] = before.get("dollars")
    return {"action": "reroll_boss",
            "params": {"reconstructed": True},
            "before": snapshot}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("recording")
    parser.add_argument("--write", action="store_true",
                        help="rewrite the file, keeping a .bak beside it")
    args = parser.parse_args()

    with open(args.recording) as handle:
        payload = json.load(handle)
    actions = payload["actions"]

    already = sum(1 for a in actions if a["action"] == "reroll_boss")
    found = find_unexplained_reroll(actions)
    if not found:
        print("%s: nothing to put back (%d actions, %d boss rerolls already "
              "recorded)" % (args.recording, len(actions), already))
        return

    print("%s: %d place(s) where ten dollars went unexplained"
          % (args.recording, len(found)))
    for at, before, after in found:
        print("  after action %d (%s): $%s then $%s on the blind select "
              "screen" % (at, actions[at - 1]["action"],
                          before.get("dollars"), after.get("dollars")))

    if not args.write:
        print("\nrun again with --write to insert a reroll_boss at each")
        return

    shutil.copyfile(args.recording, args.recording + ".bak")
    for offset, (at, before, after) in enumerate(found):
        actions.insert(at + offset, repair(actions, at, before, after))
    for position, action in enumerate(actions, start=1):
        if "n" in action:
            action["n"] = position
    with open(args.recording, "w") as handle:
        json.dump(payload, handle)
    print("\ninserted %d, %d actions now (original saved as %s.bak)"
          % (len(found), len(actions), args.recording))


if __name__ == "__main__":
    main()
