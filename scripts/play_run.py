"""Play complete runs of the real game headlessly and report how far they get."""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimbot_sim.headless.policy import GreedyPolicy  # noqa: E402
from jimbot_sim.headless.run import HeadlessRun  # noqa: E402
from jimbot_sim.headless.runtime import HeadlessBalatro  # noqa: E402


def seed_for(i: int) -> str:
    """Balatro seeds are 8 characters from an unambiguous alphabet."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
    out, n = [], i + 1
    for _ in range(8):
        out.append(alphabet[n % len(alphabet)])
        n = n // len(alphabet) + 7
    return "".join(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--seed", type=str, default=None,
                        help="play one specific Balatro seed")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--reserve", type=int, default=5)
    parser.add_argument("--no-packs", action="store_true")
    args = parser.parse_args()

    # Booting is ~85ms; one runtime is reused across runs.
    game = HeadlessBalatro().boot()
    policy = GreedyPolicy(reserve=args.reserve, buy_packs=not args.no_packs)

    seeds = [args.seed] if args.seed else [seed_for(i) for i in range(args.runs)]
    antes: Counter[int] = Counter()
    reached: list[int] = []
    wins = 0
    started = time.perf_counter()

    for seed in seeds:
        run = HeadlessRun(seed=seed, game=game, verbose=args.verbose)
        result = run.play_run(policy)
        antes[result.ante] += 1
        reached.append(result.ante)
        wins += result.won
        if args.verbose or args.seed:
            print(result)

    elapsed = time.perf_counter() - started
    print(f"\n{len(seeds)} runs  mean ante {statistics.mean(reached):.2f}  "
          f"median {statistics.median(reached):.0f}  best {max(reached)}  "
          f"wins {wins} ({wins / len(seeds):.0%})  "
          f"[{elapsed:.1f}s, {elapsed / len(seeds):.2f}s/run]")
    for ante in sorted(antes):
        bar = "#" * round(40 * antes[ante] / len(seeds))
        print(f"  ante {ante:2d}: {antes[ante]:4d} {bar}")


if __name__ == "__main__":
    main()
