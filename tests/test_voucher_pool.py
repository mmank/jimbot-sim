"""Which voucher the shop offers, checked draw for draw against the game.

A voucher is bought once and lasts the run, so getting one wrong is not a
wrong roll -- it is a wrong run from then on. Overstock changes every shop
after it, Hieroglyph changes how many blinds there are, Observatory changes
what a Planet card is worth.

The simulator had eleven vouchers written from memory, with names the game
does not use. There are thirty-two: sixteen base and sixteen upgrades, each
upgrade blank until its base has been redeemed, which is why half the pool is
unavailable at the start of a run and why the odds shift as it goes on.
"""

import pytest

from balatro.rng import RunRng
from balatro.shop_pool import (UNAVAILABLE, build_voucher_pool, draw_voucher)
from balatro.voucher_data import VOUCHER_DATA
from balatro_headless.runtime import HeadlessBalatro

SEEDS = ["TESTSEED", "ABCD1234", "7EVEN"]

POOLS_QUERY = ('local t = {} for k, v in pairs(G.GAME.pseudorandom) do '
               'if type(v) == "number" then '
               '  t[#t+1] = k .. "=" .. string.format("%.17g", v) end '
               'end return table.concat(t, " ")')


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


def _ev(engine, body):
    return engine.eval("(function() %s end)()" % body)


def _start(engine, seed):
    engine.execute('BOT.start_run({"%s","Red_Deck"}); api.pump(300)' % seed)


def _synced_rng(engine, seed):
    rng = RunRng(seed)
    for entry in _ev(engine, POOLS_QUERY).split():
        key, _, value = entry.partition("=")
        if key != "hashed_seed":
            rng.pools[key] = float(value)
    return rng


def _redeemed(engine):
    raw = _ev(engine, 'local t = {} for k, _ in pairs(G.GAME.used_vouchers '
                      'or {}) do t[#t+1] = k end return table.concat(t, " ")')
    return raw.split()


@pytest.mark.parametrize("seed", SEEDS)
def test_python_predicts_the_voucher_the_shop_offers(engine, seed):
    """Fifteen consecutive draws, each compared by key."""
    _start(engine, seed)
    for draw in range(15):
        rng = _synced_rng(engine, seed)
        predicted = draw_voucher(rng, ante=1, redeemed=_redeemed(engine))
        actual = _ev(engine, "return get_next_voucher_key()")
        assert predicted == actual, "draw %d on seed %s" % (draw, seed)


def test_the_pool_matches_the_games(engine):
    """Same entries in the same places, blanks included.

    The blanks are the point: the pool keeps its length, so an unavailable
    voucher costs a resample rather than being skipped, and a pool that drops
    them draws the wrong voucher from the same roll.
    """
    _start(engine, "TESTSEED")
    actual = _ev(engine, 'local p = get_current_pool("Voucher") local t = {} '
                         'for i, v in ipairs(p) do '
                         't[#t+1] = (type(v) == "string" and v or v.key) end '
                         'return table.concat(t, " ")').split()
    assert build_voucher_pool() == actual


def test_an_upgrade_is_blank_until_its_base_is_redeemed():
    """Half the list is unavailable at the start of every run."""
    pool = build_voucher_pool()
    keys = [row[0] for row in VOUCHER_DATA]
    assert pool[keys.index("v_overstock_plus")] == UNAVAILABLE
    opened = build_voucher_pool(redeemed=["v_overstock_norm"])
    assert "v_overstock_plus" in opened
    assert "v_overstock_norm" not in opened      # and the base is now spent


def test_a_voucher_already_in_the_shop_is_withheld():
    """Which is how a Voucher Tag's second voucher is never a duplicate."""
    pool = build_voucher_pool(on_offer=["v_hone"])
    assert "v_hone" not in pool


def test_every_upgrade_names_a_real_voucher():
    keys = {row[0] for row in VOUCHER_DATA}
    for key, _name, _cost, requires, _extra in VOUCHER_DATA:
        assert not requires or requires in keys, key
    assert len(VOUCHER_DATA) == 32
