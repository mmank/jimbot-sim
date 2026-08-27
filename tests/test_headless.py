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


# ---------------------------------------------------------------- unlocks

def test_unlock_all_opens_the_full_pool():
    """A fresh profile locks 45 jokers, which would train on a smaller game."""
    locked = HeadlessBalatro(unlock_all=False).boot()
    opened = HeadlessBalatro(unlock_all=True).boot()

    def usable(game):
        return game.eval("(function() local n=0 for _,v in pairs(G.P_CENTERS) do"
                         " if v.set=='Joker' and v.unlocked ~= false then n=n+1 end"
                         " end return n end)()")

    assert usable(locked) == 105
    assert usable(opened) == 150

    def rare_pool(game):
        game.execute("G:start_run({seed = 'ABCDEFGH'})")
        return game.eval("(function() local n=0 for _,v in "
                         "ipairs(get_current_pool('Joker',0.99)) do"
                         " if v ~= 'UNAVAILABLE' then n=n+1 end end return n end)()")

    # The gate is `unlocked ~= false` in get_current_pool, so this is the
    # shop's real distribution changing, not just a flag.
    assert rare_pool(locked) < rare_pool(opened)


def test_unlocking_reaches_the_live_shop_pool():
    game = HeadlessBalatro(unlock_all=True).boot()
    game.execute("G:start_run({seed = 'ABCDEFGH'})")
    has_blueprint = game.eval(
        "(function() for _,v in ipairs(get_current_pool('Joker',0.99)) do"
        " if v=='j_blueprint' then return true end end return false end)()")
    assert has_blueprint


# ---------------------------------------------------------------- full run

def test_a_round_pays_out_and_opens_the_shop():
    """Beating a blind must cash out and stock a shop, not park on the screen."""
    game = start(("j_baseball", "j_duo", "j_trio"))
    before = game.eval("G.GAME.dollars")
    for _ in range(4):
        if game.eval("G.STATE") != game.eval("G.STATES.SELECTING_HAND"):
            break
        count = min(5, game.eval("#G.hand.cards"))
        game.execute("api.play({%s})" % ",".join(str(i) for i in range(1, count + 1)))
    game.execute("api.pump(200)")
    paid = game.eval("api.cash_out()")

    assert paid > before, "the blind reward was never paid"
    assert game.eval("G.STATE") == game.eval("G.STATES.SHOP")
    assert game.eval("api.shop_ready()"), "shop reached but never stocked"
    assert len(game.eval("api.shop_contents()")) > 0


def test_shop_purchase_costs_money_and_grants_the_card():
    game = start(("j_duo",))
    for _ in range(4):
        if game.eval("G.STATE") != game.eval("G.STATES.SELECTING_HAND"):
            break
        count = min(5, game.eval("#G.hand.cards"))
        game.execute("api.play({%s})" % ",".join(str(i) for i in range(1, count + 1)))
    game.execute("api.pump(200)")
    game.execute("api.cash_out()")

    items = [dict(i) for i in game.eval("api.shop_contents()").values()]
    joker = next((i for i in items if i["set"] == "Joker" and i["buyable"]), None)
    if joker is None:
        pytest.skip("this seed's first shop has no affordable joker")

    before_money = game.eval("G.GAME.dollars")
    before_jokers = game.eval("#G.jokers.cards")
    game.execute(f"api.buy('{joker['area']}', {joker['index']})")
    assert game.eval("G.GAME.dollars") == before_money - joker["cost"]
    assert game.eval("#G.jokers.cards") == before_jokers + 1


def test_leaving_the_shop_returns_to_blind_select():
    game = start(("j_duo",))
    for _ in range(4):
        if game.eval("G.STATE") != game.eval("G.STATES.SELECTING_HAND"):
            break
        count = min(5, game.eval("#G.hand.cards"))
        game.execute("api.play({%s})" % ",".join(str(i) for i in range(1, count + 1)))
    game.execute("api.pump(200)")
    game.execute("api.cash_out()")
    game.execute("api.leave_shop()")
    assert game.eval("G.STATE") == game.eval("G.STATES.BLIND_SELECT")
    assert game.eval("api.blind_on_deck()") == "Big"


def test_driver_plays_a_complete_run_without_hanging():
    from balatro_headless.policy import GreedyPolicy
    from balatro_headless.run import HeadlessRun

    run = HeadlessRun(seed="ABCDEFGH", game=HeadlessBalatro().boot())
    result = run.play_run(GreedyPolicy())
    assert result.stopped in ("won", "game over"), result.stopped
    assert result.decisions < run.max_decisions, "run hit the decision limit"
    assert result.ante >= 1


def test_a_stacked_run_beats_ante_8():
    """The win condition, end to end: all 8 antes and the finisher boss.

    Deliberately overpowered -- this tests that the driver can traverse and
    finish a whole run, not that the policy is any good.
    """
    from balatro_headless.policy import GreedyPolicy
    from balatro_headless.run import HeadlessRun

    game = HeadlessBalatro().boot()
    run = HeadlessRun(seed="ABCDEFGH", game=game)
    run.start()
    for joker in ("j_caino", "j_triboulet", "j_yorick", "j_chicot", "j_perkeo"):
        game.execute(f'add_joker("{joker}")')
    game.execute("for i=1,30 do for k in pairs(G.GAME.hands) do "
                 "level_up_hand(nil, k, true, 1) end end")
    game.execute("api.pump(60)")

    result = run.play_run(GreedyPolicy())
    assert result.won, f"stopped: {result.stopped} on ante {result.ante}"
    assert result.ante > 8
    assert game.eval("G.GAME.won") is True


# ---------------------------------------------------------------- action audit

def test_every_player_action_works():
    """Run the full action audit as a test.

    scripts/audit_actions.py is the single source of truth for "does every
    primitive actually do something". Each check asserts an observable
    consequence -- money moved, a level rose, a card changed -- because the
    game's button callbacks refuse invalid actions silently, so a call that
    merely returns without raising proves nothing.
    """
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    import audit_actions

    audit_actions.RESULTS.clear()
    for check_fn in audit_actions.CHECKS:
        check_fn()

    failures = [(name, detail) for name, ok, detail in audit_actions.RESULTS
                if not ok]
    assert not failures, "\n".join(f"{n}: {d}" for n, d in failures)
    assert len(audit_actions.RESULTS) >= 20


def test_joker_reordering_changes_the_score():
    """Order is strategy, not cosmetics: XMult after +Mult differs."""
    forward = start(("j_joker", "j_duo"))
    reverse = start(("j_duo", "j_joker"))
    a = forward.eval("api.play({2,3,4,5})")
    b = reverse.eval("api.play({2,3,4,5})")
    assert (a, b) == (600, 400), f"{a}, {b}"

    # Reordering at runtime must reproduce the other ordering exactly.
    moved = start(("j_duo", "j_joker"))
    moved.execute("api.reorder_jokers({2,1})")
    assert moved.eval("api.play({2,3,4,5})") == 600


def test_eternal_jokers_are_not_sellable():
    game = start(("j_joker", "j_duo"))
    game.execute("G.jokers.cards[2].ability.eternal = true")
    assert game.eval("api.can_sell('jokers', 1)") is True
    assert game.eval("api.can_sell('jokers', 2)") is False
    before = game.eval("#G.jokers.cards")
    with pytest.raises(Exception, match="cannot sell"):
        game.execute("api.sell('jokers', 2)")
    assert game.eval("#G.jokers.cards") == before


def test_booster_packs_open_with_contents():
    """Regression: Card:open gates emplacing its cards on the pack area having
    animated into view, so headless the pack stayed permanently empty."""
    game = start(("j_baseball", "j_duo", "j_trio"))
    for _ in range(6):
        if game.eval("G.STATE") != game.eval("G.STATES.SELECTING_HAND"):
            break
        count = min(5, game.eval("#G.hand.cards"))
        game.execute("api.play({%s})" % ",".join(str(i) for i in range(1, count + 1)))
    game.execute("api.pump(200)")
    game.execute("api.cash_out()")
    game.execute("ease_dollars(30, true)")

    items = [dict(i) for i in game.eval("api.shop_contents()").values()]
    pack = next((i for i in items if i["set"] == "Booster"), None)
    if pack is None:
        pytest.skip("no booster pack in this shop")
    game.execute(f"api.buy('{pack['area']}', {pack['index']})")
    assert game.eval("api.in_pack()"), "buying a pack did not open it"
    assert len(game.eval("api.pack_contents()")) > 0, "pack opened empty"
