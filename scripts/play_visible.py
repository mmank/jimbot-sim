"""Play the real, visible Balatro with the *engine's own* best play.

Not the trained bot. This asks bot_api for `best_play` -- the scripted
heuristic that picks the highest-scoring hand it can see -- and drives the
window with it. It is the bridge's demonstration and its end-to-end check:
that the mod loads, that the socket answers, that an action taken here has
the consequence it should in a game that animates.

It drives the game through `jimbot_sim.run.EngineRun`, the interface every
driver uses, so what it checks is the path a policy's actions take too.

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
from jimbot_sim.compare import hand as hand_names  # noqa: E402
from jimbot_sim.game import Action, ActionType  # noqa: E402
from jimbot_sim.run import EngineRun  # noqa: E402

# What buys from each of the shop's areas.
BUYS = {"shop_jokers": ActionType.BUY, "shop_vouchers": ActionType.BUY_VOUCHER,
        "shop_booster": ActionType.BUY_PACK}


def play_blind(run: EngineRun, state: dict, pace: float) -> dict:
    """Play one blind: best hand each time, discarding when it is weak."""
    while state["state_name"] == "SELECTING_HAND":
        best = run.bridge.command("best_play")
        choice = list(best["cards"] or [])
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
                choice = list(best["cards"])

        cards = tuple(i - 1 for i in choice)
        run.step(Action(ActionType.DISCARD if discarding else ActionType.PLAY,
                        cards=cards))
        state = run.state()
        if discarding:
            print(f"    discarded {len(choice)} cards "
                  f"({state['discards_left']} left)")
        else:
            print(f"    played {best['hand']:<15} -> "
                  f"{state['chips']}/{state['blind_chips']} chips, "
                  f"{state['hands_left']} hands left")
        time.sleep(pace)
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default="ABCDEFGH")
    parser.add_argument("--deck", default="Red Deck",
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
    run = EngineRun(bridge)
    run.start(args.seed, args.deck)
    print(f"run started on seed {args.seed}\n")

    for _ in range(args.max_rounds):
        state = run.state()
        if state.get("won"):
            print("\n*** RUN WON ***")
            break
        name = state["state_name"]

        if name == "GAME_OVER":
            print(f"\ngame over on ante {state['ante']}")
            break
        if name == "BLIND_SELECT":
            print(f"ante {state['ante']} {state.get('blind_on_deck')} blind  "
                  f"(${state['dollars']})")
            run.step(Action(ActionType.SELECT_BLIND))
            state = run.state()
            print(f"  need {state['blind_chips']}, "
                  f"hand: {' '.join(hand_names(state))}")
            time.sleep(args.pace)
            play_blind(run, state, args.pace)
        elif name == "ROUND_EVAL":
            run.step(Action(ActionType.CASH_OUT))
            print(f"  cashed out -> ${run.state()['dollars']}")
            time.sleep(args.pace)
        elif name == "SHOP":
            shop = [row for row in state.get("shop") or []
                    if row.get("buyable") and row.get("area") in BUYS]
            if shop:
                pick = max(shop, key=lambda row: row["cost"])
                run.step(Action(BUYS[pick["area"]],
                                index=int(pick["index"]) - 1))
                print(f"  bought something for ${pick['cost']}")
                time.sleep(args.pace)
                continue
            time.sleep(args.pace)
            run.step(Action(ActionType.LEAVE_SHOP))
            print("  left the shop")
        elif state.get("in_pack"):
            run.step(Action(ActionType.SKIP_PACK))
        else:
            time.sleep(0.2)

    print(f"\nfinished: ante {bridge.state().get('ante')}")
    bridge.close()


if __name__ == "__main__":
    main()
