"""The vouchers, audited the way the joker flags and the consumables were.

The roster came out clean: 32 present, costs and prerequisite chains all
matching, and the pool correctly blanks a voucher that is redeemed, offered,
or waiting on its base. What the audit was actually for is the part a roster
check cannot see -- whether each effect is combined the way the game combines
it, and whether it costs the same draws.

Two rules run through Card:apply_to_run and it is easy to apply the wrong one:

  set   an upgrade replaces its base's number. Tarot Tycoon's rate is 32, not
        32 on top of Merchant's 9.6; Liquidation's discount is 50, not 75.
  add   Grabber and Nacho Tong are +1 hand each, Paint Brush and Palette +1
        hand size each, Reroll Surplus and Glut -2 reroll cost each.

Telescope, Observatory, Omen Globe, Director's Cut and Retcon carry no field
at all: the game reads them out of G.GAME.used_vouchers where they are
needed, and so does the simulator.

Blank is the one that reads as a mistake and is not. Its card text is
"Does nothing?", question mark and all, and within a run that is true. It
still has two jobs. It is Antimatter's in-run prerequisite -- Antimatter is
the +1 joker slot -- which the requires chain covers. And redeeming it ten
times across a *profile* is what unlocks Antimatter in the first place,
counted in voucher_usage rather than in any run. Profile unlocks are outside
what this simulator models, so that half is out of scope rather than missing;
see test_every_voucher_either_carries_a_field_or_is_read_by_key.
"""

import pytest

from balatro.blinds import ante_base_chips
from balatro.game import GameState
from balatro.shop import VOUCHER_BY_KEY, VOUCHERS


def _run(*keys):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for key in keys:
        voucher = VOUCHER_BY_KEY[key]
        game.vouchers.append(voucher)
        game._redeem_voucher(voucher)
    return game


# ------------------------------------------------------------------
# set, not add
# ------------------------------------------------------------------

def test_an_upgrade_replaces_its_base_rate_rather_than_stacking():
    """G.GAME.tarot_rate = 4*extra, an assignment. Summing gives 41.6."""
    assert _run("v_tarot_merchant")._shop_rates()["Tarot"] == 9.6
    both = _run("v_tarot_merchant", "v_tarot_tycoon")
    assert both._shop_rates()["Tarot"] == 32


def test_the_planet_pair_behaves_the_same_way():
    both = _run("v_planet_merchant", "v_planet_tycoon")
    assert both._shop_rates()["Planet"] == 32


def test_liquidation_replaces_clearance_sale():
    assert _run("v_clearance_sale").discount_percent == 25
    assert _run("v_clearance_sale", "v_liquidation").discount_percent == 50


def test_glow_up_replaces_hone():
    assert _run("v_hone").edition_rate == 2
    assert _run("v_hone", "v_glow_up").edition_rate == 4


def test_money_tree_replaces_seed_money():
    """The cap is in five-dollar blocks: $50 is 10, $100 is 20."""
    assert _run("v_seed_money").interest_cap == 10
    assert _run("v_seed_money", "v_money_tree").interest_cap == 20


# ------------------------------------------------------------------
# add, not set
# ------------------------------------------------------------------

def test_the_hand_and_discard_pairs_stack():
    plain = GameState(seed="TESTSEED", deck="Red Deck")._round_allowance()
    both = _run("v_grabber", "v_nacho_tong")._round_allowance()
    assert both[0] == plain[0] + 2

    discards = _run("v_wasteful", "v_recyclomancy")._round_allowance()
    assert discards[1] == plain[1] + 2


def test_paint_brush_and_palette_stack():
    plain = GameState(seed="TESTSEED", deck="Red Deck").hand_size
    assert _run("v_paint_brush").hand_size == plain + 1
    assert _run("v_paint_brush", "v_palette").hand_size == plain + 2


def test_overstock_and_overstock_plus_stack():
    plain = GameState(seed="TESTSEED", deck="Red Deck")._shop_slot_count()
    assert _run("v_overstock_norm")._shop_slot_count() == plain + 1
    both = _run("v_overstock_norm", "v_overstock_plus")
    assert both._shop_slot_count() == plain + 2


def test_a_voucher_hands_over_its_extra_for_the_round_in_progress():
    """ease_hands_played fires on redemption, not only from the next round."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._start_round()
    before = game.hands_left
    voucher = VOUCHER_BY_KEY["v_grabber"]
    game.vouchers.append(voucher)
    game._redeem_voucher(voucher)
    assert game.hands_left == before + 1


# ------------------------------------------------------------------
# Hieroglyph and Petroglyph
# ------------------------------------------------------------------

def test_hieroglyph_costs_an_ante_and_a_hand():
    plain = GameState(seed="TESTSEED", deck="Red Deck")
    game = _run("v_hieroglyph")
    assert game.ante == plain.ante - 1
    assert game._round_allowance()[0] == plain._round_allowance()[0] - 1


def test_petroglyph_costs_an_ante_and_a_discard():
    plain = GameState(seed="TESTSEED", deck="Red Deck")
    game = _run("v_hieroglyph", "v_petroglyph")
    assert game.ante == plain.ante - 2
    assert game._round_allowance()[1] == plain._round_allowance()[1] - 1


def test_the_ante_really_does_go_below_one():
    """ease_ante is a bare addition with no floor.

    Measured: two Hieroglyphs from ante one leave the engine at minus one,
    and get_blind_amount returns 100 for anything under one. A clamp at one
    is not a safety net -- it asks 300 where the engine asks 100, and it
    names every ante-keyed pool wrongly, which moves the whole shop stream.
    """
    game = _run("v_hieroglyph")
    assert game.ante == 0
    game2 = _run("v_hieroglyph", "v_petroglyph")
    assert game2.ante == -1

    assert ante_base_chips(0) == 100
    assert ante_base_chips(-1) == 100
    assert ante_base_chips(1) == 300


# ------------------------------------------------------------------
# the pool
# ------------------------------------------------------------------

def test_an_upgrade_is_not_offered_before_its_base_is_redeemed():
    from balatro.shop_pool import UNAVAILABLE, build_voucher_pool

    pool = build_voucher_pool(redeemed=())
    keys = {v.key for v in VOUCHERS}
    gated = {v.key for v in VOUCHERS if v.requires}
    live = {k for k in pool if k != UNAVAILABLE}
    assert live == keys - gated

    with_base = build_voucher_pool(redeemed={"v_hieroglyph"})
    assert "v_petroglyph" in with_base
    assert "v_hieroglyph" not in with_base, "redeemed cannot come again"


def test_a_voucher_already_on_offer_is_withheld():
    """Which is what a Voucher Tag's second slot needs."""
    from balatro.shop_pool import build_voucher_pool

    assert "v_blank" in build_voucher_pool()
    assert "v_blank" not in build_voucher_pool(on_offer={"v_blank"})


def test_every_voucher_either_carries_a_field_or_is_read_by_key():
    """The declared-but-unread check, turned on the vouchers.

    Blank is the only one that legitimately does nothing. The other four
    without a field are read straight out of used_vouchers where they act --
    Telescope and Omen Globe in pack contents, Observatory in scoring,
    Director's Cut and Retcon at the boss reroll.
    """
    import dataclasses
    import pathlib

    read_by_key = {"v_telescope", "v_observatory", "v_omen_globe",
                   "v_directors_cut", "v_retcon"}
    package = pathlib.Path(__file__).resolve().parents[1] / "src" / "balatro"
    body = "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted(package.glob("*.py")))

    # Blank is allowed to be here. It does nothing to a run by design, and
    # its real jobs are elsewhere: gating Antimatter through the requires
    # chain, and counting toward Antimatter's profile unlock, which this
    # simulator does not model at all.
    inert = []
    for voucher in VOUCHERS:
        fields = [f.name for f in dataclasses.fields(voucher)
                  if f.name not in ("key", "name", "cost", "requires")]
        if any(getattr(voucher, f) for f in fields):
            continue
        if voucher.key in read_by_key:
            assert voucher.key in body, "%s is read by key nowhere" % voucher.key
            continue
        inert.append(voucher.name)

    assert inert == ["Blank"], "a voucher that does nothing at all: %s" % inert
