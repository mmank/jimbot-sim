"""Jokers that grow, checked at a value they have actually grown to.

A scaling joker on a fresh run contributes nothing: Glass Joker at X1, Flash
Card at +0. Both engines agree on nothing happening, and the scaling formula --
the part that is easy to get wrong -- never runs. The differential matrix
reports these as inert for exactly that reason.

So set the accumulated value on both sides and compare. The engine keeps it on
the joker (ability.x_mult, ability.mult, ability.extra.chips depending on the
joker) and the simulator keeps it in the instance counter, so the table below
is also a statement about where each joker's state lives -- which is the thing
that was wrong when these were first written against a run-wide counter.

Some of these jokers are never even offered without the right cards in the
deck: the game gates Glass Joker, Golden Ticket, Lucky Cat, Steel Joker and
Stone Joker behind owning a card of the matching enhancement. Forcing them
here is fine -- it tests the arithmetic -- but a shop that offered them
ungated would be generating runs the real game cannot produce.
"""

from dataclasses import dataclass

import pytest

from balatro.cards import Card, Rank, Suit, standard_deck

_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT, "9": Rank.NINE,
          "T": Rank.TEN, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
          "A": Rank.ACE}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}


def _card(code):
    suit, rank = code.split("_")
    return Card(_RANKS[rank], _SUITS[suit])
from balatro.game import GameState
from balatro.hands import HandType, evaluate
from balatro.jokers import make
from balatro.scoring import score_hand
from balatro_headless.runtime import HeadlessBalatro
from balatro_headless.scenario import Scenario

HAND = "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4"
PLAY = (1, 2)
PLAYED = [Card(Rank.KING, Suit.SPADES), Card(Rank.KING, Suit.HEARTS)]
HELD = [Card(Rank.TWO, Suit.DIAMONDS), Card(Rank.FIVE, Suit.CLUBS),
        Card(Rank.SEVEN, Suit.HEARTS), Card(Rank.NINE, Suit.SPADES),
        Card(Rank.THREE, Suit.DIAMONDS), Card(Rank.FOUR, Suit.CLUBS)]


@dataclass(frozen=True)
class Scaled:
    """A joker, the engine field holding its growth, and a grown value."""
    name: str
    key: str
    field: str          # lua path under the joker card's `ability`
    value: float
    # Some jokers reset on the shared hand -- Ride the Bus clears on a face
    # card, and the default hand is two kings -- so they need one of their own.
    hand: str = ""
    play: tuple = ()
    # A couple of these do not keep their growth at all: the game recomputes
    # Throwback from G.GAME.skips every frame, and rewrites Obelisk before
    # each hand. Setting ability.x_mult on those is overwritten, so the run
    # state that drives them has to be set on both sides instead.
    lua_setup: str = ""
    sim_setup: object = None


CASES = [
    Scaled("Constellation", "j_constellation", "x_mult", 1.5),
    Scaled("Throwback", "j_throwback", "x_mult", 2.0,
           lua_setup="G.GAME.skips = 4",
           sim_setup=lambda g: setattr(g, "blinds_skipped", 4)),
    Scaled("Glass Joker", "j_glass", "x_mult", 3.25),
    Scaled("Lucky Cat", "j_lucky_cat", "x_mult", 1.75),
    Scaled("Campfire", "j_campfire", "x_mult", 2.5),
    Scaled("Flash Card", "j_flash", "mult", 12),
    Scaled("Green Joker", "j_green_joker", "mult", 7),
    Scaled("Ride the Bus", "j_ride_the_bus", "mult", 9,
           hand="S_9 H_9 D_2 C_5 H_7 S_3 D_4 C_6", play=(1, 2)),
    Scaled("Spare Trousers", "j_trousers", "mult", 6),
    Scaled("Ceremonial Dagger", "j_ceremonial", "mult", 14),
    Scaled("Ice Cream", "j_ice_cream", "extra.chips", 65),
    Scaled("Runner", "j_runner", "extra.chips", 45),
    Scaled("Square Joker", "j_square", "extra.chips", 28),
    Scaled("Wee Joker", "j_wee", "extra.chips", 32),
    Scaled("Castle", "j_castle", "extra.chips", 21),
    Scaled("Hit the Road", "j_hit_the_road", "x_mult", 2.5),
    # Obelisk grows only while some other hand has been played at least as
    # often as the one being played, so give Flush a history.
    Scaled("Obelisk", "j_obelisk", "x_mult", 1.8,
           lua_setup='G.GAME.hands["Flush"].played = 5',
           sim_setup=lambda g: g.hand_levels.plays.__setitem__(
               HandType.FLUSH, 5)),
    Scaled("Vampire", "j_vampire", "x_mult", 1.6),
    Scaled("Madness", "j_madness", "x_mult", 2.5),
    Scaled("Red Card", "j_red_card", "mult", 9),
]


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


def _engine_score(engine, case):
    scene = Scenario(engine).start().hand(case.hand or HAND).jokers(case.key)
    if case.lua_setup:
        engine.execute(case.lua_setup)
    engine.execute("G.jokers.cards[1].ability.%s = %r; api.pump(20)"
                   % (case.field, case.value))
    scene.select(case.play or PLAY)
    read = lambda e: int(engine.eval("(function() return %s end)()" % e))
    label = engine.eval(
        "(function() return G.GAME.current_round.current_hand.handname end)()")
    state = {
        "discards_left": read("G.GAME.current_round.discards_left"),
        "hands_left": read("G.GAME.current_round.hands_left") - 1,
        "money": read("G.GAME.dollars"),
        "draw_pile": read("#G.deck.cards"),
        "hands_played": read("G.GAME.hands_played"),
        "plays": read('G.GAME.hands["%s"].played' % label) + 1,
    }
    return scene.play(), state


def _cards(case):
    if not case.hand:
        return PLAYED, HELD
    codes = case.hand.split()
    play = case.play
    return ([_card(codes[i - 1]) for i in play],
            [_card(c) for i, c in enumerate(codes, 1) if i not in play])


def _sim_score(case, state):
    game = GameState(seed=0)
    played, held = _cards(case)
    joker = make(case.name)
    joker.counter = case.value
    game.jokers = [joker]
    game.hand = played + held
    game.discards_left = state["discards_left"]
    game.hands_left = state["hands_left"]
    game.money = state["money"]
    game.hands_played = state["hands_played"]
    game.draw_pile = standard_deck()[:state["draw_pile"]]
    game.full_deck = game.hand + game.draw_pile
    result = evaluate(played, splash=game._splash(),
                    smeared=game.has_smeared(),
                    four_fingers=game._four_fingers(),
                    shortcut=game._shortcut())
    game.hand_levels.plays[result.hand] = state["plays"]
    if case.sim_setup is not None:
        case.sim_setup(game)
    return score_hand(game, result, played, held).score


@pytest.fixture(scope="module")
def baselines(engine):
    """No-joker score for each hand the table uses."""
    out = {}
    for hand, play in {(c.hand or HAND, c.play or PLAY) for c in CASES}:
        scene = Scenario(engine).start().hand(hand)
        scene.select(play)
        out[hand] = scene.play()
    engine.execute("G.GAME.skips = 0")
    return out


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_scaled_joker_agrees(engine, baselines, case):
    expected, state = _engine_score(engine, case)
    assert expected == _sim_score(case, state), (
        "%s grown to %s: engine %d, simulator %d"
        % (case.name, case.value, expected, _sim_score(case, state)))
    assert expected != baselines[case.hand or HAND], (
        "%s at %s scored the same as no joker at all, so the growth was "
        "never applied by either side" % (case.name, case.value))
