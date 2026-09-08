# Scripts

Tooling that keeps the simulator honest. None of it is needed to *use* the
library — see the README for that — but all of it is needed to trust it.

`scripts/` is what you run by hand; `ops/` maintains the repository itself.
Each one's docstring says more than the line here does.

**Play a run**

| | |
|---|---|
| `scripts/headless_demo.py` | boots the real game headless and plays a hand — start here |
| `scripts/play_run.py` | complete runs headlessly, with a scripted policy; `--runs 20` |
| `scripts/play_visible.py` | drives the visible window with the engine's own best play |
| `scripts/build_modded_game.py` | builds the driveable copy of your Balatro install |
| `scripts/make_snapshots.py` | freezes a mid-run position so a run can resume from it |

**Prove the simulator right**

This is the part that matters. Nothing here trusts the simulator on its own.

| | |
|---|---|
| `scripts/record_replay.py` | records a human playing, then replays it and checks every step |
| `ops/sim_replay.py` | replays a recording through the simulator alone |
| `ops/run_differential.py` | same run in both engines, reports where they part |
| `ops/explain_divergence.py` | replays to the point of divergence and explains how it got there |
| `scripts/check_rng.py` | compares what each side *offers*, not just what it does |
| `scripts/audit_actions.py` | exercises every player action and asserts a real consequence |
| `ops/record_expectations.py` | freezes the engine's scores so the fast tests need not boot it |

**Repair a recording**

The recorder wraps the game's own buttons, so anything the game does to itself
is not captured — these fix that up rather than throwing the run away.

| | |
|---|---|
| `ops/clean_recording.py` | removes actions the game made itself |
| `ops/repair_recording.py` | puts back an action the recorder never saw |

**Regenerate the data tables**

The simulator's tables are generated from the real game, never typed in. Rerun
these after a Balatro update.

| | |
|---|---|
| `ops/gen_joker_data.py` | `src/jimbot_sim/joker_data.py` from the game's centres |
| `ops/gen_consumable_data.py` `ops/gen_pack_data.py` | consumables and booster pools |
| `ops/gen_boss_data.py` `ops/gen_tag_data.py` | blinds and tags |
| `ops/gen_deck_data.py` `ops/gen_voucher_data.py` | deck backs and vouchers |
| `ops/joker_catalogue.py` | dumps every joker with its text and config, for reading |

**Measure**

| | |
|---|---|
| `scripts/bench_parallel.py` | runs and decisions per second, engine driven in parallel |

