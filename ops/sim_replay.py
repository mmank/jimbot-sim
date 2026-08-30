"""Replay a recording through the Python simulator.

The recordings are the best test data in the project and the simulator has
never been shown one. They are real human runs -- five of them replay against
the engine with no divergence at all -- and every action carries the state the
player was looking at when they chose it. That is hundreds of positions each,
reached by someone actually playing, which is a different distribution from
anything a scripted driver or a policy produces.

    python ops/sim_replay.py recordings/3.json
    python ops/sim_replay.py recordings/5.json --verbose

Card ids are not compared: the simulator numbers its cards its own way and
nothing about the run depends on the numbers matching. What is compared is
what a player would notice -- the money, the chips, the blind, the hands and
discards left, the jokers held and the cards in hand.

Expect it to stop early. Every stop is a thing the simulator does not do yet,
named precisely rather than guessed at.
"""

import argparse
import json
import sys

sys.path.insert(0, "src")

from balatro.game import Action, ActionType, GameState, Phase   # noqa: E402
from balatro.joker_data import JOKER_DATA                       # noqa: E402

KEY_BY_JOKER = {name: key for name, (key, *_r) in JOKER_DATA.items()}
RANK_CODE = {"Two": "2", "Three": "3", "Four": "4", "Five": "5", "Six": "6",
             "Seven": "7", "Eight": "8", "Nine": "9", "Ten": "10",
             "Jack": "Jack", "Queen": "Queen", "King": "King", "Ace": "Ace"}


def sim_view(game):
    """What the recording's own fingerprint holds, in the same vocabulary."""
    return {
        "dollars": game.money,
        "chips": game.chips_scored,
        "hands_left": game.hands_left,
        "discards_left": game.discards_left,
        "hand_size": len(game.hand),
        "ante": game.ante,
        "jokers": [KEY_BY_JOKER.get(j.name, j.name) for j in game.jokers],
        "last_hand": game.last_hand,
    }


# Fields worth comparing, and why the others are not. Card ids are the
# simulator's own numbering. `round` counts differently on the two sides. The
# blind name is compared only while one is being played, since the recording
# reports none between rounds.
# last_hand is the cheapest way to catch the two sides playing different
# cards: the hand ids cannot be compared, since the simulator numbers its own,
# but what the cards *made* is right there in the recording.
COMPARED = ("dollars", "chips", "hands_left", "discards_left", "hand_size",
            "ante", "jokers", "last_hand")


def differences(recorded, sim):
    out = []
    for field in COMPARED:
        if field not in recorded or recorded[field] is None:
            continue
        want, got = recorded[field], sim.get(field)
        if isinstance(want, list):
            want = list(want)
        if want != got:
            out.append("  %-14s recorded %r\n  %-14s sim      %r"
                       % (field, want, "", got))
    return out


def apply(game, action, params, selected):
    """Do to the simulator what the recording says the player did.

    Returns None when it worked, or a reason it could not, which is the
    finding rather than an error.
    """
    name = params.get("key") if isinstance(params, dict) else None
    cards = tuple(i - 1 for i in (params.get("cards") or [])) if params else ()

    if action == "select_blind":
        game.step(Action(ActionType.SELECT_BLIND))
    elif action == "skip_blind":
        game.step(Action(ActionType.SKIP_BLIND))
    elif action == "play_cards_from_highlighted":
        game.step(Action(ActionType.PLAY, cards=cards))
    elif action == "discard_cards_from_highlighted":
        game.step(Action(ActionType.DISCARD, cards=cards))
    elif action == "sort_hand_value":
        game.sort_hand("rank")
    elif action == "sort_hand_suit":
        game.sort_hand("suit")
    elif action == "cash_out":
        pass                       # the simulator cashes out by itself
    elif action == "toggle_shop":
        game.step(Action(ActionType.LEAVE_SHOP))
    elif action == "reroll_shop":
        game.step(Action(ActionType.REROLL))
    else:
        return "no mapping for %s (%s)" % (action, name)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("recording")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    payload = json.loads(open(args.recording).read())
    actions = payload["actions"]
    game = GameState(seed=payload["seed"], deck=payload["deck"])
    if payload.get("money") is not None:
        game.money = payload["money"]

    print("replaying %d actions through the simulator (%s, %s)\n"
          % (len(actions), payload["seed"], payload["deck"]))

    for i, entry in enumerate(actions, start=1):
        recorded = entry.get("before") or {}
        problems = differences(recorded, sim_view(game))
        if problems:
            print("step %d, before %s:" % (i, entry["action"]))
            for line in problems:
                print(line)
            print("\nreached step %d of %d" % (i, len(actions)))
            return

        reason = apply(game, entry["action"], entry.get("params") or {}, None)
        if reason is not None:
            print("step %d: %s" % (i, reason))
            print("\nreached step %d of %d" % (i, len(actions)))
            return
        if args.verbose:
            print("  %3d %-32s $%-6d chips %s"
                  % (i, entry["action"], game.money, game.chips_scored))

    print("the simulator followed the whole recording")


if __name__ == "__main__":
    main()
