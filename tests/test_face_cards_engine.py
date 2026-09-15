"""Face cards on the engine: Stone cards, Pareidolia, debuffs and The Plant.

Card:is_face (card.lua:964-970) returns nothing for a debuffed card unless
from_boss is set, reads get_id -- a random negative for a Stone card
(card.lua:957-962) -- and then `or next(find_joker("Pareidolia"))`, which does
not look at the id at all. test_face_cards holds the simulator to that rule
case by case; these put the same positions on the engine and ask for its
numbers, so the rule is measured rather than read.

The order of the setup matters for the boss. Scenario.hand goes through
set_base, which re-runs Blind:debuff_card (card.lua:143); Scenario.enhance
calls set_ability with `initial`, which does not (card.lua:365). So the boss
comes last: set_blind debuffs every playing card afresh (blind.lua:207-210),
and blind.lua:630 asks `card:is_face(true)` of the Stone King.
"""

from dataclasses import dataclass, field

import pytest

pytest.importorskip("lupa")

from jimbot_sim.blinds import BOSSES, Blind, BlindKind  # noqa: E402
from jimbot_sim.cards import Card, Enhancement, Rank, Suit  # noqa: E402
from jimbot_sim.game import GameState  # noqa: E402
from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available  # noqa: E402
from jimbot_sim.headless.scenario import Scenario  # noqa: E402
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance, make  # noqa: E402
from jimbot_sim.scoring import score_hand  # noqa: E402

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not engine_available(),
                       reason="no Balatro engine: need vendor/balatro_src"),
]

HAND = "S_K H_K D_7 C_4 S_2 H_9 D_3 C_5"

_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT, "9": Rank.NINE,
          "T": Rank.TEN, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
          "A": Rank.ACE}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}
_ENHANCEMENTS = {"m_stone": Enhancement.STONE, "m_mult": Enhancement.MULT}
_JOKERS = {"j_scary_face": "Scary Face", "j_pareidolia": "Pareidolia",
           "j_sock_and_buskin": "Sock and Buskin", "j_photograph": "Photograph",
           "j_midas_mask": "Midas Mask", "j_ride_the_bus": "Ride the Bus",
           "j_caino": "Canio"}
PLANT = next(b for b in BOSSES if b.name == "The Plant")


@dataclass
class Case:
    name: str
    jokers: str
    play: tuple = (1, 2)
    enhance: dict = field(default_factory=lambda: {1: "m_stone"})
    boss: bool = False
    ride_the_bus: int | None = None


CASES = [
    Case("scary face, a stone king", "j_scary_face"),
    Case("pareidolia, scary face, a stone king",
         "j_pareidolia j_scary_face"),                   # card.lua:3136
    Case("pareidolia, sock and buskin, a stone king",
         "j_pareidolia j_sock_and_buskin"),              # card.lua:3344
    Case("pareidolia, photograph, a stone king before a mult king",
         "j_pareidolia j_photograph",
         enhance={1: "m_stone", 2: "m_mult"}),           # card.lua:3093
    Case("pareidolia, midas mask, a stone king",
         "j_pareidolia j_midas_mask"),                   # card.lua:3443
    Case("pareidolia, ride the bus, a lone stone king",
         "j_pareidolia j_ride_the_bus", play=(1,),
         ride_the_bus=3),                                # card.lua:3525
    Case("the plant, a stone king", "", boss=True),     # blind.lua:630
    Case("the plant beside pareidolia, a stone king", "j_pareidolia",
         boss=True),
    Case("the plant, midas mask, two debuffed kings", "j_midas_mask",
         enhance={}, boss=True),                         # card.lua:965
]


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


def _int(engine, expr):
    return int(float(engine.eval("(function() return %s end)()" % expr)))


_GOLD = ("(function() local n = 0 for _, c in ipairs(G.playing_cards) do "
         "if c.ability.name == 'Gold Card' then n = n + 1 end end "
         "return n end)()")


def engine_answer(engine, case):
    scene = Scenario(engine).start()
    if case.jokers:
        scene.jokers(case.jokers)
    scene.hand(HAND)
    for index, key in case.enhance.items():
        scene.enhance(index, enhancement=key)
    if case.boss:
        scene.boss("bl_plant")
    if case.ride_the_bus is not None:
        engine.execute("for _, j in ipairs(G.jokers.cards) do "
                       "if j.ability.name == 'Ride the Bus' then "
                       "j.ability.mult = %d end end" % case.ride_the_bus)
    debuffs = tuple(_int(engine, "G.hand.cards[%d].debuff and 1 or 0" % i)
                    for i in (1, 2, 3))
    score = scene.play(case.play)
    return debuffs, score, int(float(engine.eval(_GOLD)))


def _card(code):
    return Card(_RANKS[code[2]], _SUITS[code[0]])


def simulator_answer(case):
    game = GameState(seed=0)
    game.jokers = [make(_JOKERS[key]) for key in case.jokers.split()]
    if case.boss:
        game.blind = Blind(BlindKind.BOSS, ante=1, target=999999999,
                           reward=5, boss=PLANT)
    cards = [_card(code) for code in HAND.split()]
    for index, key in case.enhance.items():
        cards[index - 1].enhancement = _ENHANCEMENTS[key]
    for joker in game.jokers:
        if joker.name == "Ride the Bus":
            joker.counter = float(case.ride_the_bus)
    game.hand = list(cards)
    game.full_deck = list(cards)
    game._apply_debuffs()
    debuffs = tuple(int(c.debuffed) for c in cards[:3])
    played = [cards[i - 1] for i in case.play]
    held = [c for i, c in enumerate(cards, start=1) if i not in case.play]
    result = game.evaluate_selection(played)
    score = score_hand(game, result, played, held).score
    gold = sum(1 for c in game.full_deck if c.enhancement is Enhancement.GOLD)
    return debuffs, score, gold


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_the_simulator_matches_the_engine(engine, case):
    expected = engine_answer(engine, case)
    assert simulator_answer(case) == expected, (
        "%s: engine (debuffs, score, gold cards) %s" % (case.name, expected))


# Canio: `if val:is_face() then face_cards = face_cards + 1 end` over
# context.removed (card.lua:2673-2679), asked of the engine directly.
CANIO = [
    # name, row, Lua on hand card 1, sim change, gain
    ("a king", "j_caino", "", {}, 1),
    ("a stone king", "j_caino",
     "G.hand.cards[1]:set_ability(G.P_CENTERS.m_stone, nil, true)",
     {"stone": True}, 0),
    ("a debuffed king", "j_caino", "G.hand.cards[1]:set_debuff(true)",
     {"debuffed": True}, 0),
    ("pareidolia, a stone two", "j_caino j_pareidolia",
     "G.hand.cards[1]:set_base(G.P_CARDS.S_2); "
     "G.hand.cards[1]:set_ability(G.P_CENTERS.m_stone, nil, true)",
     {"stone": True, "rank": Rank.TWO}, 1),
]


@pytest.mark.parametrize("name, row, lua, sim, gain", CANIO,
                         ids=[c[0] for c in CANIO])
def test_canio_counts_as_the_engine_does(engine, name, row, lua, sim, gain):
    Scenario(engine).start().jokers(row).hand(HAND)
    if lua:
        engine.execute(lua)
    before = float(engine.eval("G.jokers.cards[1].ability.caino_xmult"))
    engine.execute("G.jokers.cards[1]:calculate_joker({remove_playing_cards "
                   "= true, removed = {G.hand.cards[1]}})")
    after = float(engine.eval("G.jokers.cards[1].ability.caino_xmult"))
    assert after - before == gain

    game = GameState(seed="TESTSEED", deck="Red Deck")
    for key in row.split():
        game.gain_joker(JokerInstance(JOKERS[_JOKERS[key]]))
    game._start_round()
    card = game.hand[0]
    card.rank = sim.get("rank", Rank.KING)
    card.debuffed = sim.get("debuffed", False)
    if sim.get("stone"):
        card.enhancement = Enhancement.STONE
    canio = next(j for j in game.jokers if j.name == "Canio")
    counter = canio.counter
    game.remove_card(card)
    assert canio.counter - counter == gain
