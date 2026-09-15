"""A Buffoon pack joker only takes the stickers its centre allows.

A pack builds its jokers with create_card("Joker", G.pack_cards, ...)
(card.lua:1774), the same function that stocks the shop, and create_card
polls the stickers the same way for both areas -- only the pool names change,
"packetper" and "packssjr" (common_events.lua:2137-2146). The stickers are
then put on with Card:set_eternal and Card:set_perishable, and both refuse:
set_eternal needs `eternal_compat` and set_perishable needs `perishable_compat`
(card.lua:506-518). Spare Trousers is `perishable_compat = false`
(game.lua:471); Gros Michel is `eternal_compat = false`.

The simulator vetoed the stickers in `_apply_stickers`, which only the shop
row goes through. shop_pool.pack_contents polled them and GameState._pack_card
copied them onto the joker as they came, so a pack joker took a sticker the
game refuses.

0K02UUCE, Blue Deck, stake 7, stopped at decision 43 on it. A Spare Trousers
picked from a Buffoon pack at ante 2 (decision 18) came out perishable in the
shadow and plain in the game. The shadow counted it down 5, 4, 3, 2, 1 over
rounds 4 to 7, which is the game's own calculate_perishable timing
(card.lua:2278-2289, state_events.lua:109). At the end of round 8 it was
debuffed, while the game's Spare Trousers, which had no sticker, played on.
"""

import itertools

import pytest

from jimbot_sim import shop as shop_mod
from jimbot_sim import shop_pool
from jimbot_sim.game import GameState
from jimbot_sim.rng import RunRng

ALL_ON = {"eternals": True, "perishables": True, "rentals": True}

# Spare Trousers refuses perishable, Gros Michel refuses eternal, Joker takes
# both, which shows the polls are firing at all.
CYCLE = ("j_trousers", "j_gros_michel", "j_joker")

KEY_BY_NAME = {name: key for key, name in shop_pool.NAME_BY_JOKER_KEY.items()}


def _fixed_draws(monkeypatch):
    keys = itertools.cycle(CYCLE)
    monkeypatch.setattr(shop_pool, "draw_joker",
                        lambda *a, **k: next(keys))


def _check(marks):
    """marks: (key, eternal, perishable) for every joker a pack offered."""
    assert shop_pool.takes_sticker("Spare Trousers") == (True, False)
    assert shop_pool.takes_sticker("Gros Michel")[0] is False
    for key, eternal, perishable in marks:
        name = shop_pool.NAME_BY_JOKER_KEY[key]
        eternal_ok, perishable_ok = shop_pool.takes_sticker(name)
        assert not (eternal and not eternal_ok), name
        assert not (perishable and not perishable_ok), name
        assert not (eternal and perishable), name
    joker = [m for m in marks if m[0] == "j_joker"]
    assert any(m[1] for m in joker) and any(m[2] for m in joker)


def test_pack_contents_drops_a_sticker_the_centre_refuses(monkeypatch):
    """card.lua:1774 -> common_events.lua:2137-2146 -> card.lua:506-518."""
    _fixed_draws(monkeypatch)
    rng = RunRng("0K02UUCE")
    cards = []
    for ante in (1, 2, 3, 4):
        for _ in range(10):
            cards += shop_pool.pack_contents(rng, "Buffoon", 4, ante,
                                             stickers=ALL_ON)
    _check([(c["key"], c["eternal"], c["perishable"]) for c in cards])


def test_a_buffoon_pack_opened_at_stake_eight_offers_no_refused_sticker(
        monkeypatch):
    """The whole path a pack joker takes into the run: _open_pack fills the
    pack with pack_contents, _pack_card builds each joker from its entry."""
    _fixed_draws(monkeypatch)
    game = GameState(seed="0K02UUCE", deck="Blue Deck", stake=8)
    offered = []
    for _ in range(40):
        game._open_pack(shop_mod.pack_from_key("p_buffoon_normal_1"),
                        free=True)
        offered += game.pack_options
    _check([(KEY_BY_NAME[j.name], j.eternal, j.perishable) for j in offered])
    trousers = [j for j in offered if j.name == "Spare Trousers"]
    assert trousers and not any(j.perishable or j.perish_tally
                                for j in trousers)


# --------------------------------------------------------------------------
# on the engine
# --------------------------------------------------------------------------

POOLS_QUERY = ('local t = {} for k, v in pairs(G.GAME.pseudorandom) do '
               'if type(v) == "number" then '
               '  t[#t+1] = k .. "=" .. string.format("%.17g", v) end '
               'end return table.concat(t, " ")')

# Card:open's Buffoon branch (card.lua:1774), into a pack area so create_card
# takes its `area == G.pack_cards` sticker branch.
BUFFOON_STICKERS = """
if not G.pack_cards then
  G.pack_cards = CardArea(0, 0, G.CARD_W, G.CARD_H,
                          {card_limit = 5, type = 'consumeable'})
end
local t = {}
for i = 1, %d do
  local c = create_card("Joker", G.pack_cards, nil, nil, true, true, nil, "buf")
  t[#t+1] = table.concat({c.config.center.key,
                          c.ability.eternal and 1 or 0,
                          c.ability.perishable and 1 or 0,
                          c.ability.rental and 1 or 0}, ",")
end
return table.concat(t, " ")
"""


@pytest.fixture(scope="module")
def engine():
    pytest.importorskip("lupa")
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
    if not engine_available():
        pytest.skip("no Balatro engine: need vendor/balatro_src")
    return HeadlessBalatro().boot()


def _ev(engine, body):
    return engine.eval("(function() %s end)()" % body)


@pytest.mark.slow
@pytest.mark.parametrize("seed", ["0K02UUCE", "TESTSEED", "ABCD1234"])
def test_the_engine_puts_the_same_stickers_on_a_buffoon_pack(engine, seed):
    """Stake 8 turns on all three stickers. Twelve packs of four are compared
    key and sticker by sticker against create_card itself."""
    engine.execute('BOT.start_run({"%s","Blue_Deck",8}); api.pump(300)'
                   % seed)
    for pack in range(12):
        rng = RunRng(seed)
        for entry in _ev(engine, POOLS_QUERY).split():
            key, _, value = entry.partition("=")
            if key != "hashed_seed":
                rng.pools[key] = float(value)
        used = _ev(engine, 'local t = {} for k, _ in pairs('
                           'G.GAME.used_jokers or {}) do t[#t+1] = k end '
                           'return table.concat(t, " ")').split()
        predicted = shop_pool.pack_contents(rng, "Buffoon", 4, ante=1,
                                            seen=used, seen_jokers=used,
                                            stickers=ALL_ON)
        actual = [row.split(",") for row in
                  _ev(engine, BUFFOON_STICKERS % 4).split()]
        assert [(c["key"], int(c["eternal"]), int(c["perishable"]),
                 int(c["rental"])) for c in predicted] == [
            (k, int(e), int(p), int(r)) for k, e, p, r in actual], (
            "buffoon pack %d on seed %s" % (pack + 1, seed))
