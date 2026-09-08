# jimbot-sim

Balatro you can drive from Python. Seed a run, ask what the legal moves are,
take one, repeat — and it plays out as it would in the game.

```python
from balatro import GameState, ActionType

game = GameState(seed="SEED0000", deck="Red Deck", stake=1)
while not game.is_over:
    actions = game.legal_actions()
    plays = [a for a in actions if a.type is ActionType.PLAY]
    game.step(max(plays, key=lambda a: game.preview_score(a.cards))
              if plays else actions[0])
print(game.ante, game.money)
```

That is a whole bot. It beats the small blind and the big blind of ante one
and then loses to the boss — because `actions[0]` in a shop is "leave", so it
never buys a joker. Making it better is the interesting part, and the rest of
this is about giving you what you need to.

```bash
pip install -e .
```

`lupa` is the only dependency, and only the Lua-backed halves need it. The
simulator is pure Python.

## The loop

Four things, and no hidden state between them.

| | |
|---|---|
| `GameState(seed=..., deck=..., stake=...)` | a run. The seed is Balatro's own — the same string is the same run in the real game |
| `game.legal_actions()` | every legal move right now, as `Action` objects |
| `game.step(action)` | takes one, and advances as far as that goes |
| `game.is_over` | the run ended, won or lost |

`legal_actions()` is the entire interface to the rules. It knows that Death
takes exactly two cards, that an eternal joker cannot be sold, that a boss
blind cannot be skipped, that a full joker row cannot buy a sixth. You never
encode a rule yourself, and you cannot choose a move the game would refuse.

It enumerates card subsets, so during a hand it returns around 400 actions —
every play and every discard available. That is deliberate: choosing among
them is the bot's job, and a smaller interface would be hiding most of the
game.

`preview_score(indices)` scores a candidate play without advancing anything.
Joker counters, card enhancements and the run's random stream are all restored
afterwards, so you can weigh every option before committing to one.

## Reading a position

Plain attributes. There is no observation format to learn.

```python
game.phase           # BLIND_SELECT, PLAYING, ROUND_EVAL, SHOP, GAME_OVER ...
game.ante            # 1..8, and past it
game.money           # dollars
game.hands_left      # and game.discards_left
game.chips_scored    # against game.blind.target
game.blind           # kind, target, reward, and the boss effect if any

game.hand            # what you are holding, as Card objects
game.full_deck       # all 52, or however many it is by now
game.jokers          # JokerInstance: name, edition, stickers, counter
game.consumables     # what is in the consumable slots
game.shop            # while you are in one
game.pack_options    # while a booster is open

game.hand_levels     # per poker hand: level, chips, mult, times played
game.vouchers        # redeemed
game.tags            # held
game.joker_slots     # and consumable_slots, hand_size
```

A joker's `counter` is the number that decides late runs — Ride the Bus at
+2 Mult and at +60 are the same joker and very different cards. The stake's
stickers are there too: `eternal`, `perishable` with its `perish_tally`,
`rental`.

## Actions

```python
Action(ActionType.PLAY, cards=(0, 2, 3))              # indices into game.hand
Action(ActionType.DISCARD, cards=(1, 4))
Action(ActionType.SELECT_BLIND)                       # or SKIP_BLIND
Action(ActionType.BUY, index=0)                       # into the shop
Action(ActionType.BUY_AND_USE, index=1)               # the shop's second button
Action(ActionType.BUY_PACK, index=2)
Action(ActionType.PICK_PACK, index=0, cards=(1, 3))   # a Tarot needs targets
Action(ActionType.USE_CONSUMABLE, index=0, cards=(1, 3))
Action(ActionType.SELL_JOKER, index=2)
Action(ActionType.REROLL)
Action(ActionType.LEAVE_SHOP)
Action(ActionType.CASH_OUT)
```

You rarely need to build one. `legal_actions()` returns them already filled
in, including which target combinations each consumable will accept.

## What the seed means

Balatro's randomness is a seeded counter per draw, not one stream. Which joker
a shop offers depends on the seed, the ante, and how many jokers that run has
already been offered — so a simulator that draws the right *kind* of random
number in the wrong order diverges within an ante, and looks plausible while
it does.

That scheme is reproduced here and pinned against the real game in
`tests/test_rng.py`. It is why a seed means the same thing in both, and why
you can develop against the simulator and expect the real game to agree.

Two runs from one seed are identical. Nothing here reads a clock or a global
random source.

## When you want the real thing instead

The simulator is fast — no game install, no Lua, and a few thousand steps a
second, so a whole run costs a fraction of one. Two slower backends exist for
when being *exactly* right matters more:

- **`balatro_headless`** runs Balatro's own Lua under LuaJIT with the window
  and the animation removed. It is the game rather than a model of it, and it
  is what the simulator is tested against.
- **`balatro_bridge`** talks to the actual running game over a socket, so a
  bot can play the copy on your screen.

All three answer the same questions, so a bot written against one runs against
the others. `scripts/play_visible.py` drives the real window — build a
driveable copy of your own install first with `scripts/build_modded_game.py`,
and note it needs the Steam client running even though it launches outside it.

## Is the simulator actually right?

Mostly, and where it is not, it is written down rather than left to be found.

- `tests/test_differential.py` plays one seed through the simulator and the
  real engine side by side, comparing them step by step.
- `tests/test_recordings.py` replays games recorded from a person playing and
  demands the same scores.
- `recordings/` holds those games: every action taken, with the state the
  player was looking at when they took it.
- 150 jokers, 22 tarots, 12 planets, 18 spectrals, 32 vouchers, 28 bosses and
  24 tags are implemented. `tests/test_registry_honesty.py` names the ones
  that are registered but hollow, so that list cannot quietly grow.

The largest known gap is the four bosses that hide cards from the player. They
carry their chip requirement and no mechanic, and
`tests/test_finisher_bosses.py` pins them as the only four that are blank — so
nothing here has ever had to play under uncertainty.

```bash
pytest              # the simulator, seconds
pytest -m slow      # against the real engine, minutes
```

The engine tests need Balatro's Lua extracted from your own install into
`vendor/balatro_src`, which is never committed. Without it they skip rather
than fail.

## Everything else

The rest is tooling that keeps the above honest, listed in
[SCRIPTS.md](SCRIPTS.md): replaying and repairing recordings, finding where
two backends diverge, and regenerating the simulator's data tables from the
game after an update.

## Where it came from

Extracted from a reinforcement learning project, which is why the bridge
reports a position as a single dictionary that is identical whether it came
from the simulator or the real game. That project is a consumer of this, not
the point of it.
