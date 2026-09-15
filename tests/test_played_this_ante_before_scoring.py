"""A card is marked played this ante as it is played, before anything scores.

G.FUNCS.play_cards_from_highlighted sets the flag on each highlighted card as
it moves it to the play area (state_events.lua:478-483):

    G.hand.highlighted[i].ability.played_this_ante = true
    draw_card(G.hand, G.play, ...)

and only afterwards does evaluate_play run. So when Vampire strips an
enhancement under context.before (card.lua:3465-3480) or Midas Mask gilds a
face card (card.lua:3443-3456), both through Card:set_ability, the ability
table is rebuilt (card.lua:223-366) and the flag that had just been set goes
with it. The Pillar reads that flag (blind.lua:634), so a card Vampire ate is
not debuffed by it later in the ante. And a DNA copy, made under
context.before too (card.lua:3501), takes the whole ability table across
(common_events.lua:2161-2167), flag included.

The simulator set the flag after the hand had scored, so the enhancement
change cleared a flag that was not set yet, and the card came out marked.

NQ86453Q (Checkered Deck, stake 1) stopped on it at decision 78: a wild 8 of
Spades played into Vampire on the Big Blind, and a Flush with it into The
Pillar. The game scored the 8 -- 262 chips x 30.8 = 8069 -- and the shadow
debuffed it, 254 x 30.8 = 7823.
"""

import pytest

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Enhancement, Rank, Suit
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

PILLAR = next(b for b in BOSSES if b.name == "The Pillar")
KEYS = {"Vampire": "j_vampire", "Midas Mask": "j_midas_mask", "Joker": "j_joker"}

# (played this ante, debuffed once The Pillar is up) for a Bonus King of
# Spades and a plain King of Hearts played together, and a card left in hand.
# Measured on the engine.
WANT = {
    "Vampire": {"bonus king": (False, False), "plain king": (True, True),
                "unplayed": (False, False)},
    "Midas Mask": {"bonus king": (False, False), "plain king": (False, False),
                   "unplayed": (False, False)},
    "Joker": {"bonus king": (True, True), "plain king": (True, True),
              "unplayed": (False, False)},
}


def _sim_kings(joker):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS[joker]))
    game._start_round()
    bonus, plain, unplayed = game.hand[0], game.hand[1], game.hand[2]
    bonus.rank, bonus.suit = Rank.KING, Suit.SPADES
    game.set_enhancement(bonus, Enhancement.BONUS)
    plain.rank, plain.suit = Rank.KING, Suit.HEARTS
    unplayed.rank, unplayed.suit = Rank.TWO, Suit.DIAMONDS
    game.step(Action(ActionType.PLAY, cards=(0, 1)))
    cards = {"bonus king": bonus, "plain king": plain, "unplayed": unplayed}
    flags = {k: c.played_this_ante for k, c in cards.items()}
    game.blind = make_blind(BlindKind.BOSS, game.ante, PILLAR)
    game._apply_debuffs()
    return {k: (flags[k], c.debuffed) for k, c in cards.items()}


@pytest.mark.parametrize("joker", sorted(WANT))
def test_an_enhancement_changed_while_scoring_clears_the_flag(joker):
    assert _sim_kings(joker) == WANT[joker]


def test_a_dna_copy_is_already_played_this_ante():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["DNA"]))
    game._start_round()
    played = game.hand[0]
    before = len(game.full_deck)
    game.step(Action(ActionType.PLAY, cards=(0,)))
    assert len(game.full_deck) == before + 1
    copy = game.full_deck[-1]
    assert copy is not played
    assert copy.played_this_ante and played.played_this_ante


# -- measured on the engine ---------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    pytest.importorskip("lupa")
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
    if not engine_available():
        pytest.skip("no Balatro engine: need vendor/balatro_src")
    return HeadlessBalatro().boot()


_READ = ("(function() local out = {} for _, c in ipairs(G.playing_cards) do "
         "if c.bot_probe then out[#out+1] = c.bot_probe .. '=' .. "
         "(c.ability.played_this_ante and '1' or '0') .. "
         "(c.debuff and '1' or '0') end end "
         "return table.concat(out, ' ') end)()")


def _engine_flags(engine):
    out = {}
    for item in str(engine.eval(_READ)).split():
        name, bits = item.split("=")
        out[name] = bits
    return out


@pytest.mark.slow
@pytest.mark.parametrize("joker", sorted(WANT))
def test_the_flags_match_the_engine(engine, joker):
    from jimbot_sim.headless.scenario import Scenario

    scene = Scenario(engine).start().jokers(KEYS[joker]).hand(
        "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4").enhance(1, "m_bonus")
    engine.execute("G.hand.cards[1].bot_probe = 'bonus_king'; "
                   "G.hand.cards[2].bot_probe = 'plain_king'; "
                   "G.hand.cards[3].bot_probe = 'unplayed'")
    scene.play([1, 2])
    played = _engine_flags(engine)
    scene.boss("bl_pillar")
    pillar = _engine_flags(engine)
    engine.execute("for _, c in ipairs(G.playing_cards) do c.bot_probe = nil end")
    got = {name.replace("_", " "): (played[name][0] == "1",
                                    pillar[name][1] == "1")
           for name in played}
    assert got == WANT[joker]
    assert _sim_kings(joker) == got


@pytest.mark.slow
def test_a_dna_copy_matches_the_engine(engine):
    from jimbot_sim.headless.scenario import Scenario

    scene = Scenario(engine).start().jokers("j_dna").hand(
        "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4")
    before = int(engine.eval("#G.playing_cards"))
    scene.play([1])
    assert int(engine.eval("#G.playing_cards")) == before + 1
    assert engine.eval("(function() return G.playing_cards[#G.playing_cards]"
                       ".ability.played_this_ante and 1 or 0 end)()") == 1
