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
from balatro.cards import Card, Enhancement, Rank, Suit, standard_deck
from balatro.game import GameState
from balatro.hands import HandType, evaluate
from balatro.jokers import REGISTRY, make
from balatro.rng import RunRng
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
    hands_left: int = -1
    boss: str = ""
    gold_cards: tuple = ()
    # Lua run before the hand. Used to put an RNG pool somewhere its next draw
    # actually fires: a 1-in-4 that never comes up on any fixture leaves the
    # joker unobserved, and now that the pools are replicated the state that
    # makes it fire can be worked out here and set on both sides at once.
    lua_setup: str = ""

    @property
    def codes(self):
        return self.hand.split()

    def _at(self, index):
        card = _card(self.codes[index - 1])
        if index in self.gold_cards:
            card.enhancement = Enhancement.GOLD
        return card

    @property
    def played(self):
        return [self._at(i) for i in self.play]

    @property
    def held(self):
        return [self._at(i) for i in range(1, len(self.codes) + 1)
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
    Case("final hand of the round", "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4", (1, 2),
         hands_left=1),
    Case("queens held", "S_9 H_9 D_Q C_Q H_Q S_3 D_4 C_5", (1, 2)),
    # Four eights, for the jokers that roll a chance per played 8.
    Case("eights", "S_8 H_8 D_8 C_8 H_2 S_3 D_4 C_5", (1, 2, 3, 4)),
    # Pool states chosen so the next draw lands under the threshold, found
    # with the replicated generator in balatro.rng.
    Case("eights with a winning roll", "S_8 H_8 D_8 C_8 H_2 S_3 D_4 C_5",
         (1, 2, 3, 4),
         lua_setup='G.GAME.pseudorandom["8ball"] = 1.5e-05'),
    Case("a hand upgrade that lands", "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4",
         (1, 2), lua_setup='G.GAME.pseudorandom["space"] = 1.5e-05'),
    Case("gold and diamonds", "D_K D_Q D_2 C_5 H_7 S_9 D_3 C_4", (1, 2, 3),
         gold_cards=(1, 2)),
]

KNOWN_BAD = {
    # Every one of these draws from a game RNG pool whose state this harness
    # does not synchronise, so the two engines roll different numbers while
    # agreeing about the rule. They are the same blocker as the lucky cards in
    # test_retriggers.py, and all of them come good once pools are shared.
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
    "Madness": "tests/test_scaling.py, grows on Blind selection",
    "Red Card": "tests/test_scaling.py, grows on skipped Booster Packs",
    "Pareidolia": "tests/test_retriggers.py, needs a joker that reads faces",
    # Scaling jokers contribute nothing until they have grown, so they are
    # measured at a grown value in tests/test_scaling.py instead. Leaving them
    # to pass here would count "did nothing, twice" as verification.
    "Campfire": "tests/test_scaling.py, grows on cards sold",
    "Castle": "tests/test_scaling.py, grows on discarded cards of a suit",
    "Ceremonial Dagger": "tests/test_scaling.py, grows on destroyed jokers",
    "Constellation": "tests/test_scaling.py, grows on Planet cards used",
    "Flash Card": "tests/test_scaling.py, grows on shop rerolls",
    "Glass Joker": "tests/test_scaling.py, grows on Glass cards destroyed",
    "Hit the Road": "tests/test_scaling.py, grows on Jacks discarded",
    "Lucky Cat": "tests/test_scaling.py, grows on Lucky triggers",
    "Obelisk": "tests/test_scaling.py, needs another hand played more often",
    "Throwback": "tests/test_scaling.py, needs Blinds skipped",
    "Baron": "tests/test_retriggers.py, needs kings held in hand",
    "Blueprint": "tests/test_retriggers.py, needs a neighbour to copy",
    "Brainstorm": "tests/test_retriggers.py, needs a leftmost joker to copy",
    "Dusk": "tests/test_retriggers.py, needs the final hand of the round",
    "Mime": "tests/test_retriggers.py, needs abilities held in hand",
    "Swashbuckler": "tests/test_retriggers.py, needs other jokers to value",
}
NOT_A_SCORING_EFFECT = {
    # Structural jokers: they shape the run -- the shop, the round
    # boundary, the deck -- and move no chips or mult, so this file
    # cannot see them however many hands it plays. Listed one by one
    # rather than waved through as a group, so that anything wrongly
    # believed to be non-scoring has to be argued for individually.
    'Astronomer': 'makes shop Planets free',
    'Burglar': 'trades discards for hands when the Blind is selected',
    'Burnt Joker': 'upgrades the first discarded hand of a round',
    'Cartomancer': 'creates a Tarot when the Blind is selected',
    'Certificate': 'adds a sealed card to hand as the round begins',
    'Chaos the Clown': 'gives a free shop reroll',
    'Cloud 9': 'pays per 9 in the deck at the end of a round',
    'Credit Card': 'raises the debt limit',
    'DNA': 'copies a lone first-hand card into the deck',
    'Delayed Gratification': 'pays at the end of a round for unused discards',
    'Diet Cola': 'creates a Double Tag when sold',
    'Drunkard': 'grants an extra discard each round',
    'Egg': 'grows its own sell value at the end of a round',
    'Gift Card': 'adds sell value to other cards at the end of a round',
    'Hallucination': 'creates a Tarot when a Booster Pack is opened',
    'Invisible Joker': 'duplicates a Joker when sold after two rounds',
    'Juggler': 'changes hand size',
    'Luchador': 'disables the Boss Blind when sold',
    'Mail-In Rebate': 'pays on a discard, not on a played hand',
    'Marble Joker': 'adds a Stone card when the Blind is selected',
    'Merry Andy': 'changes hand size and discards',
    'Mr. Bones': 'prevents a loss instead of scoring',
    'Riff-Raff': 'creates Jokers when the Blind is selected',
    'Rocket': 'pays at the end of a round',
    'Satellite': 'pays per unique Planet used at the end of a round',
    'Showman': 'lets duplicates appear in the shop',
    'Sixth Sense': 'destroys a lone first-hand 6 and makes a Spectral',
    'To the Moon': 'adds interest at the end of a round',
    'Trading Card': 'pays on a lone first discard',
    'Troubadour': 'changes hand size and hands',
    'Turtle Bean': 'changes hand size',

    "Driver's License": "needs 16 enhanced cards in the deck, which no "
                        "scenario here builds",
    "Erosion": "needs cards missing from a 52-card deck",
    "Fortune Teller": "counts Tarot cards used across the run",
    "Hiker": "adds chips to a card permanently, visible only on a later hand",
    "Stone Joker": "needs Stone cards in the deck; the game will not even "
                   "offer it without one",
    "Baseball Card": "multiplies per uncommon joker owned, not per hand",
    "Canio": "grows when a face card is destroyed",
    "Card Sharp": "needs the same hand played earlier this round",
    "Faceless Joker": "pays money on a discard",
    "Golden Joker": "pays money at the end of a round",
    "Hologram": "grows as playing cards are added to the deck",
    "Midas Mask": "turns played face cards Gold, which pays at the end of a "
                  "round rather than on the hand",
    "Oops! All 6s": "doubles listed probabilities, so it needs both a chance "
                    "joker and synchronised RNG pools to show anything",
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
        # The game spends the hand before the jokers score it, so a joker
        # that asks "is this the last hand?" -- Acrobat, Dusk -- sees one
        # fewer than the state read here. Syncing the pre-play number left
        # both engines agreeing that it was never the final hand.
        "hands_left": read("G.GAME.current_round.hands_left") - 1,
        "money": read("G.GAME.dollars"),
        "draw_pile": read("#G.deck.cards"),
        # The game counts the hand before the jokers score it, so Supernova
        # sees 1 on its first Pair. GameState._play does the same.
        "plays": read('G.GAME.hands["%s"].played' % label) + 1,
        "hands_played": read("G.GAME.hands_played"),
        "boss": engine.eval(
            "(function() return tostring(G.GAME.blind.name) end)()"),
        # The game picks a card and a suit fresh each round, and The Idol,
        # Ancient Joker, Castle and Mail-In Rebate all read them off the
        # round. A simulator left at None simply never fires and looks like a
        # rule bug.
        "idol_rank": engine.eval(
            "(function() return tostring("
            "G.GAME.current_round.idol_card.rank) end)()"),
        "idol_suit": engine.eval(
            "(function() return tostring("
            "G.GAME.current_round.idol_card.suit) end)()"),
        "ancient_suit": engine.eval(
            "(function() return tostring("
            "G.GAME.current_round.ancient_card.suit) end)()"),
        # The whole pseudorandom table: the run's seed and every pool's
        # current state. A joker that rolls a chance draws from the pool named
        # after it, so matching the rule is not enough -- the stream has to be
        # at the same place, or the two engines roll different numbers while
        # agreeing about everything else.
        "rng_seed": engine.eval(
            "(function() return tostring(G.GAME.pseudorandom.seed) end)()"),
        "rng_pools": engine.eval(
            "(function() local t = {} "
            "for k, v in pairs(G.GAME.pseudorandom) do "
            "  if type(v) == 'number' then "
            "    t[#t+1] = k .. '=' .. string.format('%.17g', v) end "
            "end return table.concat(t, ' ') end)()"),
        "todo_hand": engine.eval(
            "(function() return tostring(G.jokers.cards[1] and "
            "G.jokers.cards[1].ability.to_do_poker_hand or '') end)()"),
        "castle_suit": engine.eval(
            "(function() return tostring("
            "G.GAME.current_round.castle_card.suit) end)()"),
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
    if case.hands_left >= 0:
        engine.execute("G.GAME.current_round.hands_left = %d"
                       % case.hands_left)
    for index in case.gold_cards:
        scene.enhance(index, enhancement="m_gold")
    if case.lua_setup:
        engine.execute(case.lua_setup)
    engine.execute("api.pump(30)")
    # Pin the targets the game rerolls each round, so a joker that names a
    # card or a hand actually fires on these fixtures instead of waiting for a
    # roll that never comes. The Idol reads the round; To Do List keeps its
    # hand on the joker itself.
    engine.execute('G.GAME.current_round.idol_card = '
                   '{rank = "Ace", suit = "Spades", id = 14}')
    engine.execute('if G.jokers.cards[1] and '
                   'G.jokers.cards[1].ability.to_do_poker_hand then '
                   'G.jokers.cards[1].ability.to_do_poker_hand = "Pair" end')
    engine.execute("api.pump(20)")
    scene.select(case.play)
    state = _engine_state(engine)
    read_money = lambda: int(
        engine.eval("(function() return G.GAME.dollars end)()"))
    read_cons = lambda: int(
        engine.eval("(function() return #G.consumeables.cards end)()"))
    money_before, cons_before = read_money(), read_cons()
    score = scene.play()
    # Consumables created are the third thing a joker can do with a hand, and
    # the only one 8 Ball, Superposition, Séance and Vagabond do at all --
    # comparing score and money alone called them verified without ever
    # running them, exactly as it did Rough Gem before money was added.
    return (score, read_money() - money_before,
            read_cons() - cons_before), state


_BOSS_BY_NAME = {b.name: b for b in BOSSES + FINISHER_BOSSES}

# The game names ranks and suits its own way; map them once, loudly.
_RANK_BY_GAME_NAME = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR,
                      "5": Rank.FIVE, "6": Rank.SIX, "7": Rank.SEVEN,
                      "8": Rank.EIGHT, "9": Rank.NINE, "10": Rank.TEN,
                      "Jack": Rank.JACK, "Queen": Rank.QUEEN,
                      "King": Rank.KING, "Ace": Rank.ACE}
_SUIT_BY_GAME_NAME = {"Spades": Suit.SPADES, "Hearts": Suit.HEARTS,
                      "Diamonds": Suit.DIAMONDS, "Clubs": Suit.CLUBS}
_HAND_BY_GAME_NAME = {h.label: h for h in HandType}


def _sim_score(name, case, state):
    game = GameState(seed=0)
    # Put the simulator's pools where the engine's are, so a chance-based
    # joker draws the same number rather than merely the same distribution.
    game.rng = RunRng(state["rng_seed"])
    for entry in state["rng_pools"].split():
        key, _, value = entry.partition("=")
        if key not in ("hashed_seed",):
            game.rng.pools[key] = float(value)
    game.jokers = [make(name)] if name else []
    game.idol_rank = _RANK_BY_GAME_NAME.get(state["idol_rank"])
    game.idol_suit = _SUIT_BY_GAME_NAME.get(state["idol_suit"])
    game.ancient_suit = _SUIT_BY_GAME_NAME.get(state["ancient_suit"])
    game.castle_suit = _SUIT_BY_GAME_NAME.get(state["castle_suit"])
    game.todo_hand = _HAND_BY_GAME_NAME.get(state["todo_hand"])
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
    game.idol_rank = _RANK_BY_GAME_NAME.get(state["idol_rank"])
    game.idol_suit = _SUIT_BY_GAME_NAME.get(state["idol_suit"])
    game.ancient_suit = _SUIT_BY_GAME_NAME.get(state["ancient_suit"])
    game.castle_suit = _SUIT_BY_GAME_NAME.get(state["castle_suit"])
    game.todo_hand = _HAND_BY_GAME_NAME.get(state["todo_hand"])
    game.draw_pile = standard_deck()[:state["draw_pile"]]
    # _apply_debuffs walks full_deck, so the cards under test have to be in it
    # or a boss that debuffs a suit silently debuffs nothing.
    game.full_deck = game.hand + game.draw_pile
    game._apply_debuffs()
    result = evaluate(played, splash=game._splash(),
                      smeared=game.has_smeared(),
                      four_fingers=game._four_fingers(),
                      shortcut=game._shortcut())
    game.hand_levels.plays[result.hand] = state["plays"]
    before = len(game.consumables)
    ctx = score_hand(game, result, played, held)
    for joker in list(game.jokers):
        if joker.spec.after_hand is not None:
            joker.spec.after_hand(joker, ctx)
    # Rough Gem and Golden Ticket change no score at all -- they pay money --
    # and the creation jokers change neither.
    return ctx.score, ctx.money_gained, len(game.consumables) - before


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
            "%s on %r: engine %s, simulator %s (score, money, consumables)"
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
