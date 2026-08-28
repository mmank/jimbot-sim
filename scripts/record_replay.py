"""Record a human playing Balatro, then replay it and check every step matches.

This is the strongest check that we actually control the game: if the same
actions applied through the bot's API produce the same money, the same jokers
and the same round score at every step, then what the agent sees and does is
what the game sees and does.

    python scripts/record_replay.py record --seed ABCDEFGH   # then play
    python scripts/record_replay.py replay recording.json

A divergence is informative either way: a missing action means the API cannot
express something a player can, and a state mismatch means we are reading or
driving the game differently than a click does.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from balatro_bridge import (DEFAULT_BUILD, DEFAULT_HOST, DEFAULT_PORT,  # noqa: E402
                            BalatroBridge, BridgeError, launch)

# Fields compared at every step. Money, jokers and round score are the ones
# that catch real divergence; hand_size and deck_size catch bookkeeping drift.
COMPARED = ("phase", "dollars", "chips", "ante", "round", "hands_left",
            "discards_left", "blind", "blind_chips", "hand_size", "jokers",
            "consumables", "hand_levels", "deck_size", "hands_played",
            "last_hand", "hand_ids", "tags", "joker_ids")

# Phases where the round score is a settled number rather than mid-animation.
# cash_out resets it with ease_chips(0) over several frames, so between the
# cash-out and the next blind its value depends on exactly when you sample --
# a race, not a divergence. It is compared exactly everywhere it is meaningful.
SCORE_STABLE_PHASES = {"SELECTING_HAND", "HAND_PLAYED", "ROUND_EVAL"}

# How a recorded G.FUNCS call is re-issued through the bot's API. These use the
# client's waiting wrappers rather than raw commands: the real game animates,
# and firing the next action before the last one lands is how a replay ends up
# playing an empty hand.
REPLAY = {
    "select_blind": lambda b, p: b.select_blind(),
    "skip_blind": lambda b, p: b.command("skip_blind"),
    "play_cards_from_highlighted": lambda b, p: _play(b, p),
    "discard_cards_from_highlighted": lambda b, p: _discard(b, p),
    "buy_from_shop": lambda b, p: b.buy(p["area"], p["index"]),
    "use_card": lambda b, p: _use(b, p),
    "sell_card": lambda b, p: b.sell(p["area"], p["index"]),
    "reroll_shop": lambda b, p: b.reroll(),
    "toggle_shop": lambda b, p: b.leave_shop(),
    "cash_out": lambda b, p: b.cash_out(),
    "skip_booster": lambda b, p: b.skip_pack(),
    "sort_hand_value": lambda b, p: b.command("sort_hand", "rank"),
    "sort_hand_suit": lambda b, p: b.command("sort_hand", "suit"),
}


def _select(bridge, params):
    """Reproduce the human's card selection.

    Selects by card identity (sort_id) rather than position, so a recording
    still picks the right cards if the hand ended up in a different order.
    Falls back to the recorded positions when identities are unavailable.
    """
    bridge.wait_until(lambda s: s["state_name"] == "SELECTING_HAND"
                      and len(s.get("hand") or []) > 0, timeout=30)
    bridge.command("clear")
    ids = params.get("card_ids")
    if ids:
        for card_id in ids:
            bridge.command("toggle_id", card_id)
    else:
        for index in params.get("cards") or []:
            bridge.toggle(index)


def _match_order(bridge, expected_ids, field: str, command: str) -> bool:
    """Put an area into the recorded order.

    Dragging is not a G.FUNCS call so it cannot be hooked, but the order it
    produces is observable and can be set directly. Joker order matters as much
    as hand order: effects resolve left to right, and a recording of this very
    project diverged by 434 chips on ordering alone.
    """
    if not expected_ids:
        return True
    current = normalise(bridge.command("check").get(field)) or []
    expected = list(expected_ids)
    if current == expected:
        return True
    if sorted(current) != sorted(expected):
        return False          # different contents, not a reorder
    bridge.command(command, *expected)
    return True


def _match_joker_order(bridge, expected: dict) -> bool:
    """Put the jokers into the recorded order.

    Prefers ids, but falls back to the recorded key order: a recording made
    before joker ids existed still carries the joker keys in order, and the
    key sequence is enough to reorder by. Jokers sharing a key are
    interchangeable for this purpose.
    """
    ids = normalise(expected.get("joker_ids"))
    if ids:
        return _match_order(bridge, ids, "joker_ids", "set_joker_order")

    keys = normalise(expected.get("jokers")) or []
    if not keys:
        return True
    state = bridge.command("check")
    current_keys = normalise(state.get("jokers")) or []
    if list(current_keys) == list(keys):
        return True
    if sorted(current_keys) != sorted(keys):
        return False
    live = normalise(bridge.command("state").get("jokers")) or []
    if len(live) != len(current_keys):
        return True
    # Greedily pair each recorded key with an unused joker carrying that key.
    remaining = {i: k for i, k in enumerate(current_keys)}
    order = []
    for key in keys:
        for i, have in list(remaining.items()):
            if have == key:
                order.append(live[i]["id"])
                del remaining[i]
                break
    if len(order) != len(live):
        return False
    bridge.command("set_joker_order", *order)
    return True


def _match_hand_order(bridge, expected_ids) -> bool:
    """Put the hand in the recorded order.

    Dragging a card is not a G.FUNCS call so it cannot be hooked, but the order
    it produces is observable -- and reproducible by setting it directly. This
    is what lets a replay follow a hand the player rearranged by hand.
    """
    if not expected_ids:
        return True
    state = bridge.command("check")
    current = normalise(state.get("hand_ids")) or []
    expected = list(expected_ids)
    if current == expected:
        return True
    if sorted(current) != sorted(expected):
        return False          # different cards entirely, not a reorder
    bridge.command("set_hand_order", *expected)
    return True


def _play(bridge, params):
    _select(bridge, params)
    return bridge.play()


def _discard(bridge, params):
    _select(bridge, params)
    return bridge.discard()


def _use(bridge, params):
    """use_card covers consumables, vouchers, packs and pack picks."""
    area, index = params.get("area"), params.get("index")
    if area == "consumeables":
        if params.get("targets"):
            _select(bridge, {"cards": params["targets"]})
        return bridge.use_consumable(index)
    if area == "pack_cards":
        return bridge.pick_pack(index)
    if area == "shop_booster":
        return bridge.buy_pack(area, index)
    return bridge.buy(area, index)


def normalise(value):
    """Lua tables arrive as dicts keyed 1..n; compare them as lists."""
    if isinstance(value, dict):
        if not value:
            return []
        if all(isinstance(k, int) for k in value):
            return [normalise(value[k]) for k in sorted(value)]
        return {k: normalise(v) for k, v in sorted(value.items())}
    return value


def differences(expected: dict, actual: dict) -> list[str]:
    out = []
    scoring = expected.get("phase") in SCORE_STABLE_PHASES
    for field in COMPARED:
        if field == "chips" and not scoring:
            continue
        # A recording made before a field existed simply does not have it;
        # that is not a divergence, and treating it as one buries the real
        # ones under noise.
        if field not in expected:
            continue
        want, got = normalise(expected.get(field)), normalise(actual.get(field))
        if want != got:
            out.append(f"{field}: recorded {want!r} but replayed {got!r}")
    return out


# ---------------------------------------------------------------- record

# G.FUNCS names are the game's; these are what a player would call them.
FRIENDLY = {
    "select_blind": "select blind",
    "skip_blind": "skip blind",
    "play_cards_from_highlighted": "play",
    "discard_cards_from_highlighted": "discard",
    "buy_from_shop": "buy",
    "sell_card": "sell",
    "use_card": "use",
    "reroll_shop": "reroll",
    "toggle_shop": "leave shop",
    "cash_out": "cash out",
    "skip_booster": "skip pack",
}


def describe(entry: dict) -> str:
    """One readable line per recorded action, so a player can check live that
    what they did is what got captured."""
    action = entry["action"]
    params = normalise(entry.get("params")) or {}
    name = FRIENDLY.get(action, action)
    detail = ""

    cards = params.get("cards")
    if cards:
        detail = f"cards {list(cards)}"
    elif params.get("key"):
        where = params.get("area", "")
        detail = str(params["key"])
        if where and where != "consumeables":
            detail += f" from {where}"
        targets = params.get("targets")
        if targets:
            detail += f" on cards {list(targets)}"
    elif params.get("blind"):
        detail = str(params["blind"])

    before = entry.get("before") or {}
    state = (f"${before.get('dollars', '?')}"
             f"  ante {before.get('ante', '?')}")
    if before.get("phase") in SCORE_STABLE_PHASES:
        state += f"  chips {before.get('chips', 0)}/{before.get('blind_chips', 0)}"
    jokers = normalise(before.get("jokers")) or []
    if jokers:
        state += f"  jokers {len(jokers)}"
    tags = normalise(before.get("tags")) or []
    if tags:
        state += f"  tags {list(tags)}"
    return f"  {entry['n']:3d}  {name:<12} {detail:<34} {state}"


def do_record(args) -> None:
    bridge = _connect(args)
    print(f"starting a run on seed {args.seed}...")
    bridge.command("start_run", args.seed,
                   *([args.deck.replace(" ", "_")] if args.deck else []))
    bridge.wait_until(lambda s: s.get("in_run"), timeout=60)
    info = bridge.command("start_recording")
    print(f"\nRECORDING. Play the game in the window -- blinds, hands, shop,")
    print(f"whatever you like. Press Ctrl+C here when you are done.\n")
    print(f"  seed {info['seed']}  deck {info['deck']}")

    def drain(actions: list[dict]) -> None:
        """Print every action that has landed since the last check."""
        total = bridge.command("recording", 1, 1).get("total", 0)
        while len(actions) < total:
            chunk = bridge.command("recording", len(actions) + 1, 20)
            got = normalise(chunk.get("entries")) or []
            if not got:
                break
            for entry in got:
                print(describe(entry), flush=True)
            actions.extend(got)

    # Print each action as it lands rather than a running count: the point of
    # watching is to see that what you did is what got captured.
    print(f"  {'#':>3}  {'action':<12} {'detail':<34} state\n")
    actions: list[dict] = []
    try:
        while True:
            time.sleep(0.4)
            drain(actions)
    except KeyboardInterrupt:
        print()

    # Anything between the last poll and the interrupt. Never let a failure
    # here lose a recording the player just spent time making.
    try:
        drain(actions)
    except (BridgeError, OSError) as error:
        print(f"  (could not fetch the last actions: {error})")
    try:
        bridge.command("stop_recording")
    except (BridgeError, OSError):
        pass

    payload = {"seed": info["seed"], "deck": info["deck"],
               "start": normalise(info["start"]), "actions": actions}
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"saved {len(actions)} actions to {args.out}")


# ---------------------------------------------------------------- replay

def do_replay(args) -> None:
    payload = json.loads(args.recording.read_text(encoding="utf-8"))
    actions = payload["actions"]
    bridge = _connect(args)

    print(f"replaying {len(actions)} actions on seed {payload['seed']} "
          f"({payload['deck']})\n")
    bridge.command("start_run", payload["seed"],
                   payload["deck"].replace(" ", "_"))
    # in_run flips before the blind-select screen exists, and select_blind is
    # a no-op until it does.
    bridge.wait_until(lambda s: s.get("in_run") and s.get("ready"), timeout=60)

    mismatches = 0
    for i, entry in enumerate(actions, start=1):
        action, params = entry["action"], normalise(entry["params"])

        # Reproduce any reordering the player did (dragging, or the sort
        # buttons) before comparing, so a rearranged hand is followed rather
        # than reported as a divergence.
        _match_hand_order(bridge, normalise(entry["before"].get("hand_ids")))
        _match_joker_order(bridge, entry["before"])

        # Compare before acting: the recorded `before` is the state the human
        # was looking at when they made this choice.
        actual = normalise(bridge.command("check"))
        problems = differences(entry["before"], actual)
        if problems:
            mismatches += 1
            print(f"  [{i:3d}] MISMATCH before {action}")
            for line in problems:
                print(f"         {line}")
            if args.stop_on_mismatch:
                break
        elif args.verbose:
            score = (f"chips {actual['chips']}"
                     if actual.get("phase") in SCORE_STABLE_PHASES
                     else "chips --")
            print(f"  [{i:3d}] ok   {action:32s} "
                  f"${actual['dollars']:<4} {score}")

        handler = REPLAY.get(action)
        if handler is None:
            print(f"  [{i:3d}] NO REPLAY for {action} -- the API cannot "
                  f"express this action")
            mismatches += 1
            break
        try:
            handler(bridge, params)
        except BridgeError as error:
            print(f"  [{i:3d}] FAILED {action}: {error}")
            mismatches += 1
            break
        bridge.wait_until(lambda s: s.get("ready"), timeout=30)

    print(f"\n{len(actions)} actions, {mismatches} divergences")
    if not mismatches:
        print("the replay matched the recording at every step")
    raise SystemExit(1 if mismatches else 0)


def _connect(args) -> BalatroBridge:
    """Attach to a running game, starting one if there is not one already.

    Requiring an explicit --launch just means the first attempt usually fails
    with a connection error, so this falls back to launching.
    """
    if not args.launch:
        try:
            return BalatroBridge(timeout=30).connect()
        except BridgeError:
            print("no game listening on "
                  f"{DEFAULT_HOST}:{DEFAULT_PORT}, starting one...")
    else:
        print("launching the modded game...")

    if not DEFAULT_BUILD.exists():
        raise SystemExit(
            f"{DEFAULT_BUILD} not found. Build it first:\n"
            f"    python scripts/build_modded_game.py")
    try:
        _process, bridge = launch(wait=90)
    except BridgeError as error:
        raise SystemExit(
            f"{error}\n\nBalatro quits immediately if Steam is not running "
            "(main.lua calls love.event.quit when luasteam fails to init), "
            "so check Steam is up.")
    return bridge


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)

    rec = sub.add_parser("record", help="record yourself playing")
    rec.add_argument("--seed", default="ABCDEFGH")
    rec.add_argument("--deck", default=None)
    rec.add_argument("--out", type=Path, default=Path("recording.json"))
    rec.add_argument("--launch", action="store_true")
    rec.set_defaults(func=do_record)

    rep = sub.add_parser("replay", help="replay a recording and compare")
    rep.add_argument("recording", type=Path)
    rep.add_argument("--launch", action="store_true")
    rep.add_argument("--verbose", action="store_true")
    rep.add_argument("--stop-on-mismatch", action="store_true")
    rep.set_defaults(func=do_replay)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
