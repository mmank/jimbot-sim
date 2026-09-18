"""Record a human playing Balatro, then replay it and check every step matches.

This is the strongest check that we actually control the game: if the same
actions applied through the bot's API produce the same money, the same jokers
and the same round score at every step, then what the agent sees and does is
what the game sees and does.

    python scripts/record_replay.py record --seed ABCDEFGH   # then play
    python scripts/record_replay.py replay recording.json
    python scripts/record_replay.py replay recording.json --headless

A divergence is informative either way: a missing action means the API cannot
express something a player can, and a state mismatch means we are reading or
driving the game differently than a click does.

The replay is `jimbot_sim.replay` on an `EngineRun` -- the replayer
ops/sim_replay.py runs on the simulator, and the interface every other driver
uses -- so a recorded action becomes the same `Action` here as there, and the
engine carries it out through the same translation a policy's actions go
through.
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
from jimbot_sim.replay import (SCORE_STABLE_PHASES, Recording,  # noqa: E402,F401
                               differences, normalise, ranked, replay)
from jimbot_sim.run import EngineRun  # noqa: E402

# Kept under its old name for the tests that pin the ranking.
_ranked = ranked


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
    recording = Recording.load(args.recording)
    bridge = _headless() if getattr(args, "headless", False) else _connect(args)
    # Endless, as the simulator replays it: a recording that goes on past
    # ante eight is the player having pressed "Endless Mode".
    run = EngineRun(bridge, endless=True)
    print(f"replaying {len(recording.actions)} actions on seed "
          f"{recording.seed} ({recording.deck})\n")

    def report(step, entry, snapshot, problems):
        if problems:
            print(f"  [{step:3d}] MISMATCH before {entry['action']}")
            for line in problems:
                print(f"         {line}")
        elif args.verbose:
            score = (f"chips {snapshot.get('chips')}"
                     if snapshot.get("phase") in SCORE_STABLE_PHASES
                     else "chips --")
            print(f"  [{step:3d}] ok   {entry['action']:32s} "
                  f"${snapshot.get('dollars'):<4} {score}")

    result = replay(run, recording, keep_going=not args.stop_on_mismatch,
                    stop_at=getattr(args, "stop_at", None), report=report)
    mismatches = len(result.mismatches)
    if result.problem is not None and not (
            result.mismatches and args.stop_on_mismatch):
        # Stopped on an action rather than on a difference: the API could not
        # express it, or the game refused it.
        print(f"  [{result.reached + 1:3d}] FAILED: {result.problem}")
        mismatches += 1
    if getattr(args, "stop_at", None) and result.reached >= args.stop_at:
        print(f"  stopped after {result.reached} actions")

    if getattr(args, "probe", None):
        # The engine is standing exactly where the recording left it, which is
        # the only place worth asking it anything.
        engine = getattr(bridge, "engine", None)
        print("\nprobe:", engine.eval("(function() %s end)()" % args.probe)
              if engine is not None else "probe needs --headless")

    print("\n" + f"{len(recording.actions)} actions, {mismatches} divergences")
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
