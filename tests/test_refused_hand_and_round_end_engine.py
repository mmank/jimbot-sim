"""The after-hand pass on a refused hand, and end_of_round resets, measured.

The simulator side is covered at more length by test_refused_hand_after_pass
and test_end_of_round_resets; these pin the engine to the same numbers.

- A refused hand skips the block at state_events.lua:614-996 (the `before`
  pass, scoring, destroying_card) and still gets the `after` pass at
  1068-1075: Ice Cream (card.lua:3571) and Seltzer (3601) count it, Green
  Joker (3563) and Ride the Bus (3525) do not. DNA (3501) copies nothing.
- end_of_round (state_events.lua:101) resets Hit the Road (card.lua:3011)
  and rolls Cavendish's 1 in 1000 on 'cavendish' (3019-3020).
"""

import pytest

pytest.importorskip("lupa")

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind  # noqa: E402
from jimbot_sim.cards import Card, Rank, Suit  # noqa: E402
from jimbot_sim.game import Action, ActionType, GameState  # noqa: E402
from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available  # noqa: E402
from jimbot_sim.headless.scenario import Scenario  # noqa: E402
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance  # noqa: E402

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not engine_available(),
                       reason="no Balatro engine: need vendor/balatro_src"),
]

MOUTH = next(b for b in BOSSES if b.name == "The Mouth")
PAIR_HAND = "H_9 D_9 H_5 C_4 C_2 S_3 D_8 H_T"
TRIPS_HAND = "H_7 C_7 D_7 D_5 S_4 S_3 D_8 H_T"


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


def _read(engine, expr):
    return float(engine.eval("(function() return %s end)()" % expr))


def _win_the_round(engine):
    engine.execute("G.GAME.blind.chips = 1; api.highlight({1}); "
                   "G.FUNCS.play_cards_from_highlighted()")
    engine.execute("api.pump_until(function() "
                   "return G.STATE == G.STATES.ROUND_EVAL end); api.pump(600)")


def _sim_cards(codes, picks):
    ranks = {"7": Rank.SEVEN, "9": Rank.NINE}
    suits = {"H": Suit.HEARTS, "D": Suit.DIAMONDS, "C": Suit.CLUBS,
             "S": Suit.SPADES}
    out = []
    for i in picks:
        suit, rank = codes.split()[i - 1].split("_")
        out.append(Card(ranks[rank], suits[suit]))
    return out


def test_the_mouth_refuses_a_hand(engine):
    keys = "j_ice_cream j_selzer j_green_joker j_ride_the_bus"  # sic, game.lua:475
    fields = ("extra.chips", "extra", "mult", "mult")
    scene = Scenario(engine).start().boss("bl_mouth").jokers(keys)
    scene.hand(PAIR_HAND)
    assert scene.play([1, 2]) > 0
    scene.hand(TRIPS_HAND)
    assert scene.play([1, 2, 3]) == 0
    seen = [_read(engine, "G.jokers.cards[%d].ability.%s" % (i, f))
            for i, f in enumerate(fields, start=1)]
    assert seen == [90, 8, 1, 1]

    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in ("Ice Cream", "Seltzer", "Green Joker", "Ride the Bus"):
        game.gain_joker(JokerInstance(JOKERS[name]))
    game.ante_boss = ""
    game.blind = make_blind(BlindKind.BOSS, game.ante, MOUTH)
    game._start_round()
    game.blind.target = 10 ** 12
    for codes, picks in ((PAIR_HAND, (1, 2)), (TRIPS_HAND, (1, 2, 3))):
        game.hand[:] = _sim_cards(codes, picks)
        game.step(Action(ActionType.PLAY, cards=tuple(range(len(picks)))))
    assert [j.counter for j in game.jokers] == seen


def test_dna_under_the_psychic(engine):
    """One card: refused by The Psychic, allowed on the small blind."""
    decks = []
    for boss in ("bl_psychic", None):
        scene = Scenario(engine).start()
        if boss:
            scene.boss(boss)
        scene.jokers("j_dna").hand("S_K H_5 C_4 C_2 S_3 D_8 H_T D_9")
        scene.play([1])
        decks.append(int(_read(engine, "#G.playing_cards")))
    assert decks == [52, 53]


def test_hit_the_road_resets(engine):
    Scenario(engine).start().jokers("j_hit_the_road")
    engine.execute("G.jokers.cards[1].ability.x_mult = 2.5; api.pump(20)")
    _win_the_round(engine)
    assert _read(engine, "G.jokers.cards[1].ability.x_mult") == 1

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Hit the Road"]))
    game._start_round()
    game.jokers[0].counter = 2.5
    game.blind = make_blind(BlindKind.SMALL, game.ante)
    game._beat_blind()
    assert game.jokers[0].counter == 1


def test_cavendish_rolls(engine):
    Scenario(engine).start().jokers("j_cavendish")
    assert engine.eval("G.GAME.pseudorandom.cavendish == nil and 1 or 0") == 1
    _win_the_round(engine)
    state = _read(engine, "G.GAME.pseudorandom.cavendish")

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Cavendish"]))
    game._start_round()
    game.blind = make_blind(BlindKind.SMALL, game.ante)
    game._beat_blind()
    assert game.rng.pools["cavendish"] == pytest.approx(state, abs=1e-12)
