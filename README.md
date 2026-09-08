# jimbot-sim

Balatro as a Python library. Three things that agree with each other:

- **A simulator** of the game in pure Python — jokers, consumables, blinds,
  the shop, the RNG. No Lua, no game install, about 3,500 steps a second.
- **A headless driver** that runs the game's *own* Lua under LuaJIT, with the
  window and the animation taken out. Slower and authoritative.
- **A bridge** to the real, running game over a socket, so a program can play
  the version on your screen.

They serve one purpose between them: the simulator is only worth having if it
is right, and the other two are how you find out that it is not.

## The RNG is the point

Balatro's randomness is a seeded counter per draw, not one stream. Which
joker a shop offers depends on the seed, the ante, and how many jokers that
run has already been offered — so a simulator that draws the *right kind* of
random number in the wrong order diverges from the real game within an ante
and stays plausible while it does.

`src/balatro/rng.py` reproduces the game's own scheme, and
`tests/test_rng.py` pins it against values taken from the engine. That is
what makes a seed mean the same thing here as it does in Balatro.

## What checks what

Nothing here trusts the simulator on its own.

- `tests/test_differential.py` plays the same seed through the simulator and
  the headless engine and compares them step by step.
- `tests/test_recordings.py` replays real games recorded from the bridge and
  demands the simulator reach the same scores.
- `recordings/` holds those games — every action a person took, with the
  state they were looking at when they took it.
- `ops/run_differential.py` and `ops/explain_divergence.py` are for when they
  do disagree: the second one finds the first step where they part and says
  what differs.
- `ops/record_expectations.py` freezes what the engine scores, so the fast
  tests need not boot it.

Tests that boot LuaJIT are marked `slow` and deselected by default:

```
pytest                 # the simulator, seconds
pytest -m slow         # against the engine, minutes
```

## Playing the real game

`scripts/build_modded_game.py` builds a copy of your own Balatro install with
the bridge mod fused in. It reads the Steam install and never writes to it.
The build embeds the mod, so rebuild after changing `bot_api.lua`.

```
python scripts/build_modded_game.py
python scripts/play_visible.py --launch
```

`play_visible.py` drives the window with the engine's own `best_play`
heuristic. It is the bridge's demonstration and its end-to-end check, not a
trained agent.

The build needs the Steam client running even though it launches outside it.

## Every script

`scripts/` is what you run; `ops/` is what maintains the repository itself.
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
| `ops/gen_joker_data.py` | `src/balatro/joker_data.py` from the game's centres |
| `ops/gen_consumable_data.py` `ops/gen_pack_data.py` | consumables and booster pools |
| `ops/gen_boss_data.py` `ops/gen_tag_data.py` | blinds and tags |
| `ops/gen_deck_data.py` `ops/gen_voucher_data.py` | deck backs and vouchers |
| `ops/joker_catalogue.py` | dumps every joker with its text and config, for reading |

**Measure**

| | |
|---|---|
| `scripts/bench_parallel.py` | runs and decisions per second, engine driven in parallel |

## Where it came from

Extracted from a reinforcement learning project, which is why the state the
bridge reports is shaped the way it is: one dictionary describing a position,
identical whether it came from the simulator or the real game, so a program
written against one runs against the other unchanged. That project is the
consumer, not the point; everything here stands on its own.

Fidelity gaps are written down rather than left to be discovered — see the
notes in `src/balatro/game.py` and the tests named after the bugs they pin.
