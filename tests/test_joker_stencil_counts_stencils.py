"""Joker Stencil counts the stencils, not just itself.

card.lua:4203-4207 builds its X from the empty slots and then adds one for
*every* Joker Stencil in the row:

    self.ability.x_mult = (G.jokers.config.card_limit - #G.jokers.cards)
    for i = 1, #G.jokers.cards do
      if G.jokers.cards[i].ability.name == 'Joker Stencil' then
        self.ability.x_mult = self.ability.x_mult + 1 end
    end

The simulator added exactly one, for itself. One stencil reads the same
either way, which is why it stood; two do not. U9QERIL2 on Ghost Deck held
two with four jokers in six slots, so the game gave each X4 and the shadow
each X3 -- X16 against X9 -- and the live run stopped after decision 256 on
81360 against 45765, which is 16/9 exactly.
"""

from dataclasses import dataclass

import pytest

from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.hands import evaluate
from jimbot_sim.jokers import make
from jimbot_sim.scoring import score_hand

KEYS = {"Joker Stencil": "j_stencil", "Joker": "j_joker"}
_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "7": Rank.SEVEN, "9": Rank.NINE, "K": Rank.KING}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}

# A pair of Kings out of eight cards, so the row is the only thing moving.
HAND = "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4"
PLAY = (1, 2)


@dataclass(frozen=True)
class Row:
    label: str
    jokers: tuple
    want: int                  # what the engine scores


# Five slots throughout, so the empty count is 5 - len(jokers).
ROWS = [
    Row("no stencil", ("Joker",), 180),
    # Four empty, one stencil: X5.
    Row("one stencil is unchanged", ("Joker Stencil", "Joker"), 360),
    # Two empty, two stencils: X4 each, so X16.
    Row("two stencils count each other",
        ("Joker Stencil", "Joker", "Joker Stencil"), 1440),
    # Two empty, three stencils: X5 each, so X125.
    Row("three of them", ("Joker Stencil",) * 3, 7500),
]


def _card(code):
    suit, rank = code.split("_")
    return Card(_RANKS[rank], _SUITS[suit])


def _sim_score(row):
    codes = HAND.split()
    played = [_card(codes[i - 1]) for i in PLAY]
    held = [_card(c) for i, c in enumerate(codes, 1) if i not in PLAY]
    game = GameState(seed=0)
    game.jokers = [make(name) for name in row.jokers]
    game.hand = played + held
    game.full_deck = list(game.hand)
    result = evaluate(played, splash=game._splash(),
                      smeared=game.has_smeared(),
                      four_fingers=game._four_fingers(),
                      shortcut=game._shortcut())
    return score_hand(game, result, played, held)


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.label)
def test_a_stencil_counts_every_stencil(row):
    ctx = _sim_score(row)
    assert ctx.score == row.want, "\n".join(ctx.log)


# -- the same rows, measured on the engine ------------------------------------

@pytest.fixture(scope="module")
def engine():
    pytest.importorskip("lupa")
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
    if not engine_available():
        pytest.skip("no Balatro engine: need vendor/balatro_src")
    return HeadlessBalatro().boot()


@pytest.mark.slow
@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.label)
def test_the_stencil_rows_match_the_engine(engine, row):
    from jimbot_sim.headless.scenario import Scenario

    scene = Scenario(engine).start().jokers(
        " ".join(KEYS[name] for name in row.jokers)).hand(HAND)
    engine.execute("api.pump(30)")
    got = scene.play(list(PLAY))
    assert got == row.want
    assert _sim_score(row).score == got
