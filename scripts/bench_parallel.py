"""Runs and decisions per second, driving the real engine in parallel.

The Lua engine is single-threaded and lives in-process, so scaling means
processes rather than threads. This is the measurement that decides whether
driving the game itself is fast enough for a given job, or whether the
simulator is worth the risk of being subtly wrong.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def worker(args):
    """One process: boot once, then play runs until told to stop."""
    n_runs, seed_offset, deck = args
    from balatro_headless.policy import GreedyPolicy
    from balatro_headless.run import HeadlessRun
    from balatro_headless.runtime import HeadlessBalatro

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from play_run import seed_for

    boot_started = time.perf_counter()
    game = HeadlessBalatro().boot()
    boot_time = time.perf_counter() - boot_started

    policy = GreedyPolicy()
    decisions = 0
    started = time.perf_counter()
    for i in range(n_runs):
        run = HeadlessRun(seed=seed_for(seed_offset + i), deck=deck, game=game)
        decisions += run.play_run(policy).decisions
    return n_runs, decisions, time.perf_counter() - started, boot_time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--procs", type=int, nargs="+", default=[1, 4, 8])
    parser.add_argument("--runs-per-proc", type=int, default=8)
    parser.add_argument("--deck", default=None)
    args = parser.parse_args()

    print(f"{'procs':>5} {'runs':>6} {'wall':>7} {'runs/s':>8} {'decisions/s':>12} "
          f"{'boot ms':>8}")
    for procs in args.procs:
        jobs = [(args.runs_per_proc, p * 1000, args.deck) for p in range(procs)]
        started = time.perf_counter()
        with mp.Pool(procs) as pool:
            results = pool.map(worker, jobs)
        wall = time.perf_counter() - started
        runs = sum(r[0] for r in results)
        decisions = sum(r[1] for r in results)
        boot_ms = 1000 * sum(r[3] for r in results) / len(results)
        print(f"{procs:>5} {runs:>6} {wall:>6.1f}s {runs / wall:>8.1f} "
              f"{decisions / wall:>12,.0f} {boot_ms:>8.0f}")


if __name__ == "__main__":
    mp.freeze_support()
    main()
