"""A joker from a Buffoon pack or an Uncommon/Rare Tag polls at the run's rate.

Every joker create_card makes ends with

    poll_edition('edi'..(key_append or '')..G.GAME.round_resets.ante)

(functions/common_events.lua:2149), and poll_edition's ordinary branch widens
the polychrome, holographic and foil bands by the global G.GAME.edition_rate
(common_events.lua:2071-2076) whatever the caller passes. Hone and Glow Up set
that rate to their `extra` (card.lua:1900-1903). A Buffoon pack creates its
jokers through create_card with key_append 'buf' (card.lua:1774), and the
Uncommon and Rare Tags with 'uta' and 'rta' (tag.lua:370, 356) -- so all three
take the rate as a shop joker does.

The simulator passed it to the shop, to add_random_joker and to a Standard
pack, and not to these three, which moved the polychrome and holographic
boundaries back to their unhoned places.
"""

import pytest

from jimbot_sim import shop_pool
from jimbot_sim.game import GameState, Tag
from jimbot_sim.rng import RunRng
from jimbot_sim.shop import VOUCHER_BY_KEY


def _honed(seed="EDIPOLL2"):
    game = GameState(seed=seed, deck="Red Deck")
    voucher = VOUCHER_BY_KEY["v_hone"]
    game.vouchers.append(voucher)
    game._redeem_voucher(voucher)
    assert game.edition_rate == 2
    return game


def test_a_buffoon_pack_joker_is_polled_at_the_runs_edition_rate():
    """H7NS6Y2Y, Checkered Deck, stake 1, holding Hone: its first Buffoon pack
    of ante 6 offered Mr. Bones and a Polychrome Mail-In Rebate in the real
    game, where the simulator had a Holographic one.

    The second 'edibuf6' poll is 0.99058: above 1 - 0.006*2 (polychrome at
    Hone's rate) but below 1 - 0.006 (polychrome without it), and above
    1 - 0.02 either way (holographic).
    """
    def pack(rate):
        cards = shop_pool.pack_contents(RunRng("H7NS6Y2Y"), "Buffoon", 2,
                                        ante=6, edition_rate=rate)
        return [(c["key"], c["edition"]) for c in cards]

    assert pack(2.0) == [("j_mr_bones", "none"), ("j_mail", "polychrome")]
    # Without Hone the same roll is only holographic, so the rate is what
    # the comparison above is actually checking.
    assert pack(1.0) == [("j_mr_bones", "none"), ("j_mail", "holo")]


@pytest.mark.parametrize("tag,append", [(Tag.UNCOMMON, "uta"),
                                        (Tag.RARE, "rta")])
def test_a_tag_joker_is_polled_at_the_runs_edition_rate(monkeypatch, tag,
                                                        append):
    """create_card(..., 'uta') / 'rta' (tag.lua:370, 356) -> common_events.lua:2149."""
    game = _honed()
    game.ante = 3
    game.tags.append(tag)
    polled = []

    def record(rng, key, **kw):
        polled.append((key, kw.get("edition_rate", 1.0)))
        return "none"

    monkeypatch.setattr(shop_pool, "poll_edition", record)
    slot = game._forced_shop_slot()
    assert slot is not None and slot.joker is not None
    assert polled == [("edi%s3" % append, 2.0)]
