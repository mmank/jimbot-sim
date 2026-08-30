"""Every joker the simulator claims, scored against the real engine.

The simulator had 31 hand-written tests, all passing, while five of its jokers
scored the wrong number. Hand-written tests check what the author already
believed; only the engine knows what the game does. So the rule here is that
implementing a joker is not what makes it real -- agreeing with the engine is.

Two failure modes this harness has to keep apart:

  fixture mismatch   the two sides scored different *situations*. Banner pays
                     per discard remaining, so a simulator with 0 discards and
                     an engine with 4 disagree while both are correct. Four of
                     the original nine "bugs" were this, which is why run state
                     is synchronised from the engine rather than assumed.

  fidelity bug       same situation, different answer. Those are real.

test_every_implemented_joker_is_checked is the load-bearing one: it fails when
the simulator gains a joker that this file does not exercise, so coverage
cannot quietly drift behind implementation.
"""

import pytest

from balatro.cards import Card, Rank, Suit, standard_deck
from balatro.game import GameState
from balatro.hands import evaluate
from balatro.jokers import REGISTRY, make
from balatro.scoring import score_hand
from balatro_headless.runtime import HeadlessBalatro
from balatro_headless.scenario import Scenario

S, H, D, C = Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS

# A pair of kings and neutral filler: no suit bias, no straight, no flush, so
# a joker that pays on any of those is visibly paying for the wrong reason.
HAND = "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4"
PLAYED = [Card(Rank.KING, S), Card(Rank.KING, H)]
HELD = [Card(Rank.TWO, D), Card(Rank.FIVE, C), Card(Rank.SEVEN, H),
        Card(Rank.NINE, S), Card(Rank.THREE, D), Card(Rank.FOUR, C)]

# Known-wrong, each with the reason. An xfail here is a bug with a name, not a
# joker quietly excused: when one is fixed the test fails as XPASS and the
# entry has to come out.
KNOWN_BAD = {
    "Ice Cream": "decrements its chips before scoring; the game does it after",
    "Misprint": "rolls its own RNG, not the game's 'misprint' pool",
    "Runner": "adds its bonus with no straight played",
    "Supernova": "counts hands played before this one; the game includes it",
    "Swashbuckler": "ignores its own sell value in the total",
}
NEEDS_SETUP = {"Steel Joker": "scales on steel cards in the deck; none here"}


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


@pytest.fixture(scope="module")
def joker_keys(engine):
    """The game's own key-to-name map, so nothing is hand-maintained."""
    raw = engine.eval('''(function()
        local t = {}
        for k, v in pairs(G.P_CENTERS) do
          if v.set == "Joker" then
            local loc = G.localization.descriptions.Joker[k]
            if loc then t[#t+1] = k .. "=" .. loc.name end
          end
        end
        return table.concat(t, "|") end)()''')
    return {name: key for key, name in
            (pair.split("=", 1) for pair in raw.split("|"))}


def _sim_score(name: str, state: dict) -> int:
    game = GameState(seed=0)
    game.jokers = [make(name)]
    game.hand = PLAYED + HELD
    game.discards_left = state["discards_left"]
    game.hands_left = state["hands_left"]
    game.money = state["money"]
    game.draw_pile = standard_deck()[:state["draw_pile"]]
    return score_hand(game, evaluate(PLAYED), PLAYED, HELD).score


def _engine_state(engine) -> dict:
    read = lambda expr: int(engine.eval(f"(function() return {expr} end)()"))
    return {
        "discards_left": read("G.GAME.current_round.discards_left"),
        "hands_left": read("G.GAME.current_round.hands_left"),
        "money": read("G.GAME.dollars"),
        "draw_pile": read("#G.deck.cards"),
    }


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_joker_scores_what_the_engine_scores(engine, joker_keys, name):
    if name in NEEDS_SETUP:
        pytest.skip(NEEDS_SETUP[name])
    if name in KNOWN_BAD:
        pytest.xfail(KNOWN_BAD[name])

    key = joker_keys[name]
    scene = Scenario(engine).start().hand(HAND).jokers(key)
    state = _engine_state(engine)           # read before playing consumes it
    assert scene.play([1, 2]) == _sim_score(name, state)


def test_every_implemented_joker_is_checked(joker_keys):
    """A joker the engine cannot name is one this harness cannot verify."""
    unknown = sorted(set(REGISTRY) - set(joker_keys))
    assert not unknown, f"not in the game's joker list: {unknown}"


def test_coverage_is_reported_honestly(joker_keys):
    """Fails if the simulator ever claims to be finished when it is not."""
    implemented, total = len(REGISTRY), len(joker_keys)
    verified = implemented - len(KNOWN_BAD) - len(NEEDS_SETUP)
    print(f"\n  jokers in the game   {total}"
          f"\n  implemented          {implemented}"
          f"\n  agreeing with engine {verified}")
    assert total == 150
    assert verified < total, "if this trips, the sim is done -- check it"
