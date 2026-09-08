"""What a booster pack contains, checked card for card against the game.

The simulator drew pack contents by picking uniformly out of a whole card set.
That is wrong in two directions at once. It ignores the pool, so it offers a
Tarot the run has already been given, or Planet X for a hand nobody has
played. And it ignores the stream: a real pack takes a known number of rolls
from known pools -- a Standard pack takes six per card -- so a pack generated
any other way leaves every later draw in the run standing somewhere else.

Three details in the game's own sequence are easy to miss and all three change
what comes out:

  the soul poll   every Tarot, Planet and Spectral card rolls 1-in-333 for The
                  Soul or Black Hole *before* its pool draw, and the roll
                  happens whether or not it fires. Spectral rolls twice, once
                  for each, against the same pool name.
  the append      a Tarot from an Arcana pack draws from "Tarotar11", one from
                  the shop from "Tarotsho1". Same pool contents, separate
                  streams.
  the order       a Standard card decides enhanced-or-not, then enhancement,
                  then face, then edition, then whether a seal, then which
                  seal. Six pools, each advanced even when nothing comes of it.

The engine side runs the same create_card calls Card:open runs, so what is
being compared is the sequence, not a reimplementation of it.
"""

import pytest

from jimbot_sim.pack_data import PACK_DATA
from jimbot_sim.rng import RunRng
from jimbot_sim.shop_pool import ENHANCEMENTS, draw_pack, pack_contents
from jimbot_sim.headless.runtime import HeadlessBalatro

SEEDS = ["TESTSEED", "ABCD1234", "7EVEN"]

POOLS_QUERY = ('local t = {} for k, v in pairs(G.GAME.pseudorandom) do '
               'if type(v) == "number" then '
               '  t[#t+1] = k .. "=" .. string.format("%.17g", v) end '
               'end return table.concat(t, " ")')

# The create_card call for each kind, lifted from Card:open in card.lua.
CONSUMABLE_CALL = {
    "Arcana": 'create_card("Tarot", nil, nil, nil, true, true, nil, "ar1")',
    "Celestial": 'create_card("Planet", nil, nil, nil, true, true, nil, "pl1")',
    "Spectral": 'create_card("Spectral", nil, nil, nil, true, true, nil, "spe")',
    "Buffoon": 'create_card("Joker", nil, nil, nil, true, true, nil, "buf")',
}

RANK_CODE = {"2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7",
             "8": "8", "9": "9", "10": "T", "Jack": "J", "Queen": "Q",
             "King": "K", "Ace": "A"}
SUIT_CODE = {"Clubs": "C", "Diamonds": "D", "Hearts": "H", "Spades": "S"}


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


def _ev(engine, body):
    return engine.eval("(function() %s end)()" % body)


def _start(engine, seed):
    engine.execute('BOT.start_run({"%s","Red_Deck"}); api.pump(300)' % seed)


def _synced_rng(engine, seed):
    """A generator standing exactly where the engine's pools stand."""
    rng = RunRng(seed)
    for entry in _ev(engine, POOLS_QUERY).split():
        key, _, value = entry.partition("=")
        if key != "hashed_seed":
            rng.pools[key] = float(value)
    return rng


def _seen(engine, table):
    raw = _ev(engine, 'local t = {} for k, _ in pairs(G.GAME.%s or {}) do '
                      't[#t+1] = k end return table.concat(t, " ")' % table)
    return raw.split()


def _played_hands(engine):
    raw = _ev(engine, 'local t = {} for k, v in pairs(G.GAME.hands) do '
                      'if (v.played or 0) > 0 then t[#t+1] = k end end '
                      'return table.concat(t, "|")')
    return [h for h in raw.split("|") if h]


def _context(engine):
    used = _seen(engine, "used_jokers")
    return {
        "seen": used,
        "seen_jokers": used,
        "played_hands": _played_hands(engine),
        "soul_used": "c_soul" in used,
        "black_hole_used": "c_black_hole" in used,
    }


def _engine_consumables(engine, kind, count):
    """The keys the game's own create_card hands back, in order."""
    return _ev(engine,
               'local t = {} for i = 1, %d do local c = %s '
               't[#t+1] = c.config.center.key end '
               'return table.concat(t, " ")'
               % (count, CONSUMABLE_CALL[kind])).split()


# The Standard branch of Card:open: the card, then its edition, then its seal.
STANDARD_CALL = """
local t = {}
for i = 1, %d do
  local ante = G.GAME.round_resets.ante
  local card = create_card(
      (pseudorandom(pseudoseed("stdset"..ante)) > 0.6) and "Enhanced" or "Base",
      nil, nil, nil, nil, true, nil, "sta")
  local edition = poll_edition("standard_edition"..ante, 2, true)
  card:set_edition(edition)
  local seal = "none"
  if pseudorandom(pseudoseed("stdseal"..ante)) > 1 - 0.02*10 then
    local seal_type = pseudorandom(pseudoseed("stdsealtype"..ante))
    if seal_type > 0.75 then seal = "Red"
    elseif seal_type > 0.5 then seal = "Blue"
    elseif seal_type > 0.25 then seal = "Gold"
    else seal = "Purple" end
  end
  local ed = "none"
  if card.edition then
    for _, name in ipairs({"negative", "polychrome", "holo", "foil"}) do
      if card.edition[name] then ed = name end
    end
  end
  t[#t+1] = table.concat({tostring(card.base.value), tostring(card.base.suit),
                          card.config.center.key, ed, seal}, ",")
end
return table.concat(t, " ")
"""


def _engine_standard(engine, count):
    rows = []
    for row in _ev(engine, STANDARD_CALL % count).split():
        value, suit, center, edition, seal = row.split(",")
        rows.append({"set": "Playing", "rank": RANK_CODE[value],
                     "suit": SUIT_CODE[suit],
                     "enhancement": None if center == "c_base" else center,
                     "edition": edition,
                     "seal": None if seal == "none" else seal})
    return rows


@pytest.mark.parametrize("kind", ["Arcana", "Celestial", "Spectral"])
@pytest.mark.parametrize("seed", SEEDS)
def test_python_predicts_what_a_consumable_pack_offers(engine, kind, seed):
    """Five packs in a row, five cards each, compared key by key.

    Consecutive matters more here than anywhere: the soul poll advances a pool
    on every single card, so a sequence this long only stays in step if the
    poll is being made even on the 332 cards out of 333 where it does nothing.
    """
    _start(engine, seed)
    for pack in range(5):
        rng = _synced_rng(engine, seed)
        predicted = pack_contents(rng, kind, 5, ante=1, **_context(engine))
        actual = _engine_consumables(engine, kind, 5)
        assert [c["key"] for c in predicted] == actual, (
            "%s pack %d on seed %s" % (kind, pack + 1, seed))


@pytest.mark.parametrize("seed", SEEDS)
def test_python_predicts_what_a_buffoon_pack_offers(engine, seed):
    _start(engine, seed)
    for pack in range(5):
        rng = _synced_rng(engine, seed)
        predicted = pack_contents(rng, "Buffoon", 4, ante=1,
                                  **_context(engine))
        actual = _engine_consumables(engine, "Buffoon", 4)
        assert [c["key"] for c in predicted] == actual, (
            "buffoon pack %d on seed %s" % (pack + 1, seed))


@pytest.mark.parametrize("seed", SEEDS)
def test_python_predicts_a_standard_pack_down_to_the_seal(engine, seed):
    """Face, enhancement, edition and seal, not just the card.

    Everything but the face is rare, so a run long enough to see one is a run
    long enough for the pools to have drifted -- which is the point.
    """
    _start(engine, seed)
    for pack in range(6):
        rng = _synced_rng(engine, seed)
        predicted = pack_contents(rng, "Standard", 5, ante=1,
                                  **_context(engine))
        assert predicted == _engine_standard(engine, 5), (
            "standard pack %d on seed %s" % (pack + 1, seed))


def test_a_standard_pack_is_not_all_plain_cards(engine):
    """Guard against the comparison passing on a degenerate sequence."""
    _start(engine, "TESTSEED")
    rng = _synced_rng(engine, "TESTSEED")
    cards = (pack_contents(rng, "Standard", 5, ante=1)
             + pack_contents(rng, "Standard", 5, ante=1)
             + pack_contents(rng, "Standard", 5, ante=1))
    assert any(c["enhancement"] for c in cards), \
        "thirty standard cards and not one enhanced"
    assert len({(c["rank"], c["suit"]) for c in cards}) > 8


def test_the_enhancement_pool_is_the_games_order():
    """Alphabetising it would hand out different enhancements from a seed."""
    assert ENHANCEMENTS[:4] == ["m_bonus", "m_mult", "m_wild", "m_glass"]
    assert sorted(ENHANCEMENTS) != ENHANCEMENTS


@pytest.mark.parametrize("seed", SEEDS)
def test_python_predicts_which_pack_the_shop_offers(engine, seed):
    """The pack itself, before anything is inside it.

    Weights are not uniform across sizes -- a mega pack is a quarter as likely
    as a normal one -- so treating the kind and the size as separate choices
    gives the right kinds at the wrong sizes, and the wrong price with them.
    """
    _start(engine, seed)
    # Burn the opening Buffoon, which is a short-circuit rather than a roll
    # and is checked on its own below.
    _ev(engine, 'local p = get_pack("shop_pack") return p.key')
    for draw in range(10):
        rng = _synced_rng(engine, seed)
        predicted = draw_pack(rng, ante=1, key="shop_pack")
        actual = _ev(engine, 'local p = get_pack("shop_pack") return p.key')
        assert predicted[0] == actual, "draw %d on seed %s" % (draw, seed)


def test_the_first_shop_of_a_run_always_offers_a_buffoon_pack(engine):
    """The game short-circuits before any roll, with a bare math.random(1, 2).

    Rolling normally there gives an opening the game never gives, so this is
    the one place a simulator's very first shop can be wrong.
    """
    for seed in SEEDS:
        _start(engine, seed)
        assert _ev(engine, "return G.GAME.first_shop_buffoon and 1 or 0") == 0

        # Which of the two it is comes from a bare math.random, which reads
        # whatever the last seeded draw left behind rather than a pool of its
        # own. Both sides make the same named draw first so that both live
        # streams stand in the same place, which is the thing being checked:
        # get the stream wrong and the branch is still right and the pack is
        # still wrong.
        _ev(engine, 'return pseudorandom(pseudoseed("probe"))')
        rng = _synced_rng(engine, seed)
        rng.pseudorandom("probe")

        actual = _ev(engine, 'local p = get_pack("shop_pack") return p.key')
        assert actual.startswith("p_buffoon_normal_"),             "seed %s opened on %s" % (seed, actual)
        assert _ev(engine, "return G.GAME.first_shop_buffoon and 1 or 0") == 1
        assert draw_pack(rng, ante=1, first_shop=True)[0] == actual,             "seed %s" % seed


def test_pack_data_carries_the_prices():
    """The cost is the pack's, not a function of its size class."""
    by_key = {row[0]: row for row in PACK_DATA}
    assert by_key["p_buffoon_normal_1"][4:] == (2, 4)     # cards, cost
    assert by_key["p_arcana_mega_1"][3:] == (2, 5, 8)     # choose, cards, cost
