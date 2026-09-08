"""Play the real, visible Balatro with the *engine's own* best play.

Not the trained bot. This asks bot_api for `best_play` -- the scripted
heuristic that picks the highest-scoring hand it can see -- and drives the
window with it. It is the bridge's demonstration and its end-to-end check:
that the mod loads, that the socket answers, that an action taken here has
the consequence it should in a game that animates.

It reads no checkpoint and holds no model. Driving the window with a trained
agent is a separate concern and lives with whatever does the training.

Build the modded game first:

    python scripts/build_modded_game.py

Then either launch it yourself and run this, or pass --launch to do both.
The game window is the point: it is what driving the actual game looks like,
at the actual game's speed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimbot_sim.bridge import BalatroBridge, BridgeError, launch  # noqa: E402

RANKS = {1: "2", 2: "3", 3: "4", 4: "5", 5: "6", 6: "7", 7: "8", 8: "9",
         9: "10", 10: "J", 11: "Q", 12: "K", 13: "A"}
SUITS = {1: "S", 2: "H", 3: "C", 4: "D"}


def show_hand(hand) -> str:
    return " ".join(f"{RANKS.get(c['rank'], '?')}{SUITS.get(c['suit'], '?')}"
                    for c in (hand or []))


def play_blind(bridge: BalatroBridge, state: dict, pace: float) -> dict:
    """Play one blind: best hand each time, discarding when it is weak."""
    while state["state_name"] == "SELECTING_HAND":
        best = bridge.command("best_play")
        choice = best["cards"]
        if not choice:
            break

        needed = state["blind_chips"] - state["chips"]
        share = needed / max(1, state["hands_left"])
        discarding = (best["estimate"] < share and state["discards_left"] > 0
                      and state["hands_left"] > 1)

        if discarding:
            keep = set(choice)
            choice = [i for i in range(1, len(state["hand"]) + 1)
                      if i not in keep][:5]
            if not choice:
                discarding = False
                choice = best["cards"]

        for index in choice:
            state = bridge.toggle(index)
        time.sleep(pace)

        if discarding:
            state = bridge.discard()
            print(f"    discarded {len(choice)} cards "
                  f"({state['discards_left']} left)")
        else:
            state = bridge.play()
            print(f"    played {best['hand']:<15} -> "
                  f"{state['chips']}/{state['blind_chips']} chips, "
                  f"{state['hands_left']} hands left")
        time.sleep(pace)
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default="ABCDEFGH")
    parser.add_argument("--deck", default=None,
                        help='e.g. "Yellow Deck"')
    parser.add_argument("--launch", action="store_true",
                        help="start the modded build first")
    parser.add_argument("--pace", type=float, default=0.4,
                        help="pause between actions, so it is watchable")
    parser.add_argument("--max-rounds", type=int, default=30)
    args = parser.parse_args()

    bridge = None
    if not args.launch:
        try:
            bridge = BalatroBridge(timeout=30).connect()
        except BridgeError:
            print("no game listening, starting one...")
    if bridge is None:
        try:
            _process, bridge = launch(wait=90)
        except BridgeError as error:
            print(f"{error}\n\nBalatro quits immediately without Steam "
                  "running -- check Steam is up.", file=sys.stderr)
            raise SystemExit(1)

    print(f"connected: {bridge.hello()}")
    bridge.command("start_run", args.seed,
                   *( [args.deck.replace(" ", "_")] if args.deck else [] ))
    state = bridge.wait_until(lambda s: s.get("blind_select_up"), timeout=40)
    print(f"run started on seed {args.seed}\n")

    for _ in range(args.max_rounds):
        state = bridge.state()
        if state.get("won"):
            print("\n*** RUN WON ***")
            break
        name = state["state_name"]

        if name == "GAME_OVER":
            print(f"\ngame over on ante {state['ante']}")
            break
        if name == "BLIND_SELECT":
            if not state.get("blind_select_up"):
                time.sleep(0.2)
                continue
            print(f"ante {state['ante']} {state['blind_on_deck']} blind  "
                  f"(${state['dollars']})")
            state = bridge.select_blind()
            print(f"  need {state['blind_chips']}, hand: {show_hand(state['hand'])}")
            time.sleep(args.pace)
            state = play_blind(bridge, state, args.pace)
        elif name == "ROUND_EVAL":
            state = bridge.cash_out()
            print(f"  cashed out -> ${state['dollars']}")
            time.sleep(args.pace)
        elif name == "SHOP":
            if not state.get("shop_settled"):
                time.sleep(0.2)
                continue
            shop = state.get("shop") or []
            affordable = [i for i in shop if i.get("buyable")]
            if affordable:
                pick = max(affordable, key=lambda i: i["cost"])
                bridge.buy(pick["area"], pick["index"])
                print(f"  bought something for ${pick['cost']}")
                time.sleep(args.pace)
                continue
            time.sleep(args.pace)
            state = bridge.leave_shop()
            print("  left the shop")
        elif state.get("in_pack"):
            state = bridge.skip_pack()
        else:
            time.sleep(0.2)

    print(f"\nfinished: ante {bridge.state().get('ante')}")
    bridge.close()


if __name__ == "__main__":
    main()
