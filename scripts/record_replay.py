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

from jimbot_sim.bridge import (DEFAULT_BUILD, DEFAULT_HOST, DEFAULT_PORT,  # noqa: E402
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
    "buy_and_use": lambda b, p: b.buy_and_use(p["area"], p["index"],
                                              cards=p.get("targets") or None),
    "sell_card": lambda b, p: b.sell(p["area"], p["index"]),
    "reroll_shop": lambda b, p: b.reroll(),
    "reroll_boss": lambda b, p: b.command("reroll_boss"),
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

    Waits for a hand rather than for the hand-selection phase: cards are also
    selectable while a pack is open, which is how a tarot from an Arcana pack
    gets its targets.
    """
    bridge.wait_hand_dealt()
    bridge.command("clear")
    ids = params.get("card_ids")
    wanted = list(ids) if ids else list(params.get("cards") or [])
    if ids:
        try:
            for card_id in ids:
                bridge.command("toggle_id", card_id)
        except BridgeError:
            # A card added during the run carries an id from the run's card
            # counter, and that counter drifts when the real game builds
            # something the engine does not. The recorded positions are the
            # same click and do not drift, so they are the better answer once
            # an id cannot be found.
            bridge.command("clear")
            for index in params.get("cards") or []:
                bridge.toggle(index)
    else:
        for index in params.get("cards") or []:
            bridge.toggle(index)

    # Confirm the selection took. A tarot applied to fewer cards than it needs
    # crashes the game rather than refusing, so a partial selection must be
    # caught here rather than discovered downstream.
    got = int(bridge.command("check").get("hand_size") is not None
              and bridge.state().get("selection_size", 0))
    if wanted and got != len(wanted):
        raise BridgeError(
            f"selected {got} of {len(wanted)} cards -- the hand may not have "
            f"been dealt yet")


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
    if ids and _match_order(bridge, ids, "joker_ids", "set_joker_order"):
        return True
    # Ids can be right about identity and still not match: the run's card
    # counter moves whenever the real game builds something the engine never
    # does, so a card can be the same card under a different number. Falling
    # through to the keys keeps the order reproducible when that happens,
    # rather than leaving the jokers in whatever order they landed -- and
    # joker order decides the order effects resolve in.
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
    if sorted(current) == sorted(expected):
        bridge.command("set_hand_order", *expected)
        return True

    # The numbers can drift apart while the hand is the same hand: the run's
    # card counter moves whenever the real game builds something the engine
    # does not, so a card is the same card under a different number. That is
    # what _ranked exists for, and the comparison already uses it -- but this
    # was matching raw, so a hand the player had merely dragged looked like a
    # different set of cards and was left in whatever order it landed in.
    # Ranking both sides and mapping back gives the reorder anyway.
    if len(current) != len(expected) or _ranked(current) == _ranked(expected):
        return _ranked(current) == _ranked(expected)
    by_rank = sorted(current)
    wanted = [by_rank[r] for r in _ranked(expected)]
    bridge.command("set_hand_order", *wanted)
    return True


def _play(bridge, params):
    _select(bridge, params)
    return bridge.play()


def _discard(bridge, params):
    _select(bridge, params)
    return bridge.discard()


def merge_buy_and_use(actions):
    """Fold the shop's buy-and-use click back into one action.

    The game routes that button through buy_from_shop, which then calls
    use_card itself, so recordings made before the recorder knew about it hold
    two entries for one click: a buy, and a use of a card that by then belongs
    to no area at all (`area: "?"`). Replaying both buys the card into the
    consumable slots and then cannot find it to use.
    """
    merged, skip = [], False
    for i, action in enumerate(actions):
        if skip:
            skip = False
            continue
        params = action.get("params") or {}
        following = actions[i + 1] if i + 1 < len(actions) else None
        pair = (action.get("action") == "buy_from_shop" and following
                and following.get("action") == "use_card"
                and (following.get("params") or {}).get("area") == "?"
                and (following.get("params") or {}).get("key") == params.get("key"))
        if params.get("buy_and_use") or pair:
            action = dict(action, action="buy_and_use")
            skip = bool(pair)
        merged.append(action)
    return merged


def _use(bridge, params):
    """use_card covers consumables, vouchers, packs and pack picks."""
    area, index = params.get("area"), params.get("index")
    # Hand the targets to the client rather than selecting here: the selection
    # has to happen after the previous consumable has finished resolving (it
    # holds locks.use and clears the highlight on its way out) and after a
    # pack's targeting hand has finished being dealt. The client knows how to
    # wait for both; selecting up front and passing none loses that.
    targets = params.get("targets") or None
    if area == "consumeables":
        return bridge.use_consumable(index, cards=targets)
    if area == "pack_cards":
        return bridge.pick_pack(index, cards=targets)
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


# A card's id is its place in the run's card counter, and that counter moves
# for reasons a recording cannot capture: opening the deck collection screen
# builds fifty-two Card objects to fan out behind the deck art, and the
# recorder hooks game actions, not looking at a menu. The engine never builds
# that screen, so from the first time a player opens one, every card made
# afterwards is numbered differently while being the same card in the same
# place.
#
# What the numbers still carry is their order. Both counters only ever go up,
# and the extra cards are all created at one moment on one side, so a card
# made earlier has a lower id than one made later on both sides even though
# neither number matches. Ranking each list against itself -- smallest 0, next
# 1 -- throws away the offset and keeps that order.
#
# It beats blanking the drifted ones, which was the first attempt here: two
# cards blanked to "new" are indistinguishable, and their relative age is
# exactly the thing worth comparing. [68, 53, 54, 136] and [68, 53, 54, 83]
# both rank to [2, 0, 1, 3], and a card genuinely out of place still moves a
# rank and still shows up.
ID_FIELDS = ("hand_ids", "joker_ids")


def _ranked(ids):
    """Replace each id by its position in the sorted list of ids present."""
    if not isinstance(ids, (list, tuple)):
        return ids
    if not all(isinstance(i, int) for i in ids):
        return ids
    rank = {value: place for place, value in enumerate(sorted(ids))}
    return [rank[i] for i in ids]


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
        if field in ID_FIELDS:
            want, got = _ranked(want), _ranked(got)
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


def _headless():
    """Boot the engine and drive it with the same client the real game uses.

    Worth being clear about what this checks and what it does not. The
    fingerprint being compared is bot_api's own, so a divergence in what the
    game *did* -- a wrong index, a missing action, money or score that does not
    add up -- shows here in seconds. Timing divergences cannot: the engine
    pumps frames to completion, so the whole class of fault that comes from
    acting while the game is still animating is invisible. This is a filter in
    front of the real run, not a replacement for it.
    """
    from jimbot_sim.headless.runtime import HeadlessBalatro
    from jimbot_sim.bridge.headless import HeadlessBridge

    print("booting the headless engine...")
    return HeadlessBridge(HeadlessBalatro().boot())


def do_record(args) -> None:
    bridge = _connect(args)
    print(f"starting a run on seed {args.seed}...")
    # Deck is positional before stake, so a stake with no deck still needs a
    # deck argument in the slot.
    deck = (args.deck or "Red Deck").replace(" ", "_")
    bridge.command("start_run", args.seed, deck,
                   *([args.stake] if args.stake else []))
    bridge.wait_until(lambda s: s.get("in_run"), timeout=60)
    if args.money is not None:
        bridge.command("set_money", args.money)
        print(f"bankroll set to ${args.money}")
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
    ended = "you stopped it"
    try:
        while True:
            time.sleep(0.4)
            try:
                drain(actions)
            except Exception as error:            # noqa: BLE001
                # The game window closing is the ordinary way a session ends,
                # and it arrives as whatever the socket felt like raising. A
                # recording someone just spent an hour making is not worth
                # losing to an exception type nobody predicted, so anything
                # from the poll ends the loop and falls through to the save.
                ended = f"the game went away ({type(error).__name__})"
                break
    except KeyboardInterrupt:
        print()

    # Anything between the last poll and the end. Never let a failure here
    # lose a recording the player just spent time making.
    try:
        drain(actions)
    except Exception as error:                    # noqa: BLE001
        print(f"  (could not fetch the last actions: {error})")
    try:
        bridge.command("stop_recording")
    except Exception:                             # noqa: BLE001
        pass

    payload = {"seed": info["seed"], "deck": info["deck"],
               "money": args.money, "stake": args.stake,
               "start": normalise(info["start"]), "actions": actions}
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"saved {len(actions)} actions to {args.out} ({ended})")


# ---------------------------------------------------------------- replay

def do_replay(args) -> None:
    payload = json.loads(args.recording.read_text(encoding="utf-8"))
    actions = merge_buy_and_use(payload["actions"])
    bridge = _headless() if getattr(args, "headless", False) else _connect(args)

    print(f"replaying {len(actions)} actions on seed {payload['seed']} "
          f"({payload['deck']})\n")
    bridge.command("start_run", payload["seed"],
                   payload["deck"].replace(" ", "_"),
                   *([payload["stake"]] if payload.get("stake") else []))
    # in_run flips before the blind-select screen exists, and select_blind is
    # a no-op until it does.
    bridge.wait_until(lambda s: s.get("in_run") and s.get("ready"), timeout=60)
    # Reapply whatever bankroll the recording was made with, before comparing
    # anything -- otherwise every step diverges on dollars.
    if payload.get("money") is not None:
        bridge.command("set_money", payload["money"])

    mismatches = 0
    for i, entry in enumerate(actions, start=1):
        action, params = entry["action"], normalise(entry["params"])

        # If the recording had a hand here, let dealing finish before
        # comparing: an Arcana pack deals its targeting hand over several
        # frames, and comparing mid-deal reports a divergence that is really
        # just impatience.
        if (entry["before"].get("hand_size") or 0) > 0:
            bridge.wait_hand_dealt(timeout=10.0)

        # Reproduce any reordering the player did (dragging, or the sort
        # buttons) before comparing, so a rearranged hand is followed rather
        # than reported as a divergence.
        _match_hand_order(bridge, normalise(entry["before"].get("hand_ids")))
        _match_joker_order(bridge, entry["before"])

        # A use_card on a card in no area is the game using something it made
        # itself: a Meteor Tag opening its own Celestial pack calls use_card
        # on a card that belongs to no shop row and no consumable slot, so the
        # recorder writes "?" for where it was. The engine fires the tag and
        # opens the pack unprompted, so replaying the entry as well opens a
        # second one.
        #
        # A player's buy-and-use also lands here in older recordings, as a buy
        # followed by an area-less use -- but merge_buy_and_use has already
        # folded those into their buy by now. What is left is the game's.
        if (action == "use_card" and (params or {}).get("area") == "?"):
            if args.verbose:
                print(f"  [{i:3d}] --   {action:32s} "
                      f"(the game's own, the engine repeats it)")
            continue

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
        if getattr(args, "stop_at", None) and i >= args.stop_at:
            print(f"  stopped after {i} actions")
            break

    if getattr(args, "probe", None):
        # The engine is standing exactly where the recording left it, which is
        # the only place worth asking it anything.
        engine = getattr(bridge, "engine", None)
        print("\nprobe:", engine.eval("(function() %s end)()" % args.probe)
              if engine is not None else "probe needs --headless")

    print("\n" + f"{len(actions)} actions, {mismatches} divergences")
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
    rec.add_argument("--stake", type=int, default=None,
                     help="1-8: White, Red, Green, Black, Blue, Purple, "
                          "Orange, Gold. Higher stakes add eternal, "
                          "perishable and rental jokers")
    rec.add_argument("--money", type=int, default=None,
                     help="start with this bankroll, to set a situation up "
                          "quickly; stored in the recording and reapplied on "
                          "replay, so it does not itself cause a divergence")
    rec.set_defaults(func=do_record)

    rep = sub.add_parser("replay", help="replay a recording and compare")
    rep.add_argument("recording", type=Path)
    rep.add_argument("--launch", action="store_true")
    rep.add_argument("--verbose", action="store_true")
    rep.add_argument("--stop-on-mismatch", action="store_true")
    rep.add_argument("--headless", action="store_true",
                     help="replay against the in-process engine instead of "
                          "the running game: seconds rather than minutes, and "
                          "no window. Catches wrong actions and wrong indices; "
                          "cannot catch timing, because nothing animates")
    rep.add_argument("--probe", metavar="LUA",
                     help="after the last replayed action, evaluate this Lua "
                          "against the engine and print it. With --stop-at, "
                          "the way to ask the engine what it holds at the "
                          "exact step the simulator disagrees about")
    rep.add_argument("--stop-at", type=int, default=None,
                     help="replay only this many actions")
    rep.set_defaults(func=do_replay)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
