"""Remove actions the game made itself from a recording.

Recordings made before the recorder could tell the difference contain the
boss's own discards as if the player had made them. The Hook discards two
random cards after every hand played, by calling the same function the discard
button calls, so the recorder saw a discard and wrote one down.

The recorder no longer does this. Recordings already made still hold them, and
they cannot simply be replayed and ignored: the engine performs the boss's
discard again by itself, so a replay that also performs the recorded one takes
four cards instead of two and deals every later hand off a different deck.

The test used here is not the phase -- that is the symptom. It is whether the
action spent anything. A discard the player made costs one of their discards,
so discards_left falls by one before the next action. The boss's costs nothing
and leaves it unchanged. An action that spent nothing did not happen.

    python ops/clean_recording.py recordings/6.json          # report only
    python ops/clean_recording.py recordings/6.json --write  # rewrite it
"""

import argparse
import json
import shutil


def find_free_discards(actions):
    """Discard actions that cost the player nothing, so were not theirs."""
    suspect = []
    for i, action in enumerate(actions):
        if action["action"] != "discard_cards_from_highlighted":
            continue
        before = action.get("before") or {}
        following = actions[i + 1].get("before") if i + 1 < len(actions) else None
        if following is None:
            continue
        spent_now = before.get("discards_left")
        spent_next = following.get("discards_left")
        if spent_now is None or spent_next is None:
            continue
        # A real discard costs one. Anything else is the game's.
        if spent_next == spent_now:
            suspect.append((i, action, before, following))
    return suspect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("recording")
    parser.add_argument("--write", action="store_true",
                        help="rewrite the file, keeping a .bak beside it")
    args = parser.parse_args()

    with open(args.recording) as handle:
        payload = json.load(handle)
    actions = payload["actions"]

    suspect = find_free_discards(actions)
    if not suspect:
        print("%s: nothing to remove (%d actions)"
              % (args.recording, len(actions)))
        return

    print("%s: %d discard(s) that cost nothing\n" % (args.recording,
                                                     len(suspect)))
    for index, action, before, following in suspect:
        print("  action %d  phase %s  blind %r"
              % (index + 1, before.get("phase"), before.get("blind")))
        print("     cards %s, discards_left %s before and %s after"
              % (action.get("params", {}).get("card_ids"),
                 before.get("discards_left"), following.get("discards_left")))

    if not args.write:
        print("\nrun again with --write to remove them")
        return

    shutil.copyfile(args.recording, args.recording + ".bak")
    drop = {index for index, _a, _b, _f in suspect}
    payload["actions"] = [a for i, a in enumerate(actions) if i not in drop]
    for position, action in enumerate(payload["actions"], start=1):
        if "n" in action:
            action["n"] = position
    with open(args.recording, "w") as handle:
        json.dump(payload, handle)
    print("\nremoved %d, %d actions remain (original saved as %s.bak)"
          % (len(drop), len(payload["actions"]), args.recording))


if __name__ == "__main__":
    main()
