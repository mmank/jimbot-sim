"""Retriggers, copiers and modified cards, which is where fidelity dies.

Single jokers on a plain hand are the easy half. What breaks a reimplementation
is the interaction: how many times a card scores, in what order the copies of a
copier resolve, and whether a seal's extra trigger is counted before or after a
joker's. Blueprint copying Blueprint copying Mime is three different questions
about ordering that no single-joker test can ask.

Each case here is written once and handed to both engines, so the position
cannot drift between them. The cases deliberately stack:

  seals and enhancements   a red seal retriggers the card, gold pays on score,
                           glass and polychrome multiply, so the same card is
                           counted several times with different effects
  copier chains            Blueprint copies its right neighbour, Brainstorm the
                           leftmost -- put three in a row and the chain has to
                           resolve in the game's order, not a plausible one
  held-card retriggers     Mime retriggers abilities held in hand, Baron pays
                           per held king; together they multiply

Lucky cards are here too, and are expected to disagree until the RNG pools are
synchronised: a lucky card rolls 1 in 5 for +20 mult, so the two engines are
drawing from different streams rather than disagreeing about the rules.
"""

from dataclasses import dataclass, field

import pytest

from balatro.cards import Card, Edition, Enhancement, Rank, Seal, Suit, standard_deck
from balatro.game import GameState
from balatro.hands import evaluate
from balatro.jokers import make
from balatro.scoring import score_hand
from balatro_headless.runtime import HeadlessBalatro
from balatro_headless.scenario import Scenario

_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT, "9": Rank.NINE,
          "T": Rank.TEN, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
          "A": Rank.ACE}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}

# The game's keys on the left, the simulator's enums on the right. Written out
# rather than derived so a rename on either side fails loudly here.
_ENHANCEMENTS = {"m_bonus": Enhancement.BONUS, "m_mult": Enhancement.MULT,
                 "m_wild": Enhancement.WILD, "m_glass": Enhancement.GLASS,
                 "m_steel": Enhancement.STEEL, "m_stone": Enhancement.STONE,
                 "m_gold": Enhancement.GOLD, "m_lucky": Enhancement.LUCKY}
_SEALS = {"Gold": Seal.GOLD, "Red": Seal.RED, "Blue": Seal.BLUE,
          "Purple": Seal.PURPLE}
_EDITIONS = {"foil": Edition.FOIL, "holo": Edition.HOLOGRAPHIC,
             "polychrome": Edition.POLYCHROME}


@dataclass(frozen=True)
class Spec:
    """One card, as both engines need to see it."""
    code: str
    enhancement: str = ""
    seal: str = ""
    edition: str = ""

    def to_card(self) -> Card:
        suit, rank = self.code.split("_")
        return Card(
            _RANKS[rank], _SUITS[suit],
            enhancement=_ENHANCEMENTS.get(self.enhancement, Enhancement.NONE),
            seal=_SEALS.get(self.seal, Seal.NONE),
            edition=_EDITIONS.get(self.edition, Edition.NONE),
        )


@dataclass(frozen=True)
class Combo:
    name: str
    cards: tuple
    play: tuple
    jokers: tuple = ()
    rng_dependent: bool = False

    @property
    def played(self):
        return [self.cards[i - 1].to_card() for i in self.play]

    @property
    def held(self):
        return [c.to_card() for i, c in enumerate(self.cards, 1)
                if i not in self.play]


def C(code, **kw):
    return Spec(code, **kw)


PLAIN = (C("D_2"), C("C_5"), C("H_7"))     # filler that scores nothing special

COMBOS = [
    Combo("red seal retriggers a king",
          (C("S_K", seal="Red"), C("H_K")) + PLAIN, (1, 2)),
    Combo("gold seal on a scored card",
          (C("S_K", seal="Gold"), C("H_K")) + PLAIN, (1, 2)),
    Combo("polychrome and glass on the same hand",
          (C("S_K", edition="polychrome"), C("H_K", enhancement="m_glass"))
          + PLAIN, (1, 2)),
    Combo("red seal under Sock and Buskin",
          (C("S_K", seal="Red"), C("H_Q")) + PLAIN, (1, 2),
          ("Sock and Buskin",)),
    Combo("Hanging Chad on a face hand",
          (C("S_K"), C("H_Q"), C("D_J")) + PLAIN[:2], (1, 2, 3),
          ("Hanging Chad",)),
    Combo("Dusk on the final hand",
          (C("S_K"), C("H_K")) + PLAIN, (1, 2), ("Dusk",)),
    Combo("Hack retriggers low cards",
          (C("S_2"), C("H_2"), C("D_5"), C("C_5"), C("H_7")), (1, 2, 3, 4),
          ("Hack",)),
    Combo("Mime with steel held",
          (C("S_K"), C("H_K"), C("D_2", enhancement="m_steel"),
           C("C_5", enhancement="m_steel"), C("H_7")), (1, 2), ("Mime",)),
    Combo("Baron with kings held",
          (C("S_9"), C("H_9"), C("D_K"), C("C_K"), C("H_K")), (1, 2),
          ("Baron",)),
    Combo("Mime and Baron together",
          (C("S_9"), C("H_9"), C("D_K"), C("C_K"), C("H_K")), (1, 2),
          ("Mime", "Baron")),
    Combo("Blueprint copies Baron",
          (C("S_9"), C("H_9"), C("D_K"), C("C_K"), C("H_K")), (1, 2),
          ("Blueprint", "Baron")),
    Combo("Blueprint chain onto Baron",
          (C("S_9"), C("H_9"), C("D_K"), C("C_K"), C("H_K")), (1, 2),
          ("Blueprint", "Blueprint", "Baron")),
    Combo("the stack the player asked for",
          (C("S_K", seal="Red"), C("H_K", enhancement="m_glass"),
           C("D_K", edition="polychrome"), C("C_2", enhancement="m_gold"),
           C("H_5")), (1, 2, 3),
          ("Blueprint", "Blueprint", "Mime", "Blueprint", "Baron")),
    Combo("Brainstorm copies the leftmost",
          (C("S_K"), C("H_K")) + PLAIN, (1, 2),
          ("Sock and Buskin", "Blueprint", "Brainstorm")),
    # A 3 5 7 9, four of them spades. High Card alone; Four Fingers makes the
    # flush out of four cards, Shortcut allows the one-rank gaps, and together
    # they make it a Straight Flush -- a hand shape neither joker reaches on
    # its own, and the sort of interaction a reimplementation gets wrong.
    Combo("four fingers and shortcut make a straight flush",
          (C("S_A"), C("S_3"), C("C_5"), C("S_7"), C("S_9")), (1, 2, 3, 4, 5),
          ("Four Fingers", "Shortcut")),
    Combo("four fingers alone is only a flush",
          (C("S_A"), C("S_3"), C("C_5"), C("S_7"), C("S_9")), (1, 2, 3, 4, 5),
          ("Four Fingers",)),
    Combo("shortcut alone is only a straight",
          (C("S_A"), C("S_3"), C("C_5"), C("S_7"), C("S_9")), (1, 2, 3, 4, 5),
          ("Shortcut",)),
    Combo("lucky cards roll their own stream",
          (C("S_K", enhancement="m_lucky"), C("H_K", enhancement="m_lucky"))
          + PLAIN, (1, 2), (), True),
]

JOKER_KEYS_QUERY = (
    '(function()'
    '  local t = {}'
    '  for k, v in pairs(G.P_CENTERS) do'
    '    if v.set == "Joker" then'
    '      local loc = G.localization.descriptions.Joker[k]'
    '      if loc then t[#t+1] = k .. "=" .. loc.name end'
    '    end'
    '  end'
    '  return table.concat(t, "|") end)()')


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


@pytest.fixture(scope="module")
def joker_keys(engine):
    raw = engine.eval(JOKER_KEYS_QUERY)
    return {name: key for key, name in
            (pair.split("=", 1) for pair in raw.split("|"))}


def _engine_score(engine, combo, joker_keys):
    scene = Scenario(engine).start()
    scene.hand(" ".join(c.code for c in combo.cards))
    for i, spec in enumerate(combo.cards, start=1):
        if spec.enhancement or spec.seal or spec.edition:
            scene.enhance(i, enhancement=spec.enhancement or None,
                          seal=spec.seal or None, edition=spec.edition or None)
    if combo.jokers:
        scene.jokers(" ".join(joker_keys[n] for n in combo.jokers))
    scene.select(combo.play)
    read = lambda e: int(engine.eval("(function() return %s end)()" % e))
    state = {
        "discards_left": read("G.GAME.current_round.discards_left"),
        # The game spends the hand before the jokers score it, so a joker
        # that asks "is this the last hand?" -- Acrobat, Dusk -- sees one
        # fewer than the state read here. Syncing the pre-play number left
        # both engines agreeing that it was never the final hand.
        "hands_left": read("G.GAME.current_round.hands_left") - 1,
        "money": read("G.GAME.dollars"),
        "draw_pile": read("#G.deck.cards"),
        "hands_played": read("G.GAME.hands_played"),
    }
    return scene.play(), state


def _sim_score(combo, state):
    game = GameState(seed=0)
    game.jokers = [make(n) for n in combo.jokers]
    played, held = combo.played, combo.held
    game.hand = played + held
    game.discards_left = state["discards_left"]
    game.hands_left = state["hands_left"]
    game.money = state["money"]
    game.hands_played = state["hands_played"]
    game.draw_pile = standard_deck()[:state["draw_pile"]]
    result = evaluate(played, four_fingers=game._four_fingers(),
                      shortcut=game._shortcut())
    game.hand_levels.plays[result.hand] = 1
    return score_hand(game, result, played, held).score


@pytest.mark.parametrize("combo", COMBOS, ids=lambda c: c.name)
def test_engines_agree(engine, joker_keys, combo):
    if combo.rng_dependent:
        pytest.xfail("draws from the game's RNG pools, not yet synchronised")
    expected, state = _engine_score(engine, combo, joker_keys)
    assert expected == _sim_score(combo, state), (
        "%s: engine %d, simulator %d" % (combo.name, expected,
                                         _sim_score(combo, state)))


def test_stacking_a_copier_actually_changes_the_score(engine, joker_keys):
    """Guard against the whole suite passing because copiers do nothing.

    If Blueprint were silently inert both engines would agree on every case
    above and the chain tests would prove nothing, exactly as the single-hand
    joker matrix did before the scenario set was widened.
    """
    baron = next(c for c in COMBOS if c.name == "Baron with kings held")
    one = next(c for c in COMBOS if c.name == "Blueprint copies Baron")
    two = next(c for c in COMBOS if c.name == "Blueprint chain onto Baron")
    scores = [_engine_score(engine, c, joker_keys)[0] for c in (baron, one, two)]
    assert scores[0] < scores[1] < scores[2], (
        "stacking Blueprints onto Baron did not raise the score: %s" % scores)
