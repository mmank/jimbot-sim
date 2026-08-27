"""Fidelity tests against the real game's own Lua.

These run Balatro's actual code, so they need the game installed. Every expected
value here is one a player can verify by hand -- if the engine ever disagrees
with these, the harness is wrong, not the game.
"""

import pytest

pytest.importorskip("lupa")

from balatro_headless.runtime import DEFAULT_INSTALL, HeadlessBalatro  # noqa: E402

pytestmark = pytest.mark.skipif(
    not DEFAULT_INSTALL.exists(),
    reason=f"Balatro not installed at {DEFAULT_INSTALL}",
)

SEED = "ABCDEFGH"


@pytest.fixture(scope="module")
def booted():
    """Booting is ~100ms, so share one runtime where the test allows it."""
    return HeadlessBalatro().boot()


def start(jokers=(), seed=SEED):
    game = HeadlessBalatro().boot()
    game.execute(f"G:start_run({{seed = '{seed}'}})")
    for joker in jokers:
        game.execute(f'add_joker("{joker}")')
    game.execute("api.select_blind()")
    return game


# ---------------------------------------------------------------- boot

def test_boots_headless(booted):
    assert booted.eval("G ~= nil")
    assert booted.eval("G.FUNCS ~= nil")


def test_full_content_is_loaded(booted):
    def count(kind):
        return booted.eval(
            "(function() local n=0 for _,v in pairs(G.P_CENTERS) do"
            f" if v.set=='{kind}' then n=n+1 end end return n end)()")

    # The whole point of running the real Lua: nothing is a subset.
    assert count("Joker") == 150
    assert count("Tarot") == 22
    assert count("Planet") == 12
    assert count("Spectral") == 18
    assert count("Voucher") == 32
    assert booted.eval("(function() local n=0 for _ in pairs(G.P_BLINDS) do n=n+1 end return n end)()") == 30
    assert booted.eval("(function() local n=0 for _ in pairs(G.P_TAGS) do n=n+1 end return n end)()") == 24


def test_run_starts_with_expected_state():
    game = HeadlessBalatro().boot()
    game.execute(f"G:start_run({{seed = '{SEED}'}})")
    assert game.eval("G.GAME.pseudorandom.seed") == SEED
    assert game.eval("G.GAME.dollars") == 4
    assert game.eval("G.GAME.round_resets.ante") == 1
    assert game.eval("#G.playing_cards") == 52


def test_seed_is_deterministic():
    def hand_of(seed):
        game = start(seed=seed)
        return game.eval("(function() local t={} for _,c in ipairs(G.hand.cards) do"
                         " t[#t+1]=c.base.value..c.base.suit end return table.concat(t,',') end)()")

    assert hand_of("ZZZ11111") == hand_of("ZZZ11111")
    assert hand_of("ZZZ11111") != hand_of("YYY22222")


# ---------------------------------------------------------------- dealing

def test_blind_select_deals_a_hand():
    game = start()
    assert game.eval("G.STATE") == game.eval("G.STATES.SELECTING_HAND")
    assert game.eval("#G.hand.cards") == 8
    assert game.eval("G.GAME.blind.chips") == 300
    assert game.eval("G.GAME.current_round.hands_left") == 4
    # Red Deck's +1 discard, straight from the game's own deck definition.
    assert game.eval("G.GAME.current_round.discards_left") == 4


def test_discard_draws_replacements():
    game = start()
    before = game.eval("G.GAME.current_round.discards_left")
    game.execute("api.discard({1,2})")
    assert game.eval("G.GAME.current_round.discards_left") == before - 1
    assert game.eval("#G.hand.cards") == 8


# ---------------------------------------------------------------- scoring

def test_two_pair_scores_by_hand():
    # seed ABCDEFGH opens 8H 8D 7S 7H at indices 2-5.
    # Two Pair is 20 chips x 2 mult; cards add 8+8+7+7 = 30. (20+30) * 2 = 100.
    game = start()
    assert game.eval("api.play({2,3,4,5})") == 100


@pytest.mark.parametrize("jokers, expected, why", [
    ((), 100, "50 chips x 2 mult"),
    (("j_joker",), 300, "+4 mult: 50 x 6"),
    (("j_duo",), 200, "X2 mult: 50 x 4"),
    (("j_joker", "j_duo"), 600, "+4 then X2: 50 x ((2+4)*2)"),
    (("j_duo", "j_joker"), 400, "X2 then +4: 50 x ((2*2)+4)"),
    (("j_blueprint", "j_duo"), 400, "Blueprint copies The Duo: 50 x (2*2*2)"),
])
def test_joker_scoring_matches_hand_calculation(jokers, expected, why):
    assert start(jokers).eval("api.play({2,3,4,5})") == expected, why


def test_joker_slot_order_changes_the_score():
    """XMult does not commute with +Mult -- the classic Balatro gotcha."""
    assert start(("j_joker", "j_duo")).eval("api.play({2,3,4,5})") != \
           start(("j_duo", "j_joker")).eval("api.play({2,3,4,5})")


def test_beating_a_blind_advances_the_round():
    game = start(("j_baseball", "j_duo", "j_trio"))
    for _ in range(4):
        if game.eval("G.STATE") != game.eval("G.STATES.SELECTING_HAND"):
            break
        count = min(5, game.eval("#G.hand.cards"))
        game.execute("api.play({%s})" % ",".join(str(i) for i in range(1, count + 1)))
    assert game.eval("G.GAME.chips") >= game.eval("G.GAME.blind.chips")
    assert game.eval("G.STATE") != game.eval("G.STATES.GAME_OVER")
