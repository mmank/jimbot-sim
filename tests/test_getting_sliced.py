"""A joker Madness or Ceremonial Dagger takes is gone only once the blind is set.

Both mark their victim and leave the removal to an event (card.lua:2511-2516,
2566-2575):

    joker_to_destroy.getting_sliced = true
    G.E_MANAGER:add_event(Event({func = function()
        ... joker_to_destroy:start_dissolve(...) end}))

and start_dissolve queues remove() further back still (card.lua:2170-2175).
The setting_blind pass (state_events.lua:335-337) runs to the end of the row
first, and for the rest of it the victim is still in G.jokers.cards:

* its own setting_blind effect does not run -- the whole branch is
  `elseif context.setting_blind and not self.getting_sliced` (card.lua:2491),
  and Burglar, Riff-raff, Cartomancer and Marble Joker also check
  `not (context.blueprint_card or self).getting_sliced`;
* Madness does not pick it again (card.lua:2507), and Ceremonial Dagger does
  not eat a neighbour already taken (card.lua:2566) -- it eats nothing, not
  the joker after;
* it keeps its slot. Riff-raff counts `#G.jokers.cards + G.GAME.joker_buffer`
  (card.lua:2529), and only the Dagger hands the slot back early, through
  `G.GAME.joker_buffer - 1` (card.lua:2569). Madness does not.

Riff-raff's jokers arrive in an event too, so nothing in the pass sees them:
a Dagger to Riff-raff's right has nothing to eat.

A Blueprint or a Brainstorm runs the copied joker's calculate_joker with
context.blueprint set (card.lua:2304-2330). That branch is skipped when the
copied joker is getting sliced, and the Burglar-style guards look at the
copier through blueprint_card; Madness, Ceremonial Dagger and Chicot carry
`not context.blueprint` and are never copied. The simulator ran no copy at
all. And all of calculate_joker sits under `if self.ability.set == "Joker"
and not self.debuff` (card.lua:2303), so a perished joker does nothing here.

The simulator removed the victim on the spot and walked a copy of the row: a
Burglar that Madness ate still gave its three hands, a Riff-raff it ate still
filled the slot, and a Dagger behind it ate the joker past the one Madness
took. The engine test below runs every row on the headless game.
"""

import pytest

from jimbot_sim import shop_pool
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.jokers import REGISTRY, JokerInstance

KEY = shop_pool.KEY_BY_JOKER_NAME
NAME = shop_pool.NAME_BY_JOKER_KEY


def _select(seed, keys, perished=()):
    game = GameState(seed=seed, deck="Red Deck")
    for i, key in enumerate(keys):
        joker = JokerInstance(REGISTRY[NAME[key]])
        game.gain_joker(joker)
        if i in perished:
            joker.perishable, joker.perish_tally = True, 0
            joker.debuffed = True
    game.step(Action(ActionType.SELECT_BLIND))
    return game


def _row(game):
    return [KEY[j.name] for j in game.jokers]


def _counters(game):
    return game.hands_left, game.discards_left


ALLOWANCE = _counters(_select("SLICE1", []))
FULL = ["j_joker", "j_greedy_joker", "j_lusty_joker"]


def test_a_joker_madness_takes_does_not_fire():
    game = _select("SLICE1", ["j_madness", "j_burglar"])
    assert _row(game) == ["j_madness"]
    assert _counters(game) == ALLOWANCE, "the eaten Burglar still gave hands"


def test_a_joker_madness_takes_keeps_its_slot_for_the_pass():
    game = _select("SLICE1", ["j_madness", "j_riff_raff"] + FULL)
    assert len(game.jokers) == 4, (
        "Riff-raff filled the slot of a joker that was still in the row")


def test_the_dagger_hands_its_slot_back_through_the_buffer():
    game = _select("SLICE1", ["j_ceremonial", "j_joker", "j_riff_raff",
                              "j_greedy_joker", "j_lusty_joker"])
    assert _row(game)[:4] == ["j_ceremonial", "j_riff_raff",
                              "j_greedy_joker", "j_lusty_joker"]
    assert len(game.jokers) == 5


def test_a_dagger_eats_nothing_behind_a_neighbour_madness_took():
    game = _select("SLICE2", ["j_madness", "j_ceremonial", "j_joker",
                              "j_greedy_joker"])
    assert _row(game) == ["j_madness", "j_ceremonial", "j_greedy_joker"]
    assert game.jokers[1].counter == 0


def test_riff_raffs_jokers_are_not_in_the_row_during_the_pass():
    game = _select("SLICE1", ["j_riff_raff", "j_ceremonial"])
    assert _row(game)[:2] == ["j_riff_raff", "j_ceremonial"]
    assert len(game.jokers) == 4
    assert game.jokers[1].counter == 0, "the Dagger ate a joker made after it"


def test_blueprint_copies_a_blind_select_effect():
    game = _select("SLICE1", ["j_blueprint", "j_burglar"])
    assert _counters(game) == (ALLOWANCE[0] + 6, 0)


def test_a_copy_of_a_joker_getting_sliced_does_not_fire():
    game = _select("SLICE2", ["j_madness", "j_blueprint", "j_burglar"])
    assert _row(game) == ["j_madness", "j_blueprint"]
    assert _counters(game) == ALLOWANCE


def test_a_copier_getting_sliced_copies_nothing():
    game = _select("SLICE1", ["j_madness", "j_blueprint", "j_burglar"])
    assert _row(game) == ["j_madness", "j_burglar"]
    assert _counters(game) == (ALLOWANCE[0] + 3, 0)


def test_a_perished_joker_does_nothing_when_the_blind_is_selected():
    game = _select("SLICE1", ["j_burglar"], perished=(0,))
    assert _counters(game) == ALLOWANCE


# -- against the engine ------------------------------------------------------

CASES = [
    ("SLICE1", ["j_madness", "j_burglar"], ()),
    ("SLICE1", ["j_madness", "j_riff_raff"] + FULL, ()),
    ("SLICE1", ["j_ceremonial", "j_joker", "j_riff_raff", "j_greedy_joker",
                "j_lusty_joker"], ()),
    ("SLICE2", ["j_madness", "j_ceremonial", "j_joker", "j_greedy_joker"], ()),
    ("SLICE1", ["j_riff_raff", "j_ceremonial"], ()),
    ("SLICE1", ["j_blueprint", "j_burglar"], ()),
    ("SLICE2", ["j_madness", "j_blueprint", "j_burglar"], ()),
    ("SLICE1", ["j_madness", "j_blueprint", "j_burglar"], ()),
    ("SLICE1", ["j_burglar"], (0,)),
]

_ENGINE_STATE = (
    '(function() local t = {} for _, c in ipairs(G.jokers.cards) do '
    't[#t+1] = c.config.center.key end '
    'return table.concat(t, " ").."|"..G.GAME.current_round.hands_left'
    '.."|"..G.GAME.current_round.discards_left end)()')


@pytest.mark.slow
@pytest.mark.parametrize("seed,keys,perished", CASES,
                         ids=["-".join(k[2:6] for k in c[1]) for c in CASES])
def test_the_engine_selects_the_same_blind(seed, keys, perished):
    pytest.importorskip("lupa")
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
    from jimbot_sim.headless.scenario import Scenario

    if not engine_available():
        pytest.skip("no Balatro engine: need vendor/balatro_src")
    engine = HeadlessBalatro().boot()
    engine.execute(f'BOT.start_run({{"{seed}","Red_Deck",1}})')
    engine.execute("api.pump(300)")
    Scenario(engine).jokers(keys)
    for i in perished:
        engine.execute(f"local c = G.jokers.cards[{i + 1}]; "
                       "c.ability.perishable = true; "
                       "c.ability.perish_tally = 0; c:set_debuff(true)")
    engine.execute("api.select_blind(); api.pump(120)")

    game = _select(seed, keys, perished)
    sim = "%s|%d|%d" % (" ".join(_row(game)), *_counters(game))
    assert sim == engine.eval(_ENGINE_STATE)
