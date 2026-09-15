"""The end_of_round joker branches the simulator was missing.

end_round asks every joker `{end_of_round = true}` (state_events.lua:99-109)
the moment a round is over. card.lua:2874-3066 is the whole list; each
branch below is `not context.blueprint` (2888) and a debuffed joker answers
nothing (2291-2292).

Hit the Road resets (card.lua:3011-3017):

    if self.ability.name == 'Hit the Road' and self.ability.x_mult > 1 then
        self.ability.x_mult = 1

The simulator never reset it, so every Jack discarded stayed in the X for
the rest of the run. Seed VIBC905W, Anaglyph Deck, stake 3 on the headless
engine at decision 45: the hand that won the round left the game's Hit the
Road on X1 and the simulator's on X1.5.

Cavendish rolls its 1 in 1000 there as well, on its own stream
(card.lua:3019-3020, odds from game.lua:432):

    pseudorandom(self.ability.name == 'Cavendish' and 'cavendish'
                 or 'gros_michel') < G.GAME.probabilities.normal/odds

and, unlike Gros Michel, sets no pool flag when it goes (3037).
"""

from jimbot_sim.blinds import BlindKind, make_blind
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance


def _run(*names):
    game = GameState(seed="VIBC905W", deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    return game


def _end(game, kind=BlindKind.SMALL):
    game.blind = make_blind(kind, game.ante)
    game._beat_blind()


def test_hit_the_road_resets_when_the_round_ends():
    game = _run("Hit the Road")
    game.jokers[0].counter = 2.5
    _end(game)
    assert game.jokers[0].counter == 1.0


def test_hit_the_road_resets_on_any_blind():
    """No `blind.boss` condition, unlike Campfire (card.lua:2889)."""
    for kind in (BlindKind.SMALL, BlindKind.BIG, BlindKind.BOSS):
        game = _run("Hit the Road")
        game.jokers[0].counter = 1.5
        _end(game, kind)
        assert game.jokers[0].counter == 1.0


def test_a_debuffed_hit_the_road_keeps_its_x():
    game = _run("Hit the Road")
    game.jokers[0].counter = 2.0
    game.set_joker_debuff(game.jokers[0], True)
    _end(game)
    assert game.jokers[0].counter == 2.0


def test_cavendish_rolls_at_the_end_of_the_round():
    game = _run("Cavendish")
    assert "cavendish" not in game.rng.pools
    _end(game)
    assert "cavendish" in game.rng.pools
    assert [j.name for j in game.jokers] == ["Cavendish"]


def test_a_debuffed_cavendish_does_not_roll():
    game = _run("Cavendish")
    game.set_joker_debuff(game.jokers[0], True)
    _end(game)
    assert "cavendish" not in game.rng.pools


def test_cavendish_goes_extinct_without_a_pool_flag():
    game = _run("Cavendish")
    rolled = []

    def chance(key, numerator, denominator):
        rolled.append((key, numerator, denominator))
        return key == "cavendish"

    game.rng.chance = chance
    _end(game)
    assert ("cavendish", 1, 1000) in rolled
    assert game.jokers == []
    assert "gros_michel_extinct" not in game.pool_flags
