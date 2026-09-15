"""Flower Pot hands each scoring card one suit, and a Wild card only what is left.

The game does not ask "is every suit represented". It fills four slots, one
card at a time, and each card fills the *first* empty slot it can
(card.lua:3807-3833):

    for i = 1, #context.scoring_hand do
        if context.scoring_hand[i].ability.name ~= 'Wild Card' then
            if context.scoring_hand[i]:is_suit('Hearts', true) and suits["Hearts"] == 0 then ...
            elseif context.scoring_hand[i]:is_suit('Diamonds', true) and suits["Diamonds"] == 0 then ...
            elseif ... 'Spades' ... elseif ... 'Clubs' ... end
    for i = 1, #context.scoring_hand do
        if context.scoring_hand[i].ability.name == 'Wild Card' then
            if context.scoring_hand[i]:is_suit('Hearts') and suits["Hearts"] == 0 then ...
            elseif ... end

Three things follow, and the simulator's "any card counts as each suit" got
all three wrong:

  * a Wild card fills one slot, not four, and only after every other card
    has had its pick -- so one Wild beside a Heart and a Spade is not all
    four suits;
  * beside a Smeared Joker `is_suit` answers Hearts for a Diamond and Spades
    for a Club (card.lua:4084), so a second red card fills Diamonds and a
    second black one Clubs;
  * the plain cards ask with bypass_debuff, so a debuffed Club still fills
    Clubs, while a Wild card asks without it and a debuffed one fills
    nothing (card.lua:4077).

0RVVD29X (Ghost Deck, stake 1) stopped on the first two at decision 99:
Photograph (polychrome), Smeared Joker (foil), Hanging Chad (holo),
Brainstorm, Flower Pot, and a Flush of a Wild Queen of Diamonds, J and 10 of
Clubs, 9 and 6 of Spades. The Clubs fill Spades then Clubs, the Spades find
nothing left, the Wild fills Hearts, and Diamonds stays empty: the game
scored 150 x 394 = 59100 and the shadow gave the X3 for 177300.
"""

from dataclasses import dataclass

import pytest

from jimbot_sim.cards import Card, Edition, Enhancement, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.hands import evaluate
from jimbot_sim.jokers import make
from jimbot_sim.scoring import score_hand

_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT, "9": Rank.NINE,
          "T": Rank.TEN, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
          "A": Rank.ACE}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}
KEYS = {"Photograph": "j_photograph", "Smeared Joker": "j_smeared",
        "Hanging Chad": "j_hanging_chad", "Brainstorm": "j_brainstorm",
        "Flower Pot": "j_flower_pot"}
EDITIONS = {Edition.FOIL: "foil", Edition.HOLOGRAPHIC: "holo",
            Edition.POLYCHROME: "polychrome"}


@dataclass(frozen=True)
class Row:
    label: str
    jokers: tuple
    hand: str
    want: int                  # what the engine scores
    play: tuple = (1, 2, 3, 4, 5)
    wild: tuple = ()           # hand positions, from 1
    editions: tuple = ()       # (joker position from 0, Edition)
    boss: str = ""             # a boss key; The Club debuffs Clubs and Wilds
    debuffed: tuple = ()       # hand positions the boss debuffs, from 1


ROWS = [
    Row("0RVVD29X decision 99",
        ("Photograph", "Smeared Joker", "Hanging Chad", "Brainstorm",
         "Flower Pot"),
        "D_Q C_J C_T S_9 D_9 S_6 S_5 S_2", 59100, play=(1, 2, 3, 4, 6),
        wild=(1,), editions=((0, Edition.POLYCHROME), (1, Edition.FOIL),
                             (2, Edition.HOLOGRAPHIC))),
    # Straights, so five cards score without being one suit.
    Row("one wild cannot fill two suits", ("Flower Pot",),
        "H_9 H_8 S_7 D_6 H_5 C_2 C_3 S_K", 260, wild=(4,)),
    Row("a wild fills the one suit missing", ("Flower Pot",),
        "H_9 C_8 S_7 H_6 H_5 D_2 D_3 S_K", 780, wild=(4,)),
    Row("smeared: a second heart fills diamonds",
        ("Smeared Joker", "Flower Pot"),
        "H_9 H_8 S_7 S_6 H_5 D_2 C_3 C_K", 780),
    Row("smeared: black cards and a wild leave diamonds empty",
        ("Smeared Joker", "Flower Pot"),
        "C_9 C_8 S_7 S_6 D_5 H_2 H_3 D_K", 1080, wild=(5,)),
    Row("a debuffed club still fills clubs", ("Flower Pot",),
        "H_9 C_8 S_7 D_6 H_5 D_2 H_3 S_K", 684,
        boss="bl_club", debuffed=(2,)),
    Row("a debuffed wild fills nothing", ("Flower Pot",),
        "H_9 S_8 D_7 C_6 H_5 D_2 H_3 S_K", 236, wild=(4,),
        boss="bl_club", debuffed=(4,)),
]


def _card(code):
    suit, rank = code.split("_")
    return Card(_RANKS[rank], _SUITS[suit])


def _sim_score(row):
    cards = [_card(code) for code in row.hand.split()]
    for i in row.wild:
        cards[i - 1].enhancement = Enhancement.WILD
    for i in row.debuffed:
        cards[i - 1].debuffed = True
    played = [cards[i - 1] for i in row.play]
    held = [c for i, c in enumerate(cards, 1) if i not in row.play]
    game = GameState(seed=0)
    jokers = [make(name) for name in row.jokers]
    for position, edition in row.editions:
        jokers[position].edition = edition
    game.jokers = jokers
    game.hand = played + held
    game.full_deck = list(game.hand)
    result = evaluate(played, splash=game._splash(),
                      smeared=game.has_smeared(),
                      four_fingers=game._four_fingers(),
                      shortcut=game._shortcut())
    return score_hand(game, result, played, held)


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.label)
def test_flower_pot_fills_one_suit_per_card(row):
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
def test_flower_pot_rows_match_the_engine(engine, row):
    from jimbot_sim.headless.scenario import Scenario

    scene = Scenario(engine).start()
    if row.boss:
        scene.boss(row.boss)
    scene.jokers(" ".join(KEYS[name] for name in row.jokers)).hand(row.hand)
    for i in row.wild:
        scene.enhance(i, "m_wild")
    for position, edition in row.editions:
        engine.execute("G.jokers.cards[%d]:set_edition({%s = true}, true, true)"
                       % (position + 1, EDITIONS[edition]))
    engine.execute("api.pump(30)")
    debuffed = tuple(i for i in range(1, 9)
                     if engine.eval("G.hand.cards[%d].debuff" % i))
    assert debuffed == row.debuffed
    got = scene.play(list(row.play))
    assert got == row.want
    assert _sim_score(row).score == got
