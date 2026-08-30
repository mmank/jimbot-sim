"""Every joker the simulator claims, scored against the real engine.

The simulator had 31 hand-written tests, all passing, while jokers scored wrong
numbers. Hand-written tests check what the author already believed; only the
engine knows what the game does.

Agreement is only worth something when the joker actually did something. A
Greedy Joker and a broken Greedy Joker both score nothing on a hand with no
diamonds, so a single fixture would "verify" it while proving nothing. Hence
the scenario matrix, and the inertness check inside each test, which fails when
a joker never once moved the score away from its no-joker baseline.

Three failure modes to keep apart, all of which have already happened here:

  fixture mismatch   the sides scored different *situations*. Banner pays per
                     discard remaining, and the simulator started with none
                     while the engine had four. Run state is synchronised from
                     the engine rather than assumed.
  simulator bug      Ice Cream grew before scoring instead of after.
  oracle bug         Swashbuckler keeps its mult in Card:update, which headless
                     had disabled. The engine was wrong and the simulator right,
                     so a disagreement is not automatically the simulator's.
"""

from dataclasses import dataclass

import pytest

from balatro.blinds import BOSSES, FINISHER_BOSSES, Blind, BlindKind
from balatro.cards import Card, Rank, Suit, standard_deck
from balatro.game import GameState
from balatro.hands import evaluate
from balatro.jokers import REGISTRY, make
from balatro.scoring import score_hand
from balatro_headless.runtime import HeadlessBalatro
from balatro_headless.scenario import Scenario

_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT, "9": Rank.NINE,
          "T": Rank.TEN, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
          "A": Rank.ACE}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}


def _card(code):
    suit, rank = code.split("_")
    return Card(_RANKS[rank], _SUITS[suit])


@dataclass(frozen=True)
class Case:
    """One position, written once and handed to both engines.

    Defining the hand as codes and deriving the simulator's cards from the same
    string is deliberate: two hand-maintained lists drift, and a drifted
    fixture reads exactly like a fidelity bug.
    """
    name: str
    hand: str
    play: tuple
    # Run state to force before the hand is played. Several jokers only wake
    # up outside the default position -- Mystic Summit needs the discards
    # gone, Bootstraps needs money -- and a matrix that never leaves the
    # default "verifies" them while they sit inert.
    money: int = 0
    discards_left: int = -1
    boss: str = ""

    @property
    def codes(self):
        return self.hand.split()

    @property
    def played(self):
        return [_card(self.codes[i - 1]) for i in self.play]

    @property
    def held(self):
        return [_card(c) for i, c in enumerate(self.codes, 1)
                if i not in self.play]


CASES = [
    Case("pair of kings", "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4", (1, 2)),
    Case("flush of hearts", "H_2 H_4 H_6 H_8 H_T S_A D_3 C_5", (1, 2, 3, 4, 5)),
    Case("straight", "S_5 H_6 D_7 C_8 S_9 H_2 D_3 C_4", (1, 2, 3, 4, 5)),
    Case("straight flush", "S_A S_K S_Q S_J S_T H_2 D_3 C_4", (1, 2, 3, 4, 5)),
    Case("four sevens", "S_7 H_7 D_7 C_7 S_2 H_3 D_4 C_5", (1, 2, 3, 4)),
    Case("face cards", "S_K H_Q D_J C_K S_A H_2 D_3 C_4", (1, 2, 3, 4)),
    Case("low odds", "S_3 H_3 D_5 C_7 S_9 H_2 D_4 C_6", (1, 2)),
    Case("lone ace", "S_A H_2 D_4 C_6 S_8 H_T D_3 C_5", (1,)),
    Case("diamonds and clubs", "D_A D_9 C_9 C_2 H_5 S_6 D_3 C_4", (1, 2, 3, 4)),
    # Two pair: nothing above makes one, so every two-pair joker was inert.
    Case("two pair", "S_K H_K D_5 C_5 H_7 S_9 D_3 C_4", (1, 2, 3, 4)),
    # All held cards black, for the jokers that inspect what is *not* played.
    Case("black hand", "S_K C_K S_2 C_5 S_7 C_9 S_3 C_4", (1, 2)),
    Case("no discards left", "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4", (1, 2),
         discards_left=0),
    Case("rich", "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4", (1, 2), money=40),
    # Boss blinds change the rules of scoring itself, and nothing above ever
    # reaches one -- the run opens on a small blind. Without these, all
    # twenty-eight boss effects and the jokers that answer them went untested.
    Case("The Club debuffs clubs", "C_K C_Q D_2 C_5 H_7 S_9 D_3 C_4", (1, 2),
         boss="bl_club"),
    Case("The Goad debuffs spades", "S_K S_Q D_2 C_5 H_7 S_9 D_3 C_4", (1, 2),
         boss="bl_goad"),
    Case("The Flint halves the base", "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4", (1, 2),
         boss="bl_flint"),
    # Four spades with one-rank gaps: a High Card on its own, a Flush with
    # Four Fingers, a Straight with Shortcut, and a Straight Flush with both.
    Case("four to a gapped flush", "S_A S_3 C_5 S_7 S_9 H_2 D_4 C_6",
         (1, 2, 3, 4, 5)),
]

KNOWN_BAD = {
    "Misprint": "draws from the game's 'misprint' pool, whose state this "
                "harness does not yet synchronise -- see test_rng.py",
    "Hanging Chad": "on a five-card flush the engine scores 292 against the "
                    "simulator's 276. It agrees everywhere else, the straight "
                    "flush included, so this is not simply the wrong card "
                    "being retriggered, and is still open",
}

# Jokers that do nothing to the score of a single hand, so the matrix above
# cannot exercise them however many hands are added. Each says where it is
# actually covered, because "inert here" must not be allowed to read as
# "verified" -- that is exactly the trap the single-hand matrix fell into.
COVERED_ELSEWHERE = {
    "Baron": "tests/test_retriggers.py, needs kings held in hand",
    "Blueprint": "tests/test_retriggers.py, needs a neighbour to copy",
    "Brainstorm": "tests/test_retriggers.py, needs a leftmost joker to copy",
    "Dusk": "tests/test_retriggers.py, needs the final hand of the round",
    "Mime": "tests/test_retriggers.py, needs abilities held in hand",
    "Swashbuckler": "tests/test_retriggers.py, needs other jokers to value",
}
NOT_A_SCORING_EFFECT = {
    "Baseball Card": "multiplies per uncommon joker owned, not per hand",
    "Canio": "grows when a face card is destroyed",
    "Card Sharp": "needs the same hand played earlier this round",
    "Faceless Joker": "pays money on a discard",
    "Golden Joker": "pays money at the end of a round",
    "Hologram": "grows as playing cards are added to the deck",
    "Loyalty Card": "fires on every sixth hand; the matrix plays one",
    "Perkeo": "creates a Negative consumable when a shop is left",
    "Vampire": "grows by stripping enhancements from scored cards",
    "Yorick": "grows on discards",
}
NEEDS_SETUP = {"Steel Joker": "scales on steel cards in the deck; none here"}


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


@pytest.fixture(scope="module")
def joker_keys(engine):
    """The game's own key-to-name map, so nothing is hand-maintained."""
    raw = engine.eval(
        '(function()'
        '  local t = {}'
        '  for k, v in pairs(G.P_CENTERS) do'
        '    if v.set == "Joker" then'
        '      local loc = G.localization.descriptions.Joker[k]'
        '      if loc then t[#t+1] = k .. "=" .. loc.name end'
        '    end'
        '  end'
        '  return table.concat(t, "|") end)()')
    return {name: key for key, name in
            (pair.split("=", 1) for pair in raw.split("|"))}


def _engine_state(engine):
    read = lambda expr: int(engine.eval("(function() return %s end)()" % expr))
    label = engine.eval(
        "(function() return G.GAME.current_round.current_hand.handname end)()")
    # "Royal Flush" is a display name; the game keeps no counter under it and
    # scores the hand as a Straight Flush.
    if label == "Royal Flush":
        label = "Straight Flush"
    return {
        "discards_left": read("G.GAME.current_round.discards_left"),
        "hands_left": read("G.GAME.current_round.hands_left"),
        "money": read("G.GAME.dollars"),
        "draw_pile": read("#G.deck.cards"),
        # The game counts the hand before the jokers score it, so Supernova
        # sees 1 on its first Pair. GameState._play does the same.
        "plays": read('G.GAME.hands["%s"].played' % label) + 1,
        "hands_played": read("G.GAME.hands_played"),
        "boss": engine.eval(
            "(function() return tostring(G.GAME.blind.name) end)()"),
    }


def _engine_score(engine, case, key):
    scene = Scenario(engine).start()
    if case.boss:
        scene.boss(case.boss)
    scene.hand(case.hand)
    if key:
        scene = scene.jokers(key)
    # Select first: the game fills in current_hand only once cards are
    # highlighted, and the state has to be read before the play consumes it.
    if case.money:
        engine.execute("G.GAME.dollars = %d" % case.money)
    if case.discards_left >= 0:
        engine.execute("G.GAME.current_round.discards_left = %d"
                       % case.discards_left)
    engine.execute("api.pump(30)")
    scene.select(case.play)
    state = _engine_state(engine)
    return scene.play(), state


_BOSS_BY_NAME = {b.name: b for b in BOSSES + FINISHER_BOSSES}


def _sim_score(name, case, state):
    game = GameState(seed=0)
    game.jokers = [make(name)] if name else []
    if case.boss:
        # Match the engine on which boss is in play. The simulator resolves
        # Chicot itself, so handing it the blind rather than the effect keeps
        # the joker's own answer to the boss under test.
        effect = _BOSS_BY_NAME[state["boss"]]
        game.blind = Blind(BlindKind.BOSS, ante=1, target=999999999,
                           reward=5, boss=effect)
    played, held = case.played, case.held
    game.hand = played + held
    game.discards_left = state["discards_left"]
    game.hands_left = state["hands_left"]
    game.money = state["money"]
    game.hands_played = state["hands_played"]
    game.draw_pile = standard_deck()[:state["draw_pile"]]
    # _apply_debuffs walks full_deck, so the cards under test have to be in it
    # or a boss that debuffs a suit silently debuffs nothing.
    game.full_deck = game.hand + game.draw_pile
    game._apply_debuffs()
    result = evaluate(played, four_fingers=game._four_fingers(),
                      shortcut=game._shortcut())
    game.hand_levels.plays[result.hand] = state["plays"]
    return score_hand(game, result, played, held).score


@pytest.fixture(scope="module")
def baselines(engine):
    """What each position scores with no joker at all."""
    out = {}
    for case in CASES:
        score, state = _engine_score(engine, case, None)
        assert score == _sim_score(None, case, state), (
            "engines disagree on %r before any joker is involved" % case.name)
        out[case.name] = score
    return out


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_joker_scores_what_the_engine_scores(engine, joker_keys, baselines, name):
    if name in NEEDS_SETUP:
        pytest.skip(NEEDS_SETUP[name])
    if name in KNOWN_BAD:
        pytest.xfail(KNOWN_BAD[name])

    if name in COVERED_ELSEWHERE:
        pytest.skip("no scoring effect on a lone hand: %s"
                    % COVERED_ELSEWHERE[name])
    if name in NOT_A_SCORING_EFFECT:
        pytest.skip(NOT_A_SCORING_EFFECT[name])

    key = joker_keys[name]
    active = []
    for case in CASES:
        expected, state = _engine_score(engine, case, key)
        actual = _sim_score(name, case, state)
        assert expected == actual, (
            "%s on %r: engine %d, simulator %d"
            % (name, case.name, expected, actual))
        if expected != baselines[case.name]:
            active.append(case.name)
    assert active, (
        "%s never changed a score across %d hands, so agreeing with the engine "
        "proves nothing about it" % (name, len(CASES)))


def test_every_implemented_joker_is_checked(joker_keys):
    """A joker the engine cannot name is one this harness cannot verify."""
    unknown = sorted(set(REGISTRY) - set(joker_keys))
    assert not unknown, "not in the game's joker list: %s" % unknown


def test_nothing_is_excused_without_a_reason(joker_keys):
    """Every excused joker must name a real joker and give a reason.

    The excuse lists are the weak point of this file: an entry added to make a
    failure go away would silently drop a joker from verification. Requiring
    the name to exist keeps a typo from hiding one.
    """
    excused = (set(KNOWN_BAD) | set(NEEDS_SETUP) | set(COVERED_ELSEWHERE)
               | set(NOT_A_SCORING_EFFECT))
    assert excused <= set(joker_keys), (
        "excused a joker the game does not have: %s"
        % sorted(excused - set(joker_keys)))
    assert excused <= set(REGISTRY), (
        "excused a joker the simulator does not implement: %s"
        % sorted(excused - set(REGISTRY)))


def test_coverage_is_reported_honestly(joker_keys):
    unverified = (len(KNOWN_BAD) + len(NEEDS_SETUP)
                  + len(NOT_A_SCORING_EFFECT))
    print("\n  jokers in the game     %d"
          "\n  implemented            %d"
          "\n  agreeing on %d hands   %d"
          "\n  covered in other tests %d"
          "\n  still unverified       %d"
          % (len(joker_keys), len(REGISTRY), len(CASES),
             len(REGISTRY) - unverified - len(COVERED_ELSEWHERE),
             len(COVERED_ELSEWHERE), unverified))
    assert len(joker_keys) == 150
