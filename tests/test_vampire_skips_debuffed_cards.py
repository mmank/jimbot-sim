"""Vampire and Midas Mask leave a debuffed card alone.

Vampire walks the scoring hand under context.before (card.lua:3465-3480):

    if v.config.center ~= G.P_CENTERS.c_base and not v.debuff
       and not v.vampired then
        enhanced[#enhanced+1] = v
        v:set_ability(G.P_CENTERS.c_base, nil, true)

and Midas Mask, just above it, asks `v:is_face()` (card.lua:3443-3448), which
answers nothing for a debuffed card unless a boss is asking
(card.lua:964-965). A debuffed card can be in the scoring hand -- a Flush or a
Pair reads its printed suit and rank -- and it keeps its enhancement: the
Vampire takes X0.1 only for the cards it did strip, and Midas Mask gilds only
the face cards that are not debuffed. The simulator changed every one.

NQ86453Q (Checkered Deck, stake 1) stopped on it at decision 114, the fix for
decision 78 having let it get that far: a Flush into boss blind 22 with an
enhanced Queen of Spades that the boss had debuffed. The game's Vampire stayed
X1.2 and the shadow's went to X1.3, 18068 chips against 18445.
"""

import pytest

from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.hands import evaluate
from jimbot_sim.jokers import make
from jimbot_sim.scoring import score_hand

# A Two Pair: a Bonus King of Spades and a King of Hearts, both debuffed as The
# Plant debuffs them, and a Mult Two of Diamonds and a Two of Clubs that are
# not. Measured on the engine below. Under a Vampire only the Two is
# stripped, the Vampire is X1.1, and the hand scores 24 chips x 2.2. Under a
# Midas Mask neither King turns Gold, and it scores 24 x (2 + 4).
WANT = {
    "Vampire": {"score": 52, "king": Enhancement.BONUS,
                "other_king": Enhancement.NONE, "two": Enhancement.NONE},
    "Midas Mask": {"score": 144, "king": Enhancement.BONUS,
                   "other_king": Enhancement.NONE, "two": Enhancement.MULT},
}
KEYS = {"Vampire": "j_vampire", "Midas Mask": "j_midas_mask"}
CENTERS = {Enhancement.NONE: "c_base", Enhancement.BONUS: "m_bonus",
           Enhancement.MULT: "m_mult", Enhancement.GOLD: "m_gold"}
VAMPIRE_XMULT = 1.1


def _sim(joker):
    cards = {
        "king": Card(Rank.KING, Suit.SPADES, enhancement=Enhancement.BONUS,
                     debuffed=True),
        "other_king": Card(Rank.KING, Suit.HEARTS, debuffed=True),
        "two": Card(Rank.TWO, Suit.DIAMONDS, enhancement=Enhancement.MULT),
        "other_two": Card(Rank.TWO, Suit.CLUBS),
    }
    played = list(cards.values())
    held = [Card(Rank.SEVEN, Suit.HEARTS), Card(Rank.NINE, Suit.SPADES),
            Card(Rank.THREE, Suit.DIAMONDS), Card(Rank.FOUR, Suit.CLUBS)]
    game = GameState(seed=0)
    instance = make(joker)
    game.jokers = [instance]
    game.hand = played + held
    game.full_deck = list(game.hand)
    result = evaluate(played, splash=game._splash(),
                      smeared=game.has_smeared(),
                      four_fingers=game._four_fingers(),
                      shortcut=game._shortcut())
    ctx = score_hand(game, result, played, held)
    return ctx, instance, cards


@pytest.mark.parametrize("joker", sorted(WANT))
def test_a_debuffed_card_keeps_its_enhancement(joker):
    ctx, instance, cards = _sim(joker)
    want = WANT[joker]
    assert {k: cards[k].enhancement for k in ("king", "other_king", "two")} \
        == {k: want[k] for k in ("king", "other_king", "two")}
    assert ctx.score == want["score"], "\n".join(ctx.log)
    if joker == "Vampire":
        assert instance.counter == pytest.approx(VAMPIRE_XMULT)


# -- measured on the engine ---------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    pytest.importorskip("lupa")
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
    if not engine_available():
        pytest.skip("no Balatro engine: need vendor/balatro_src")
    return HeadlessBalatro().boot()


@pytest.mark.slow
@pytest.mark.parametrize("joker", sorted(WANT))
def test_under_the_plant_matches_the_engine(engine, joker):
    from jimbot_sim.headless.scenario import Scenario

    scene = (Scenario(engine).start().jokers(KEYS[joker])
             .hand("S_K H_K D_2 C_2 H_7 S_9 D_3 C_4").boss("bl_plant")
             .enhance(1, "m_bonus").enhance(3, "m_mult"))
    engine.execute("G.hand.cards[1].bot_probe = 'king'; "
                   "G.hand.cards[2].bot_probe = 'other_king'; "
                   "G.hand.cards[3].bot_probe = 'two'")
    debuffs = str(engine.eval(
        "(function() local t = {} for i = 1, 4 do "
        "t[#t+1] = G.hand.cards[i].debuff and '1' or '0' end "
        "return table.concat(t) end)()"))
    assert debuffs == "1100"
    got = scene.play([1, 2, 3, 4])
    centers = dict(item.split("=") for item in str(engine.eval(
        "(function() local t = {} for _, c in ipairs(G.playing_cards) do "
        "if c.bot_probe then t[#t+1] = c.bot_probe .. '=' .. c.config.center.key "
        "end c.bot_probe = nil end return table.concat(t, ' ') end)()")).split())
    want = WANT[joker]
    assert got == want["score"]
    assert centers == {k: CENTERS[want[k]] for k in ("king", "other_king", "two")}

    ctx, instance, cards = _sim(joker)
    assert ctx.score == got
    assert {k: CENTERS[cards[k].enhancement] for k in centers} == centers
    if joker == "Vampire":
        xmult = float(engine.eval(
            "(function() return G.jokers.cards[1].ability.x_mult end)()"))
        assert xmult == pytest.approx(VAMPIRE_XMULT)
        assert instance.counter == pytest.approx(xmult)
