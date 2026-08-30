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


def find_refused_buys(actions):
    """Purchases the game declined, which it does silently.

    Buying with no room for the card returns false out of buy_from_shop
    before anything happens -- the same for a joker with a full row and a
    consumable with no slot free. The button was pressed, so the recorder
    heard it, but no money left the bankroll and no card moved.

    Both halves are needed. A card bought for nothing -- the shop does hand
    those out -- also leaves the money alone, and it is a real purchase, so
    what the player owns has to be checked too.
    """
    suspect = []
    for i, action in enumerate(actions):
        if action["action"] != "buy_from_shop":
            continue
        params = action.get("params") or {}
        if params.get("buy_and_use"):
            continue                     # a different path, never refused here
        if i + 1 >= len(actions):
            continue
        # A buy-and-use click reaches older recordings as two entries: the buy,
        # then a use_card with no area. Nothing settles between them because
        # they are the same press, so the state looks unchanged and an honest
        # purchase looks refused. This is the pairing merge_buy_and_use uses.
        nxt = actions[i + 1]
        nxt_params = nxt.get("params") or {}
        if (nxt["action"] == "use_card" and nxt_params.get("area") == "?"
                and nxt_params.get("key") == params.get("key")):
            continue
        before = action.get("before") or {}
        following = nxt.get("before")
        if following is None:
            continue
        watched = ("dollars", "jokers", "consumables", "deck_size")
        if any(before.get(k) is None for k in watched):
            continue
        if all(before.get(k) == following.get(k) for k in watched):
            suspect.append((i, action, before, following))
    return suspect



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

    suspect = sorted(find_free_discards(actions) + find_refused_buys(actions),
                     key=lambda row: row[0])
    if not suspect:
        print("%s: nothing to remove (%d actions)"
              % (args.recording, len(actions)))
        return

    print("%s: %d action(s) the game did not perform"
          % (args.recording, len(suspect)))
    for index, action, before, following in suspect:
        if action["action"] == "buy_from_shop":
            print("  action %d  refused purchase of %r"
                  % (index + 1, (action.get("params") or {}).get("key")))
            print("     $%s unchanged, jokers %s, consumables %s"
                  % (before.get("dollars"), before.get("jokers"),
                     before.get("consumables")))
            continue
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
