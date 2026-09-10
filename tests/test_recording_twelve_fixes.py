"""Two simulator bugs from recording 12, and neither is about scoring.

**Crimson Heart picks by age, not by position.** It debuffs one random
joker a hand, drawn with `pseudorandom_element(jokers,
pseudoseed('crimson_heart'))` (blind.lua:594) -- and that helper sorts the
table by `sort_id` before it indexes. This sorted by where the joker sat in
the row instead, which is not an order the game would ever draw from,
because a player drags jokers about. Marcin, on the run: *"I repositioned
the jokers at the time."* The game debuffed his Baron; this debuffed the
Turtle Bean four seats away, so one hand scored 14147 there and 42441 here
and the Turtle Bean's +4 hand size went with it -- 12 cards against 8.

**The five finisher bosses pay $8.** P_BLINDS in game.lua gives every one of
the twenty-three ordinary bosses `dollars = 5` and each of bl_final_acorn,
bl_final_bell, bl_final_heart, bl_final_leaf and bl_final_vessel
`dollars = 8`. One number for every boss made an ante-eight win three
dollars short, every time.
"""

from jimbot_sim.blinds import (BOSSES, FINISHER_BOSSES, BlindKind,
                               make_blind)
from jimbot_sim.game import GameState


def test_crimson_heart_ignores_the_row_order():
    """Reordering the row must not change who gets debuffed."""
    heart = next(b for b in FINISHER_BOSSES
                 if b.name == "Crimson Heart")

    def victim(reversed_row: bool) -> str:
        game = GameState(seed="TESTSEED", endless=True)
        game._start_round()
        game.blind = make_blind(BlindKind.BOSS, 8, heart)
        specs = ["Joker", "Baron", "Mime", "Blueprint"]
        for name in specs:
            game.gain_joker(_joker(name))
        if reversed_row:
            game.jokers.reverse()
        game._play((0, 1))
        return next((j.name for j in game.jokers if j.debuffed), "")

    assert victim(False) == victim(True) != ""


def _joker(name):
    from jimbot_sim.jokers import REGISTRY, JokerInstance

    return JokerInstance(REGISTRY[name])


def test_a_finisher_boss_pays_eight():
    for boss in list(BOSSES) + list(FINISHER_BOSSES):
        blind = make_blind(BlindKind.BOSS, 8, boss)
        want = 8 if boss.is_finisher else 5
        assert blind.reward == want, "%s pays %d, not %d" % (
            boss.name, blind.reward, want)


def test_the_ordinary_blinds_are_unchanged():
    assert make_blind(BlindKind.SMALL, 1).reward == 3
    assert make_blind(BlindKind.BIG, 1).reward == 4
    assert make_blind(BlindKind.SMALL, 1, no_reward=True).reward == 0
