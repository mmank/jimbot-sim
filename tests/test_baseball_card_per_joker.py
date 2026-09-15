"""Baseball Card multiplies after each Uncommon joker, where that joker sits.

It is not a joker_main effect at all. The game's joker pass
(state_events.lua:877-944) takes each card in the row in turn and, for that
card, adds its foil or holo (880-902), runs its own joker_main (905-916), then
asks every joker in the row about it under context.other_joker (918-930), and
only then applies its polychrome (932-943). Baseball Card answers that last
question (card.lua:3396-3408):

    elseif context.other_joker then
        if self.ability.name == 'Baseball Card'
           and context.other_joker.config.center.rarity == 2
           and self ~= context.other_joker then
            return { Xmult_mod = self.ability.extra }

So the X1.5 lands straight after the Uncommon joker's own effect, and a
+Mult joker between that one and the Baseball Card is not multiplied by it.
The simulator multiplied by 1.5 per Uncommon joker once, at the Baseball
Card's own position, which multiplied everything to its left.

Nothing there asks whether the *other* joker is debuffed, so a debuffed
Uncommon joker still draws its X1.5 -- while a debuffed Baseball Card gives
nothing, because calculate_joker opens with `if self.debuff then return nil
end` (card.lua:2291-2292), and a debuffed joker has no edition to add
(card.lua:1016-1017). A Blueprint copying the Baseball Card passes the
context on (card.lua:2304-2317), so it answers every Uncommon joker too.

8KUQ2KZU (Yellow Deck, stake 1) stopped on it at decision 19: Hanging Chad,
Mime, Popcorn, Baseball Card, Troubadour and an Ace-low Straight. 77 chips
either way; the game's mult was ((4 x 1.5) + 20) x 1.5 = 39 for 3003, and
the shadow's (4 + 20) x 2.25 = 54 for 4158.
"""

from dataclasses import dataclass

import pytest

from jimbot_sim.cards import Card, Edition, Rank, Suit
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
KEYS = {"Hanging Chad": "j_hanging_chad", "Mime": "j_mime",
        "Popcorn": "j_popcorn", "Baseball Card": "j_baseball",
        "Troubadour": "j_troubadour", "Blueprint": "j_blueprint"}
EDITIONS = {Edition.HOLOGRAPHIC: "holo", Edition.POLYCHROME: "polychrome"}

PAIR = "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4"
# Troubadour's two extra cards are drawn when it joins, so this hand is set
# after the jokers and holds ten; the A-5 straight is cards 1 and 7-10.
STRAIGHT = "H_A H_J H_T H_9 S_8 S_7 D_5 D_4 S_3 C_2"


def _card(code):
    suit, rank = code.split("_")
    return Card(_RANKS[rank], _SUITS[suit])


@dataclass(frozen=True)
class Row:
    label: str
    jokers: tuple
    want: int                  # what the engine scores
    debuffed: tuple = ()       # positions in the row, from 0
    edition: tuple = ()        # (position, Edition)
    hand: str = PAIR
    play: tuple = (1, 2)


ROWS = [
    Row("8KUQ2KZU decision 19",
        ("Hanging Chad", "Mime", "Popcorn", "Baseball Card", "Troubadour"),
        3003, hand=STRAIGHT, play=(1, 7, 8, 9, 10)),
    Row("mime then baseball", ("Mime", "Baseball Card"), 90),
    Row("debuffed mime still counts", ("Mime", "Baseball Card"), 90,
        debuffed=(0,)),
    Row("debuffed baseball gives nothing", ("Mime", "Baseball Card"), 60,
        debuffed=(1,)),
    Row("blueprint copies baseball", ("Mime", "Blueprint", "Baseball Card"),
        135),
    Row("holo mime, then its x1.5, then popcorn",
        ("Mime", "Popcorn", "Baseball Card"), 1140,
        edition=(0, Edition.HOLOGRAPHIC)),
    Row("debuffed mime left of popcorn",
        ("Mime", "Popcorn", "Baseball Card"), 690, debuffed=(0,)),
    Row("polychrome mime left of popcorn",
        ("Mime", "Popcorn", "Baseball Card"), 735,
        edition=(0, Edition.POLYCHROME)),
    Row("popcorn, baseball, debuffed mime",
        ("Popcorn", "Baseball Card", "Mime"), 990, debuffed=(2,)),
]


def _sim_score(row):
    codes = row.hand.split()
    played = [_card(codes[i - 1]) for i in row.play]
    held = [_card(c) for i, c in enumerate(codes, 1) if i not in row.play]
    game = GameState(seed=0)
    jokers = [make(name) for name in row.jokers]
    for i in row.debuffed:
        jokers[i].debuffed = True
    if row.edition:
        jokers[row.edition[0]].edition = row.edition[1]
    game.jokers = jokers
    game.hand = played + held
    game.full_deck = list(game.hand)
    result = evaluate(played, splash=game._splash(),
                      smeared=game.has_smeared(),
                      four_fingers=game._four_fingers(),
                      shortcut=game._shortcut())
    return score_hand(game, result, played, held)


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r.label)
def test_baseball_card_multiplies_after_each_uncommon(row):
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
def test_baseball_card_rows_match_the_engine(engine, row):
    from jimbot_sim.headless.scenario import Scenario

    scene = Scenario(engine).start().jokers(
        " ".join(KEYS[name] for name in row.jokers)).hand(row.hand)
    if row.edition:
        engine.execute("G.jokers.cards[%d]:set_edition({%s = true}, true, true)"
                       % (row.edition[0] + 1, EDITIONS[row.edition[1]]))
    for i in row.debuffed:
        engine.execute("G.jokers.cards[%d]:set_debuff(true)" % (i + 1))
    engine.execute("api.pump(30)")
    got = scene.play(list(row.play))
    assert got == row.want
    assert _sim_score(row).score == got
