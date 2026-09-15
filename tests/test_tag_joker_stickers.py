"""An Uncommon or Rare Tag's joker takes the shop's sticker polls.

The tags make their joker with create_card('Joker', _context.area, ...)
(tag.lua:356, 370), and _context.area is the area create_card_for_shop was
handed (UI_definitions.lua:756) -- always G.shop_jokers
(common_events.lua:1114, game.lua:3112, button_callbacks.lua:2884). So
create_card takes its `area == G.shop_jokers` branch and polls "etperpoll"
and, with rentals on, "ssjr" (common_events.lua:2137-2146), and the stickers go
on through set_eternal / set_perishable with their centre veto
(card.lua:506-518).

GameState._forced_shop_slot built the tag's joker with no sticker poll at all:
from stake 4 up a tag joker never came eternal, perishable or rental, and every
later shop joker in that ante read its stickers one draw early.

Found by reading the Lua while fixing the pack-joker veto that stopped
0K02UUCE (see test_pack_joker_sticker_veto); no live run has stopped on it yet.
"""

import itertools

import pytest

from jimbot_sim import shop_pool
from jimbot_sim.game import GameState, Tag


@pytest.mark.parametrize("tag", [Tag.UNCOMMON, Tag.RARE])
def test_a_tag_joker_takes_the_shops_sticker_poll(monkeypatch, tag):
    """tag.lua:356, 370 -> common_events.lua:2137-2146, shop pool names."""
    game = GameState(seed="0K02UUCE", deck="Blue Deck", stake=8)
    game.ante = 2
    calls = []
    real = shop_pool.poll_stickers

    def record(rng, ante, in_pack=False, **kw):
        calls.append((ante, in_pack, kw.get("name")))
        return real(rng, ante, in_pack, **kw)

    monkeypatch.setattr(shop_pool, "poll_stickers", record)
    game.tags.append(tag)
    slot = game._forced_shop_slot()
    assert slot is not None and slot.joker is not None and slot.couponed
    assert calls == [(2, False, slot.joker.name)]


def test_a_tag_joker_can_be_perishable_but_not_against_its_centre(
        monkeypatch):
    keys = itertools.cycle(("j_trousers", "j_gros_michel", "j_joker"))
    monkeypatch.setattr(shop_pool, "draw_joker", lambda *a, **k: next(keys))
    game = GameState(seed="0K02UUCE", deck="Blue Deck", stake=8)
    made = []
    for _ in range(60):
        game.tags.append(Tag.UNCOMMON)
        made.append(game._forced_shop_slot().joker)
    for joker in made:
        eternal_ok, perishable_ok = shop_pool.takes_sticker(joker.name)
        assert not (joker.eternal and not eternal_ok), joker.name
        assert not (joker.perishable and not perishable_ok), joker.name
    plain = [j for j in made if j.name == "Joker"]
    assert any(j.eternal for j in plain)
    assert any(j.perishable and j.perish_tally == 5 for j in plain)
    assert any(j.rental for j in made)


# --------------------------------------------------------------------------
# on the engine
# --------------------------------------------------------------------------

POOLS_QUERY = ('local t = {} for k, v in pairs(G.GAME.pseudorandom) do '
               'if type(v) == "number" then '
               '  t[#t+1] = k .. "=" .. string.format("%.17g", v) end '
               'end return table.concat(t, " ")')

# What the Uncommon Tag runs (tag.lua:370), into the shop's own area.
UNCOMMON_TAG_JOKERS = """
if not G.shop_jokers then
  G.shop_jokers = CardArea(0, 0, G.CARD_W, G.CARD_H,
                           {card_limit = 2, type = 'shop'})
end
local t = {}
for i = 1, %d do
  local c = create_card('Joker', G.shop_jokers, nil, 0.9, nil, nil, nil, 'uta')
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
def test_the_engine_puts_the_same_stickers_on_a_tag_joker(engine, seed):
    """Stake 8, all three stickers on; twelve Uncommon Tag jokers in a row,
    key and stickers, against _forced_shop_slot from the same pools."""
    engine.execute('BOT.start_run({"%s","Blue_Deck",8}); api.pump(300)'
                   % seed)
    game = GameState(seed=seed, deck="Blue Deck", stake=8)
    for entry in _ev(engine, POOLS_QUERY).split():
        key, _, value = entry.partition("=")
        if key != "hashed_seed":
            game.rng.pools[key] = float(value)
    used = _ev(engine, 'local t = {} for k, _ in pairs('
                       'G.GAME.used_jokers or {}) do t[#t+1] = k end '
                       'return table.concat(t, " ")').split()
    game.seen_centers.update(used)
    predicted = []
    for _ in range(12):
        game.tags.append(Tag.UNCOMMON)
        joker = game._forced_shop_slot().joker
        predicted.append((joker.name, int(joker.eternal),
                          int(joker.perishable), int(joker.rental)))
        game.seen_centers.add(_key(joker.name))
    actual = [row.split(",") for row in
              _ev(engine, UNCOMMON_TAG_JOKERS % 12).split()]
    assert predicted == [(shop_pool.NAME_BY_JOKER_KEY[k], int(e), int(p),
                          int(r)) for k, e, p, r in actual]


def _key(name):
    return next(k for k, n in shop_pool.NAME_BY_JOKER_KEY.items()
                if n == name)
