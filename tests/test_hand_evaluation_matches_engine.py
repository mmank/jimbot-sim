"""Which poker hand a play is, and which of its cards score, against the game.

Each play below was put on the headless engine and played. The engine's
answer -- the hand's name, the positions of the cards evaluate_play
highlights as scoring, and the chips -- is written down in ENGINE, the
everyday test holds the simulator to it, and the slow test goes back to the
engine to make sure the record has not gone stale.

What the engine confirmed, with the Lua it comes from
(functions/misc_functions.lua unless named):

  get_straight, 548-590
    * every card of a rank in the run is in the straight (557-561, 573-575):
      with Four Fingers 9 8 7 7 6 scores both sevens;
    * a debuffed card holds its place -- it asks only get_id (card.lua:957)
      -- and a Stone card does not, its id being negative (556);
    * the Ace plays low at j = 1 and high at j = 14 and never both, so
      Q K A 2 3 is no straight with Shortcut and Four Fingers together;
    * Shortcut forgives one missing rank at a time (576-577), before or
      after the Ace, and a card past the gap joins a straight already made.
  evaluate_poker_hand, 428-443
    a Straight Flush is any hand holding both parts, sharing cards or not,
    and scores the union: 2S 3S 4S 5H 9S with Four Fingers is one.
  get_flush, 522-546
    the suits are tried Spades, Hearts, Clubs, Diamonds and the *first* to
    reach the count is the flush (532-542) -- not the biggest. Only Wild
    cards make two suits reach it at once. Four Fingers with four Wilds and
    a Club is a flush of Spades, four Wilds, and the Club scores nothing:
    240, where taking the biggest group scored all five for 280.
  both, 531 and 551
    `if #hand > 5 or #hand < (5 - four_fingers)` returns no flush and no
    straight at all -- so six hearts in a row contain neither.
  Card:is_suit(suit, nil, true), card.lua:4064-4075
    a debuffed card keeps its printed suit for a flush, and a debuffed Wild
    only that; The Club debuffs every Wild card, since the boss asks
    is_suit(suit, true), the non-flush question (blind.lua:626).
  evaluate_play, state_events.lua:580-600
    Stone cards score beside any hand, and Splash scores everything played.
"""

from dataclasses import dataclass

import pytest

from jimbot_sim.blinds import BOSSES, Blind, BlindKind
from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.hands import HANDLIST, evaluate
from jimbot_sim.jokers import make
from jimbot_sim.scoring import score_hand

_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT, "9": Rank.NINE,
          "T": Rank.TEN, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
          "A": Rank.ACE}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}
_ENHANCEMENTS = {"wild": Enhancement.WILD, "stone": Enhancement.STONE}
_JOKERS = {"j_four_fingers": "Four Fingers", "j_shortcut": "Shortcut",
           "j_smeared": "Smeared Joker", "j_splash": "Splash"}
_BOSSES = {"bl_club": "The Club", "bl_goad": "The Goad"}

FF, SC, SM, SP = "j_four_fingers", "j_shortcut", "j_smeared", "j_splash"

# The rest of the eight-card hand. Nothing here reads a held card, so what
# sits there only has to be the same on both sides.
FILLER = "D_3 C_4 H_2 S_3 D_4 C_5 H_6 S_7".split()


@dataclass(frozen=True)
class Play:
    """Cards played, all of them, in order: "S_A H_K:wild D_9:stone"."""
    name: str
    cards: str
    jokers: tuple = ()
    boss: str = ""

    @property
    def specs(self):
        return [code.partition(":")[::2] for code in self.cards.split()]

    @property
    def hand_codes(self):
        codes = [code for code, _ in self.specs]
        return codes + FILLER[:8 - len(codes)]

    def sim_cards(self):
        out = []
        for code, enhancement in self.specs:
            suit, rank = code.split("_")
            card = Card(_RANKS[rank], _SUITS[suit])
            if enhancement:
                card.enhancement = _ENHANCEMENTS[enhancement]
            out.append(card)
        return out


PLAYS = [
    # -- Four Fingers: every card of a rank in the run is in the straight ----
    Play("four fingers, a pair inside the run", "H_9 D_8 S_7 C_7 H_6", (FF,)),
    Play("four fingers, a pair at the bottom", "D_7 S_6 C_6 D_5 H_4", (FF,)),
    Play("four fingers, a kicker off the run", "H_9 D_8 S_7 H_6 C_2", (FF,)),
    Play("four fingers, a card past a gap", "S_2 H_3 D_4 C_5 H_7", (FF,)),
    Play("four fingers, ace-low run of four", "S_A H_2 D_3 C_4 H_K", (FF,)),
    Play("four fingers, ace-high run of four", "S_A H_K D_Q C_J H_5", (FF,)),
    # -- Straight Flush is any hand with both parts ---------------------------
    Play("four fingers, straight and flush share three cards",
         "S_2 S_3 S_4 H_5 S_9", (FF,)),
    Play("four fingers, straight flush with a paired eight",
         "H_5 H_6 H_7 H_8 S_8", (FF,)),
    Play("four fingers, a flush of four holding a pair",
         "H_5 S_5 H_7 H_7 H_9", (FF,)),
    Play("four fingers, a flush house", "H_K H_K S_K H_5 H_5", (FF,)),
    # -- too few cards --------------------------------------------------------
    Play("four suited in a row without four fingers", "H_2 H_3 H_4 H_5"),
    Play("four suited in a row with four fingers", "H_2 H_3 H_4 H_5", (FF,)),
    Play("three suited in a row with four fingers", "H_2 H_3 H_4", (FF,)),
    # -- Shortcut ---------------------------------------------------------------
    Play("shortcut, one-rank gaps", "S_A S_3 C_5 S_7 S_9", (SC,)),
    Play("shortcut, ace-low with a gap", "S_A H_2 D_4 C_5 H_6", (SC,)),
    Play("shortcut, a gap straight after the ace", "S_A H_3 D_5 C_6 H_7",
         (SC,)),
    Play("shortcut, gaps at both ends", "S_2 H_4 D_5 C_6 H_8", (SC,)),
    Play("shortcut, ace-high with a gap", "S_A H_Q D_J C_T H_9", (SC,)),
    Play("shortcut, a two-rank gap breaks it", "S_2 H_3 D_4 C_5 H_8", (SC,)),
    Play("shortcut does not wrap round the ace", "S_Q H_K D_A C_2 H_3", (SC,)),
    # -- both -------------------------------------------------------------------
    Play("four fingers and shortcut, a gapped straight flush",
         "S_A S_3 C_5 S_7 S_9", (FF, SC)),
    Play("four fingers and shortcut, a pair inside a gapped run",
         "S_2 H_4 C_4 D_6 S_8", (FF, SC)),
    Play("four fingers and shortcut, the card past the gap joins",
         "S_2 H_3 D_4 C_5 H_7", (FF, SC)),
    Play("four fingers and shortcut do not wrap round the ace",
         "S_Q H_K D_A C_2 H_3", (FF, SC)),
    # -- the ace ----------------------------------------------------------------
    Play("ace high", "S_T H_J D_Q C_K H_A"),
    Play("ace low", "S_A H_2 D_3 C_4 H_5"),
    Play("no wrap round the ace", "S_Q H_K D_A C_2 H_3"),
    # -- Wild cards and Smeared Joker -------------------------------------------
    Play("a wild card completes a flush", "H_2 H_5 H_8 H_J S_K:wild"),
    Play("a wild card completes a straight flush", "S_5 S_6 S_7 S_8 H_9:wild"),
    Play("four fingers, four wilds and a club",
         "D_2:wild D_5:wild D_8:wild D_J:wild C_K", (FF,)),
    Play("four fingers, three wilds, a diamond and a club",
         "H_2:wild H_5:wild H_8:wild D_J C_K", (FF,)),
    Play("smeared, a red straight flush", "H_5 D_6 H_7 D_8 H_9", (SM,)),
    Play("smeared and four fingers, a black four inside a straight",
         "S_5 C_6 S_7 H_8 C_9", (SM, FF)),
    Play("smeared and four fingers, three wilds, a spade and a heart",
         "H_2:wild H_5:wild H_8:wild S_J H_K", (SM, FF)),
    Play("smeared, a wild card in a straight flush",
         "S_5 C_6 H_7:wild S_8 C_9", (SM,)),
    # -- Stone cards --------------------------------------------------------------
    Play("four fingers, a stone card beside a run of four",
         "S_2 H_3 D_4 C_5 S_K:stone", (FF,)),
    Play("a stone card breaks a straight", "S_5 H_6 D_7 C_8 S_9:stone"),
    Play("a stone card is no suit", "H_2 H_5 H_8 H_J H_K:stone"),
    Play("four fingers, a stone card beside a flush of four",
         "H_2 H_5 H_8 H_J H_K:stone", (FF,)),
    # -- debuffed cards -----------------------------------------------------------
    Play("the club, a debuffed straight flush", "C_6 C_5 C_4 C_3 C_2",
         boss="bl_club"),
    Play("the goad, a debuffed flush", "S_A S_J S_9 S_6 S_3", boss="bl_goad"),
    Play("the club, one debuffed card in a straight", "H_9 C_8 S_7 D_6 H_5",
         boss="bl_club"),
    Play("the club debuffs a wild card, which keeps only its printed suit",
         "H_2 H_5 H_8 H_J S_K:wild", boss="bl_club"),
    Play("the club debuffs a wild card printed in the flush's suit",
         "H_2 H_5 H_8 H_J H_K:wild", boss="bl_club"),
    Play("smeared under the club debuffs spades and the flush holds",
         "S_A S_Q C_Q S_9 S_6", (SM,), boss="bl_club"),
    Play("the goad, four fingers, a debuffed pair inside the run",
         "H_9 S_8 D_8 S_7 H_6", (FF,), boss="bl_goad"),
    # -- Splash -------------------------------------------------------------------
    Play("splash and four fingers score the kicker", "H_9 D_8 S_7 H_6 C_2",
         (FF, SP)),
]

# What the engine said: (hand, positions of the scoring cards, score), all
# hands at level one. Checked against the engine again by
# test_the_engine_still_says_so.
ENGINE = {
    'four fingers, a pair inside the run':
        ('Straight', (1, 2, 3, 4, 5), 268),
    'four fingers, a pair at the bottom':
        ('Straight', (1, 2, 3, 4, 5), 232),
    'four fingers, a kicker off the run':
        ('Straight', (1, 2, 3, 4), 240),
    'four fingers, a card past a gap':
        ('Straight', (1, 2, 3, 4), 176),
    'four fingers, ace-low run of four':
        ('Straight', (1, 2, 3, 4), 200),
    'four fingers, ace-high run of four':
        ('Straight', (1, 2, 3, 4), 284),
    'four fingers, straight and flush share three cards':
        ('Straight Flush', (1, 2, 3, 4, 5), 984),
    'four fingers, straight flush with a paired eight':
        ('Straight Flush', (1, 2, 3, 4, 5), 1072),
    'four fingers, a flush of four holding a pair':
        ('Flush', (1, 3, 4, 5), 252),
    'four fingers, a flush house':
        ('Flush House', (1, 2, 3, 4, 5), 2520),
    'four suited in a row without four fingers':
        ('High Card', (4,), 10),
    'four suited in a row with four fingers':
        ('Straight Flush', (1, 2, 3, 4), 912),
    'three suited in a row with four fingers':
        ('High Card', (3,), 9),
    'shortcut, one-rank gaps':
        ('Straight', (1, 2, 3, 4, 5), 260),
    'shortcut, ace-low with a gap':
        ('Straight', (1, 2, 3, 4, 5), 232),
    'shortcut, a gap straight after the ace':
        ('Straight', (1, 2, 3, 4, 5), 248),
    'shortcut, gaps at both ends':
        ('Straight', (1, 2, 3, 4, 5), 220),
    'shortcut, ace-high with a gap':
        ('Straight', (1, 2, 3, 4, 5), 320),
    'shortcut, a two-rank gap breaks it':
        ('High Card', (5,), 13),
    'shortcut does not wrap round the ace':
        ('High Card', (3,), 16),
    'four fingers and shortcut, a gapped straight flush':
        ('Straight Flush', (1, 2, 3, 4, 5), 1080),
    'four fingers and shortcut, a pair inside a gapped run':
        ('Straight', (1, 2, 3, 4, 5), 216),
    'four fingers and shortcut, the card past the gap joins':
        ('Straight', (1, 2, 3, 4, 5), 204),
    'four fingers and shortcut do not wrap round the ace':
        ('High Card', (3,), 16),
    'ace high':
        ('Straight', (1, 2, 3, 4, 5), 324),
    'ace low':
        ('Straight', (1, 2, 3, 4, 5), 220),
    'no wrap round the ace':
        ('High Card', (3,), 16),
    'a wild card completes a flush':
        ('Flush', (1, 2, 3, 4, 5), 280),
    'a wild card completes a straight flush':
        ('Straight Flush', (1, 2, 3, 4, 5), 1080),
    'four fingers, four wilds and a club':
        ('Flush', (1, 2, 3, 4), 240),
    'four fingers, three wilds, a diamond and a club':
        ('Flush', (1, 2, 3, 5), 240),
    'smeared, a red straight flush':
        ('Straight Flush', (1, 2, 3, 4, 5), 1080),
    'smeared and four fingers, a black four inside a straight':
        ('Straight Flush', (1, 2, 3, 4, 5), 1080),
    'smeared and four fingers, three wilds, a spade and a heart':
        ('Flush', (1, 2, 3, 4), 240),
    'smeared, a wild card in a straight flush':
        ('Straight Flush', (1, 2, 3, 4, 5), 1080),
    'four fingers, a stone card beside a run of four':
        ('Straight', (1, 2, 3, 4, 5), 376),
    'a stone card breaks a straight':
        ('High Card', (4, 5), 63),
    'a stone card is no suit':
        ('High Card', (4, 5), 65),
    'four fingers, a stone card beside a flush of four':
        ('Flush', (1, 2, 3, 4, 5), 440),
    'the club, a debuffed straight flush':
        ('Straight Flush', (1, 2, 3, 4, 5), 800),
    'the goad, a debuffed flush':
        ('Flush', (1, 2, 3, 4, 5), 140),
    'the club, one debuffed card in a straight':
        ('Straight', (1, 2, 3, 4, 5), 228),
    'the club debuffs a wild card, which keeps only its printed suit':
        ('High Card', (5,), 5),
    "the club debuffs a wild card printed in the flush's suit":
        ('Flush', (1, 2, 3, 4, 5), 240),
    'smeared under the club debuffs spades and the flush holds':
        ('Flush', (1, 2, 3, 4, 5), 140),
    'the goad, four fingers, a debuffed pair inside the run':
        ('Straight', (1, 2, 3, 4, 5), 212),
    'splash and four fingers score the kicker':
        ('Straight', (1, 2, 3, 4, 5), 248),
}


def simulator_answer(play: Play):
    game = GameState(seed=0)
    game.jokers = [make(_JOKERS[key]) for key in play.jokers]
    if play.boss:
        effect = next(b for b in BOSSES if b.name == _BOSSES[play.boss])
        game.blind = Blind(BlindKind.BOSS, ante=1, target=999999999,
                           reward=5, boss=effect)
    played = play.sim_cards()
    held = [Card(_RANKS[c[2]], _SUITS[c[0]])
            for c in play.hand_codes[len(played):]]
    game.hand = played + held
    game.full_deck = list(game.hand)
    game._apply_debuffs()
    result = game.evaluate_selection(played)
    position = {c.uid: i for i, c in enumerate(played, start=1)}
    scoring = tuple(sorted(position[c.uid] for c in result.scoring))
    return (result.hand.label, scoring,
            score_hand(game, result, played, held).score)


@pytest.mark.parametrize("play", PLAYS, ids=lambda p: p.name)
def test_the_simulator_matches_the_engine(play):
    assert simulator_answer(play) == ENGINE[play.name]


# More than five cards: no flush and no straight at all (531, 551). Nothing
# plays six cards, but evaluate_poker_hand is handed whatever it is given,
# and so is evaluate. The engine's table for each, strongest hand first.
WIDE = [
    ("six hearts in a row", "H_2 H_3 H_4 H_5 H_6 H_7", (), "High Card"),
    ("six hearts in a row, four fingers", "H_2 H_3 H_4 H_5 H_6 H_7", (FF,),
     "High Card"),
    ("eight hearts in a row, four fingers and shortcut",
     "H_2 H_3 H_4 H_5 H_6 H_7 H_8 H_9", (FF, SC), "High Card"),
    # The same five cards are all three, so the branch is what is tested.
    ("five hearts in a row", "H_2 H_3 H_4 H_5 H_6", (),
     "Straight Flush|Flush|Straight|High Card"),
]


@pytest.mark.parametrize("name, cards, jokers, contains", WIDE,
                         ids=[w[0] for w in WIDE])
def test_more_than_five_cards_hold_no_flush_or_straight(name, cards, jokers,
                                                        contains):
    result = evaluate(Play(name, cards).sim_cards(),
                      four_fingers=FF in jokers, shortcut=SC in jokers)
    assert "|".join(h.label for h in HANDLIST
                    if h in result.contains) == contains


# -- the engine --------------------------------------------------------------

# evaluate_play highlights each card of the final scoring_hand -- after Splash
# and the Stone cards are folded in -- with highlight_card(card, _, 'up')
# (state_events.lua:602-605). Wrapping that reads the game's own answer
# rather than a re-derivation of it.
_PROBE = (
    "if not PROBE_HIGHLIGHT then "
    "  PROBE_HIGHLIGHT = highlight_card; "
    "  highlight_card = function(card, percent, dir) "
    "    if dir == 'up' then "
    "      PROBE_SCORING[#PROBE_SCORING + 1] = card.probe_position end "
    "    return PROBE_HIGHLIGHT(card, percent, dir) end "
    "end; "
    "PROBE_SCORING = {}; "
    "for i = 1, %d do G.hand.cards[i].probe_position = i end")


@pytest.fixture(scope="module")
def engine():
    from jimbot_sim.headless.runtime import HeadlessBalatro
    return HeadlessBalatro().boot()


def engine_answer(engine, play: Play):
    from jimbot_sim.headless.scenario import Scenario
    scene = Scenario(engine).start()
    # Jokers first: set_base re-runs the boss's debuff_card (card.lua:143),
    # and Smeared Joker has to be there for that to read spades as clubs.
    if play.jokers:
        scene.jokers(" ".join(play.jokers))
    if play.boss:
        scene.boss(play.boss)
    scene.hand(play.hand_codes)
    for i, (_, enhancement) in enumerate(play.specs, start=1):
        if enhancement:
            scene.enhance(i, enhancement="m_" + enhancement)
    count = len(play.specs)
    scene.select(range(1, count + 1))
    engine.execute(_PROBE % count)
    score = scene.play()
    hand = engine.eval("G.GAME.last_hand_played")
    scoring = engine.eval("table.concat(PROBE_SCORING, ',')")
    return (hand, tuple(sorted(int(i) for i in scoring.split(","))), score)


@pytest.mark.slow
@pytest.mark.parametrize("play", PLAYS, ids=lambda p: p.name)
def test_the_engine_still_says_so(engine, play):
    answer = engine_answer(engine, play)
    assert ENGINE[play.name] == answer, (
        "%s: recorded %s, engine %s" % (play.name, ENGINE[play.name], answer))
    assert simulator_answer(play) == answer


@pytest.mark.slow
@pytest.mark.parametrize("name, cards, jokers, contains", WIDE,
                         ids=[w[0] for w in WIDE])
def test_the_engine_agrees_about_wide_hands(engine, name, cards, jokers,
                                            contains):
    from jimbot_sim.headless.scenario import Scenario
    scene = Scenario(engine).start()
    if jokers:
        scene.jokers(" ".join(jokers))
    codes = cards.split()
    scene.hand(codes)
    labels = "{" + ",".join('"%s"' % h.label for h in HANDLIST) + "}"
    got = engine.eval(
        "(function() local h = {} "
        "for i = 1, %d do h[i] = G.hand.cards[i] end "
        "local r = evaluate_poker_hand(h) local t = {} "
        "for _, k in ipairs(%s) do if next(r[k]) then t[#t+1] = k end end "
        "return table.concat(t, '|') end)()" % (len(codes), labels))
    assert got == contains
