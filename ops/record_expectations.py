"""Record what the engine scores, so the everyday tests need not run it.

The joker sweep was two minutes of the suite, and almost all of it was
BOT.start_run: 35ms a time, once per joker per position, three and a half
thousand times. Pumping frames is free by comparison, and restoring a snapshot
only saves a quarter.

But the engine's answer never changes. For a given joker in a given position
it is the same number today as yesterday, so it can be written down once and
the everyday test can compare the simulator against that -- no Lua, no run, no
thirty-five milliseconds. The engine stays the source of truth; it is simply
consulted on demand rather than on every commit.

    python ops/record_expectations.py            # refresh the file

The file carries a fingerprint of what produced it -- the joker list and the
positions -- so adding a joker or a case invalidates it rather than silently
checking the old set. tests/test_differential.py refuses to run against a
stale one, and a slow test re-derives it from the engine to catch the case
where the engine itself changed underneath.
"""

import json
import pathlib
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

OUT = pathlib.Path("tests/data/joker_expectations.json")


def main() -> None:
    import test_differential as td

    engine = td.HeadlessBalatro().boot()
    keys = td._joker_keys(engine)
    print("recording %d jokers x %d positions" % (len(td.REGISTRY),
                                                  len(td.CASES)))

    out = {"fingerprint": td.inputs_fingerprint(), "expectations": {}}

    # What each position scores with no joker at all. The inertness check
    # compares against these, so they have to be written down alongside.
    baselines = {}
    for case in td.CASES:
        outcome, _state = td._engine_score(engine, case, None)
        baselines[case.name] = list(outcome)
    out["expectations"]["__baselines__"] = baselines

    for n, name in enumerate(sorted(td.REGISTRY), start=1):
        if name in td.NEEDS_SETUP or name in td.COVERED_ELSEWHERE \
                or name in td.NOT_A_SCORING_EFFECT:
            continue
        key = keys[name]
        rows = {}
        for case in td.CASES:
            outcome, state = td._engine_score(engine, case, key)
            rows[case.name] = {"outcome": list(outcome), "state": state}
        out["expectations"][name] = rows
        if n % 25 == 0:
            print("  %d of %d" % (n, len(td.REGISTRY)))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out), encoding="utf-8")
    size = OUT.stat().st_size / 1e6
    print("wrote %s (%d jokers, %.1f MB)"
          % (OUT, len(out["expectations"]), size))


if __name__ == "__main__":
    main()
