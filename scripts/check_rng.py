"""Compare what the engine and the real game *offer*, not just what they do.

    python scripts/check_rng.py --seeds 8 --launch

A replay proves the rules match along the path a policy actually took. It says
nothing about the distribution the policy is optimising against -- the shop
offers, the boss, the tags. A policy trained against a sim whose shop rolls
differently learns trade-offs for a shop that does not exist, and the replay
would never show it.

Running the real game's own Lua ought to make that impossible. But the engine
skips work the real game does -- per-frame card updates, the autosave, overlay
menus -- and if any of that consumed the RNG, the streams would have diverged.
This checks, on identical seeds, without playing a single hand.
"""

from __future__ import annotations

import argparse
import random
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from balatro_bridge import BalatroBridge, BridgeError, launch     # noqa: E402
from balatro_headless.runtime import HeadlessBalatro              # noqa: E402


def offered(state) -> dict:
    """What the game is presenting, as plain comparable values."""
    def rows(items):
        out = []
        for i in range(len(items or [])):
            row = items[i] if isinstance(items, list) else items[i + 1]
            out.append((str(row["area"]), int(row["center"]), int(row["cost"])))
        return out

    return {
        "blind_on_deck": state.get("blind_on_deck"),
        "tags": tuple(state.get("tags") or ()),
        "shop": tuple(sorted(rows(state.get("shop")))),
        "deck_size": state.get("deck_size"),
        "dollars": state.get("dollars"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=8)
    parser.add_argument("--deck", default="Yellow Deck")
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args()

    rng = random.Random(1)
    seeds = ["".join(rng.choice(string.ascii_uppercase + string.digits)
                     for _ in range(8)) for _ in range(args.seeds)]

    engine = HeadlessBalatro().boot()
    bridge = None
    if args.launch:
        _process, bridge = launch(wait=90)
    else:
        bridge = BalatroBridge(timeout=30).connect(retries=3)

    deck = args.deck.replace(" ", "_")
    same = 0
    for seed in seeds:
        engine.execute(f'BOT.start_run({{"{seed}","{deck}"}})')
        engine.execute("api.pump(400)")
        a = offered(dict(engine.eval("BOT.state()")))

        bridge.command("start_run", seed, deck)
        bridge.wait_until(lambda s: s.get("in_run") and s.get("ready"),
                          timeout=60)
        b = offered(bridge.state())

        match = a == b
        same += match
        print(f"  {seed}  {'match' if match else 'DIFFER'}")
        if not match:
            for key in a:
                if a[key] != b[key]:
                    print(f"      {key}: engine {a[key]!r}")
                    print(f"      {' ' * len(key)}  game   {b[key]!r}")

    print(f"\n{same}/{len(seeds)} seeds agree on what is offered")
    bridge.close()


if __name__ == "__main__":
    main()
