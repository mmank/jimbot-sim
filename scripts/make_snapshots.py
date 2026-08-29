"""Play to a position and freeze it, so training does not only see ante one.

    python scripts/make_snapshots.py --ante 3 --count 20

Every episode starting from a fresh run means the late game is reached only by
a policy that already handles the early one, so it is learned last and least.
These are positions to start from instead: a scripted policy plays several
antes in, and the run is frozen at a blind select.

Blind select specifically. The shop's card areas are built by the UI, which the
engine never builds, so a run frozen in a shop comes back without its shelves.
"""

from __future__ import annotations

import argparse
import random
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from balatro_headless.driving import advance                      # noqa: E402
from balatro_headless.policy import GreedyPolicy                  # noqa: E402
from balatro_headless.run import HeadlessRun                      # noqa: E402
from balatro_headless.runtime import HeadlessBalatro              # noqa: E402


def play_to(run: HeadlessRun, policy, ante: int, budget: int = 4000):
    """Advance until the target ante's blind select, or the run ends."""
    from balatro_headless.run import BLIND_SELECT, PACK, SELECTING_HAND, SHOP

    settled = 0
    for _ in range(budget):
        state = run.snapshot()
        if state["won"] or state["state_name"] in ("GAME_OVER", "MENU"):
            return None
        if (state["state_name"] == "BLIND_SELECT"
                and state["ante"] >= ante):
            return state
        if advance(state, run._driver, settled):
            settled += 1
            continue
        settled = 0
        name = state["state_name"]
        if name in ("TAROT_PACK", "PLANET_PACK", "SPECTRAL_PACK",
                    "STANDARD_PACK", "BUFFOON_PACK"):
            run._apply(PACK, policy.pack(run, state))
        elif name == "BLIND_SELECT":
            run._apply(BLIND_SELECT, policy.blind(run, state))
        elif name == "SELECTING_HAND":
            run._apply(SELECTING_HAND, policy.hand(run, state))
        elif name == "SHOP":
            run._apply(SHOP, policy.shop(run, state))
        else:
            run.pump(60)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ante", type=int, default=3,
                        help="freeze once this ante's blind select is reached")
    parser.add_argument("--count", type=int, default=20,
                        help="how many positions to collect")
    parser.add_argument("--deck", default="Yellow Deck")
    parser.add_argument("--stake", type=int, default=1)
    parser.add_argument("--out", type=Path, default=Path("snapshots"))
    parser.add_argument("--seed", type=int, default=0,
                        help="seeds the choice of Balatro seeds, so a set of "
                             "positions can be reproduced")
    parser.add_argument("--money", type=int, default=None,
                        help="top the run up in each shop, to reach deeper "
                             "antes more often than a greedy policy manages")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    def fresh_seed() -> str:
        # Without this every run gets the same seed and the positions are all
        # the same position, which is worse than useless as a starting set.
        alphabet = string.ascii_uppercase + string.digits
        return "".join(rng.choice(alphabet) for _ in range(8))

    game = HeadlessBalatro().boot()
    policy = GreedyPolicy()
    kept = attempts = 0

    while kept < args.count and attempts < args.count * 12:
        attempts += 1
        run = HeadlessRun(seed=fresh_seed(), deck=args.deck, stake=args.stake,
                          game=game, money_per_shop=args.money or 0)
        run.start()
        state = play_to(run, policy, args.ante)
        if state is None:
            continue
        packed = run.freeze()
        name = args.out / f"ante{state['ante']}_{run.seed}.txt"
        name.write_text(packed, encoding="utf-8")
        kept += 1
        print(f"  {name}  ante {state['ante']} round {state['round']} "
              f"${state['dollars']} jokers {state['joker_count']} "
              f"({len(packed) // 1024}KB)", flush=True)

    print(f"\n{kept} positions from {attempts} runs -> {args.out}")
    if kept < args.count:
        print("fewer than asked for: the scripted policy rarely reaches that "
              "ante. Try a lower --ante, or --money to fund it.")


if __name__ == "__main__":
    main()
