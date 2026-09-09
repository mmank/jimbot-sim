"""Which joker the shop offers, reproduced from the game.

Getting every joker's arithmetic right is only half of a faithful simulator.
The other half is which jokers a run is ever *shown*: a policy learns the odds
it is trained against, so a shop with the wrong distribution teaches a game
that does not exist, and does it invisibly, because no rule is broken.

The game's algorithm, from get_current_pool and create_card:

    rarity = pseudorandom("rarity" .. ante)
    rarity = 3 if rarity > 0.95 else 2 if rarity > 0.7 else 1

    pool = the rarity's pool, in order, with every entry that fails the
           filter replaced by "UNAVAILABLE" rather than dropped

    key    = "Joker" .. rarity .. ante
    center = pool[random index]
    while center == "UNAVAILABLE":
        center = pool[random index from key .. "_resample" .. n]

Two details there are easy to miss and both change the distribution. Filtered
entries are *replaced*, not removed, so the pool keeps its length and the draw
is uniform over all entries rather than over the available ones -- an
unavailable joker costs a resample instead of being skipped. And the resample
uses a different pool each time, so it is not a retry of the same draw.
"""

from __future__ import annotations

from typing import Iterable

from .consumable_data import CONSUMABLE_DATA, EXCLUDED_FROM_POOLS
from .joker_data import JOKER_DATA, pool_for_rarity
from .pack_data import PACK_DATA
from .tag_data import TAG_DATA
from .voucher_data import VOUCHER_DATA
from .rng import TW223, RunRng

UNAVAILABLE = "UNAVAILABLE"

# key -> the enhancement a run must already own before the shop offers it.
GATES = {key: gate
         for _, (key, _r, _o, _u, gate, _n, _y, _e, _p)
         in JOKER_DATA.items() if gate}

# Flags on the run's history rather than its deck. A joker with no_flag leaves
# the pool once that flag is set, and one with yes_flag only enters after it
# is: Gros Michel and Cavendish are the pair, and which of the two a shop can
# offer depends on whether Gros Michel has gone extinct this run. Neither
# joker's text says so.
NO_FLAG = {key: flag
           for _, (key, _r, _o, _u, _g, flag, _y, _e, _p)
           in JOKER_DATA.items() if flag}
YES_FLAG = {key: flag
            for _, (key, _r, _o, _u, _g, _n, flag, _e, _p)
            in JOKER_DATA.items() if flag}


# When every entry is blanked the game does not hand back a pool of nothing:
# it throws the pool away and offers one card. A run that has seen every Tarot
# is offered Strength, over and over. Without this the resample loop -- which
# the game writes with no bound, because it cannot fail -- never ends.
EMPTY_POOL_FALLBACK = {"Tarot": "c_strength", "Tarot_Planet": "c_strength",
                       "Planet": "c_pluto", "Spectral": "c_incantation",
                       "Joker": "j_joker", "Voucher": "v_blank",
                       "Tag": "tag_handy"}


def _or_fallback(pool: list[str], kind: str) -> list[str]:
    if any(entry != UNAVAILABLE for entry in pool):
        return pool
    return [EMPTY_POOL_FALLBACK.get(kind, "j_joker")]


def _draw(rng: RunRng, pool: list[str], key: str) -> str:
    """One pool draw, resampling past blanks under a different pool name."""
    center = rng.random_element(pool, key)
    attempt = 1
    while center == UNAVAILABLE:
        attempt += 1
        if attempt > 100:                      # cannot happen: see _or_fallback
            raise RuntimeError("pool %s never yielded a card" % key)
        center = rng.random_element(pool, "%s_resample%d" % (key, attempt))
    return center


def roll_rarity(rng: RunRng, ante: int, append: str = "") -> int:
    """1 common, 2 uncommon, 3 rare. Legendary never comes from a shop.

    `append` is the game's key_append, and it is part of the pool name rather
    than a label: a card rolled for the shop uses "sho", so it draws from a
    different stream than one created by a joker or a pack.
    """
    roll = rng.pseudorandom("rarity%d%s" % (ante, append))
    return 3 if roll > 0.95 else 2 if roll > 0.7 else 1


def build_pool(rarity: int, owned_enhancements: Iterable[str] = (),
               seen_jokers: Iterable[str] = (), showman: bool = False,
               pool_flags: Iterable[str] = ()) -> list[str]:
    """The rarity's pool with unavailable entries blanked, not removed.

    Length is preserved deliberately: the game draws an index into the whole
    pool, so removing entries would change every draw after the first gap.
    """
    owned = set(owned_enhancements)
    seen = set(seen_jokers)
    flags = set(pool_flags)
    pool = []
    for key in pool_for_rarity(rarity):
        gate = GATES.get(key)
        if gate and gate not in owned:
            pool.append(UNAVAILABLE)          # no Lucky Cat without a Lucky card
        elif NO_FLAG.get(key) in flags:
            pool.append(UNAVAILABLE)          # Gros Michel, once extinct
        elif YES_FLAG.get(key) and YES_FLAG[key] not in flags:
            pool.append(UNAVAILABLE)          # Cavendish, until then
        elif key in seen and not showman:
            pool.append(UNAVAILABLE)          # each joker appears once a run
        else:
            pool.append(key)
    return pool


def draw_joker(rng: RunRng, ante: int, owned_enhancements: Iterable[str] = (),
               seen_jokers: Iterable[str] = (), showman: bool = False,
               rarity: int | None = None,
               pool_flags: Iterable[str] = (), append: str = "") -> str:
    """One shop joker, as the game would roll it."""
    if rarity is None:
        rarity = roll_rarity(rng, ante, append)
    pool = build_pool(rarity, owned_enhancements, seen_jokers, showman,
                      pool_flags)
    # A legendary draw names a different stream, and the game is explicit
    # about it in get_current_pool:
    #
    #   _pool_key = 'Joker'..rarity..((not _legendary and _append) or '')
    #   return _pool, _pool_key..(not _legendary and ante or '')
    #
    # Both `and` clauses fail when _legendary is true, so the append and the
    # ante are dropped: The Soul draws from "Joker4", never "Joker4sou8". The
    # simulator was appending both, which is a real stream with a real
    # sequence in it -- so it drew a legendary every time, plausibly, and drew
    # the wrong one. Recording 8 stopped on it at step 190 of 443: Chicot
    # recorded, Triboulet simulated.
    #
    # Keyed on rarity four rather than on a flag because in the game the two
    # are the same thing. A forced _rarity is a probability there, not an
    # index, and it is compared against 0.95 and 0.7 -- so passing four makes
    # a *rare* joker, and four is reachable only through _legendary.
    key = "Joker4" if rarity == 4 else "Joker%d%s%d" % (rarity, append, ante)
    return _draw(rng, _or_fallback(pool, "Joker"), key)


# --------------------------------------------------------------------------
# consumables
# --------------------------------------------------------------------------

def build_consumable_pool(card_set: str, played_hands: Iterable[str] = (),
                          seen: Iterable[str] = (), showman: bool = False
                          ) -> list[str]:
    """A Tarot, Planet or Spectral pool, blanked the same way as jokers.

    The rule worth knowing is the Planet softlock: Planet X, Ceres and Eris
    are only in the pool once the hand they level has been played. A run that
    has never made a Five of a Kind is never offered the card for it, so a
    simulator that ignores this hands out deck-defining cards for hands the
    player cannot yet make.
    """
    played = set(played_hands)
    already = set(seen)
    pool = []
    for key, name, _order, softlock, hand_type in CONSUMABLE_DATA[card_set]:
        if name in EXCLUDED_FROM_POOLS:
            pool.append(UNAVAILABLE)          # The Soul, Black Hole
        elif softlock and hand_type not in played:
            pool.append(UNAVAILABLE)          # Planet X before a Five of a Kind
        elif key in already and not showman:
            pool.append(UNAVAILABLE)
        else:
            pool.append(key)
    return pool


def draw_consumable(rng: RunRng, card_set: str, ante: int,
                    played_hands: Iterable[str] = (),
                    seen: Iterable[str] = (), showman: bool = False,
                    append: str = "") -> str:
    """One consumable, as the game would roll it."""
    pool = build_consumable_pool(card_set, played_hands, seen, showman)
    return _draw(rng, _or_fallback(pool, card_set),
                 "%s%s%d" % (card_set, append, ante))


# --------------------------------------------------------------------------
# what fills a shop slot
# --------------------------------------------------------------------------

# The run's starting rates, from G.GAME. Vouchers and decks move them --
# Tarot Merchant raises tarot_rate, the Ghost Deck sets spectral_rate to 2 --
# so a run's shop mix is not fixed even though these defaults are.
BASE_RATES = {"Joker": 20.0, "Tarot": 4.0, "Planet": 4.0,
              "Base": 0.0, "Spectral": 0.0}

# The order matters: the roll walks these bands in sequence, so reordering
# them changes which type a given roll lands on even with the same weights.
RATE_ORDER = ("Joker", "Tarot", "Planet", "Base", "Spectral")

SHOP_APPEND = "sho"


def roll_slot_type(rng: RunRng, ante: int, rates: dict | None = None) -> str:
    """Which kind of card fills one shop slot.

    The game rolls once against the summed rates and walks the bands in a
    fixed order, so this is not a dictionary lookup by weight -- the sequence
    is part of the answer.
    """
    rates = dict(BASE_RATES) if rates is None else dict(rates)
    total = sum(rates[k] for k in RATE_ORDER)
    polled = rng.pseudorandom("cdt%d" % ante) * total
    seen = 0.0
    for kind in RATE_ORDER:
        value = rates[kind]
        if polled > seen and polled <= seen + value:
            return kind
        seen += value
    return RATE_ORDER[0]


def draw_shop_card(rng: RunRng, ante: int, rates: dict | None = None,
                   **pool_args) -> tuple[str, str]:
    """One shop slot: its type and the card in it.

    Shop cards carry the game's "sho" key_append, which puts them on their own
    pools -- the same joker rolled for a pack draws from a different stream.
    """
    kind = roll_slot_type(rng, ante, rates)
    seen = pool_args.get("seen_jokers", ())
    if kind == "Joker":
        # Jokers care about the deck's enhancements and the run's flags;
        # consumables care about which hands have been played. Passing either
        # set to the wrong side is a signature error, not a filter.
        return kind, draw_joker(
            rng, ante, append=SHOP_APPEND, seen_jokers=seen,
            owned_enhancements=pool_args.get("owned_enhancements", ()),
            showman=pool_args.get("showman", False),
            pool_flags=pool_args.get("pool_flags", ()))
    if kind in ("Tarot", "Planet", "Spectral"):
        return kind, draw_consumable(
            rng, kind, ante, played_hands=pool_args.get("played_hands", ()),
            seen=seen, showman=pool_args.get("showman", False),
            append=SHOP_APPEND)
    return kind, "playing_card"


# --------------------------------------------------------------------------
# turning a game key back into a simulator spec
# --------------------------------------------------------------------------
#
# The pools speak the game's keys, because that is what can be checked against
# the engine. The simulator's registries are keyed by display name, so the two
# have to be joined somewhere; doing it here keeps the pools honest rather
# than renaming them to suit us.

NAME_BY_JOKER_KEY = {key: name
                     for name, (key, *_rest) in JOKER_DATA.items()}
NAME_BY_CONSUMABLE_KEY = {entry[0]: entry[1]
                          for entries in CONSUMABLE_DATA.values()
                          for entry in entries}


def poll_edition(rng: RunRng, key: str = "edition_generic", mod: float = 1.0,
                 no_negative: bool = False, edition_rate: float = 1.0,
                 guaranteed: bool = False) -> str:
    """The game's poll_edition: one roll, compared against stacked bands.

    Written as descending thresholds off 1.0 rather than as weights, because
    that is how the game writes it and the two are not the same when a
    modifier scales them: `mod` widens every band from the top, so the
    boundaries move relative to each other rather than in proportion.

    Returns "none", "foil", "holo", "polychrome" or "negative".
    """
    poll = rng.pseudorandom(key)
    if guaranteed:
        # The Wheel of Fortune's form: the bands are twenty-five times as
        # wide and cover the whole range, so something always comes out.
        # `mod` and the run's edition rate are ignored here, as in the game.
        if poll > 1 - 0.003 * 25 and not no_negative:
            return "negative"
        if poll > 1 - 0.006 * 25:
            return "polychrome"
        if poll > 1 - 0.02 * 25:
            return "holo"
        if poll > 1 - 0.04 * 25:
            return "foil"
        return "none"
    if poll > 1 - 0.003 * mod and not no_negative:
        return "negative"
    if poll > 1 - 0.006 * edition_rate * mod:
        return "polychrome"
    if poll > 1 - 0.02 * edition_rate * mod:
        return "holo"
    if poll > 1 - 0.04 * edition_rate * mod:
        return "foil"
    return "none"


# --------------------------------------------------------------------------
# booster packs
# --------------------------------------------------------------------------

def draw_pack(rng: RunRng, ante: int, first_shop: bool = False,
              key: str = "shop_pack") -> tuple:
    """One booster pack, as get_pack rolls it.

    Two things here are not obvious from playing. The first shop of a run
    always offers a Buffoon pack -- the game short-circuits before any roll,
    so a simulator that rolls normally there gives the player a different
    opening than the game ever does. And the weights are not uniform across
    sizes: a mega pack is a quarter as likely as a normal one of the same
    kind, so treating a pack type as one choice and its size as another gives
    the right types at the wrong sizes.

    Returns the pool entry: (key, kind, weight, choose, cards, cost).
    """
    if first_shop:
        # p_buffoon_normal_1 or _2, chosen with a bare math.random(1, 2).
        # There is no pool name here, so it continues whatever stream the last
        # seeded draw left behind -- see RunRng.math_random.
        index = int(rng.math_random(1, 2))
        wanted = "p_buffoon_normal_%d" % index
        for entry in PACK_DATA:
            if entry[0] == wanted:
                return entry

    total = sum(entry[2] for entry in PACK_DATA)
    poll = rng.pseudorandom("%s%d" % (key, ante)) * total
    seen = 0.0
    for entry in PACK_DATA:
        weight = entry[2]
        seen += weight
        if seen >= poll and seen - weight <= poll:
            return entry
    return PACK_DATA[-1]


# --------------------------------------------------------------------------
# skip tags
# --------------------------------------------------------------------------

def build_tag_pool(ante: int, discovered: Iterable[str] | None = None
                   ) -> list[str]:
    """Tags on offer at this ante, blanked rather than dropped as ever.

    Five tags name a centre that must have been *discovered* -- Rare Tag wants
    Blueprint seen, the edition tags want their edition seen. Discovery
    belongs to the profile rather than the run, and a profile that has played
    at all has them, which is what the engine reports. So the default is that
    they are known, and a caller who wants to model a fresh profile passes the
    set it has. Gating on an empty set instead blanked four tags the engine
    was offering, and the draw landed elsewhere from there on.
    """
    known = None if discovered is None else set(discovered)
    pool = []
    for key, _name, min_ante, requires in TAG_DATA:
        if min_ante and min_ante > ante:
            pool.append(UNAVAILABLE)
        elif requires and known is not None and requires not in known:
            pool.append(UNAVAILABLE)
        else:
            pool.append(key)
    return pool


def draw_tag(rng: RunRng, ante: int, discovered: Iterable[str] | None = None,
             append: str = "") -> str:
    """The reward for skipping a blind, as get_next_tag_key rolls it.

    Same machinery as every other pool: an index into a list that keeps its
    length, and a resample from a differently-named pool when the entry is
    blank. The simulator used to roll from eight tags of its own with its own
    key, which handed a run a tag it was never offered -- and an Uncommon or
    Rare tag hands over a joker with it.
    """
    pool = _or_fallback(build_tag_pool(ante, discovered), "Tag")
    return _draw(rng, pool, "Tag%s%d" % (append, ante))


# --------------------------------------------------------------------------
# what is inside a pack
# --------------------------------------------------------------------------

# The Enhanced pool in the game's own order. pseudorandom_element sorts an
# array-shaped pool by its integer keys, so this order is the draw order and
# alphabetising it would hand out different enhancements from the same seed.
ENHANCEMENTS = ["m_bonus", "m_mult", "m_wild", "m_glass",
                "m_steel", "m_stone", "m_gold", "m_lucky"]

# G.P_CARDS is keyed by strings, so pseudorandom_element sorts it by key:
# clubs, diamonds, hearts, spades, and within a suit 2-9 then A J K Q T.
SUITS = ["C", "D", "H", "S"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "A", "J", "K", "Q", "T"]
FRONTS = ["%s_%s" % (suit, rank) for suit in SUITS for rank in RANKS]

# The appends each kind of pack creates its cards under. They are pool names,
# not labels: a Tarot from an Arcana pack draws from "Tarotar11", a Tarot from
# the shop from "Tarotsho1", and the two streams run independently.
PACK_APPEND = {"Arcana": "ar1", "Celestial": "pl1", "Spectral": "spe",
               "Standard": "sta", "Buffoon": "buf"}


def _soul_key(card_set: str, ante: int) -> str:
    return "soul_%s%d" % (card_set, ante)


def _soulable(rng: RunRng, card_set: str, ante: int, soul_used: bool,
              black_hole_used: bool, showman: bool) -> str | None:
    """The 1-in-333 that turns a pack card into The Soul or Black Hole.

    This runs *before* the pool draw and it runs whether or not it fires, so
    a simulator that skips it draws every later card of that type from a pool
    one step behind. Spectral polls twice -- once for each -- against the same
    pool name, which advances it twice.
    """
    if card_set in ("Tarot", "Spectral") and not (soul_used and not showman):
        if rng.pseudorandom(_soul_key(card_set, ante)) > 0.997:
            return "c_soul"
    if card_set in ("Planet", "Spectral") and not (black_hole_used
                                                   and not showman):
        if rng.pseudorandom(_soul_key(card_set, ante)) > 0.997:
            return "c_black_hole"
    return None


def _pack_consumable(rng: RunRng, card_set: str, ante: int, append: str,
                     played_hands, seen, showman) -> dict:
    forced = _soulable(rng, card_set, ante, "c_soul" in seen,
                       "c_black_hole" in seen, showman)
    if forced is not None:
        return {"set": "Spectral", "key": forced}
    key = draw_consumable(rng, card_set, ante, played_hands, seen, showman,
                          append=append)
    return {"set": card_set, "key": key}


def _standard_card(rng: RunRng, ante: int, edition_rate: float = 1.0) -> dict:
    """One card from a Standard pack: face, enhancement, edition, seal.

    The order matters as much as the rolls. The game decides enhanced-or-not
    first, then draws the enhancement, then the face, then the edition, then
    whether there is a seal and only then which seal -- five pools, each
    advanced whether or not anything comes of it.

    `edition_rate` is the run's, which Hone and Glow Up raise; card.lua:1761
    passes its own doubling as poll_edition's `_mod` and the game multiplies
    the two. Leaving the run's out moved exactly one boundary -- holographic
    against foil -- which is what a live run showed: `card-holo` from the
    game where the shadow had `card-foil`.
    """
    enhanced = rng.pseudorandom("stdset%d" % ante) > 0.6
    enhancement = None
    if enhanced:
        enhancement = rng.random_element(ENHANCEMENTS, "Enhancedsta%d" % ante)
    front = rng.random_element(FRONTS, "frontsta%d" % ante)
    suit, rank = front.split("_")
    edition = poll_edition(rng, "standard_edition%d" % ante, mod=2,
                           no_negative=True, edition_rate=edition_rate)
    seal = None
    if rng.pseudorandom("stdseal%d" % ante) > 0.8:          # 1 - 0.02*10
        roll = rng.pseudorandom("stdsealtype%d" % ante)
        seal = ("Red" if roll > 0.75 else "Blue" if roll > 0.5
                else "Gold" if roll > 0.25 else "Purple")
    return {"set": "Playing", "rank": rank, "suit": suit,
            "enhancement": enhancement, "edition": edition, "seal": seal}


def pack_contents(rng: RunRng, kind: str, cards: int, ante: int,
                  played_hands: Iterable[str] = (),
                  seen: Iterable[str] = (), showman: bool = False,
                  owned_enhancements: Iterable[str] = (),
                  seen_jokers: Iterable[str] = (),
                  pool_flags: Iterable[str] = (),
                  soul_used: bool = False, black_hole_used: bool = False,
                  telescope: bool = False, omen_globe: bool = False,
                  most_played_planet: str | None = None,
                  stickers: dict | None = None,
                  edition_rate: float = 1.0) -> list[dict]:
    """Everything a pack offers, in the order the game creates it.

    The simulator drew pack contents uniformly from whole card sets, which is
    wrong twice over: it ignores the pool -- so it offers a Tarot the run has
    already seen, or Planet X for a hand nobody has played -- and it ignores
    the stream, so every draw afterwards is off by however many rolls the pack
    should have taken.
    """
    append = PACK_APPEND[kind]
    # A card marks its own centre used the moment it is constructed -- see
    # Card:set_ability -- not when the player takes it. So a pack blanks each
    # card it has just made from the pool the next one draws from, and cannot
    # offer the same Tarot twice. G.GAME.used_jokers is one table for jokers
    # and consumables alike, which is why one set covers both here.
    made = set(seen) | set(seen_jokers) | ({"c_soul"} if soul_used else set())         | ({"c_black_hole"} if black_hole_used else set())
    out = []
    for i in range(1, cards + 1):
        if kind == "Arcana":
            if omen_globe and rng.pseudorandom("omen_globe") > 0.8:
                card = _pack_consumable(rng, "Spectral", ante, "ar2",
                                        played_hands, made, showman)
            else:
                card = _pack_consumable(rng, "Tarot", ante, append,
                                        played_hands, made, showman)
        elif kind == "Celestial":
            # The Telescope voucher forces the first card to the planet for
            # the hand the run has played most, with no roll at all.
            if telescope and i == 1 and most_played_planet:
                card = {"set": "Planet", "key": most_played_planet}
            else:
                card = _pack_consumable(rng, "Planet", ante, append,
                                        played_hands, made, showman)
        elif kind == "Spectral":
            card = _pack_consumable(rng, "Spectral", ante, append,
                                    played_hands, made, showman)
        elif kind == "Buffoon":
            key = draw_joker(rng, ante, owned_enhancements, made,
                             showman, pool_flags=pool_flags, append=append)
            # A pack joker takes the same sticker polls a shop joker does,
            # under the pack's own pool names.
            marks = poll_stickers(rng, ante, in_pack=True, **(stickers or {}))
            edition = poll_edition(rng, "edi%s%d" % (append, ante))
            card = {"set": "Joker", "key": key, "edition": edition, **marks}
        elif kind == "Standard":
            card = _standard_card(rng, ante, edition_rate)
        else:
            raise ValueError("unknown pack kind %r" % kind)
        if not showman and card.get("key"):
            made.add(card["key"])
        out.append(card)
    return out


# --------------------------------------------------------------------------
# vouchers
# --------------------------------------------------------------------------

NAME_BY_VOUCHER_KEY = {row[0]: row[1] for row in VOUCHER_DATA}


def build_voucher_pool(redeemed: Iterable[str] = (),
                       on_offer: Iterable[str] = ()) -> list[str]:
    """The voucher pool, blanked the same way as every other.

    Three things take an entry out. A voucher already redeemed cannot come
    again -- unlike a joker, there is no Showman that brings it back. An
    upgrade is gated on its base having been redeemed, so half the list is
    blank at the start of a run. And a voucher already sitting in the shop is
    withheld, which matters when a Voucher Tag adds a second one.
    """
    owned, offered = set(redeemed), set(on_offer)
    pool = []
    for key, _name, _cost, requires, _extra in VOUCHER_DATA:
        if key in owned or key in offered:
            pool.append(UNAVAILABLE)
        elif requires and requires not in owned:
            pool.append(UNAVAILABLE)
        else:
            pool.append(key)
    return pool


def draw_voucher(rng: RunRng, ante: int, redeemed: Iterable[str] = (),
                 on_offer: Iterable[str] = (), from_tag: bool = False) -> str:
    """The voucher this round's shop offers, as get_next_voucher_key rolls it.

    Rolled when the round starts rather than when the shop opens, which is
    why it is drawn against the ante and not the shop.
    """
    pool = _or_fallback(build_voucher_pool(redeemed, on_offer), "Voucher")
    key = "Voucher_fromtag" if from_tag else "Voucher%d" % ante
    return _draw(rng, pool, key)


# The reverse of the NAME_BY_* maps: the simulator holds objects with display
# names and the pools are keyed by centre key.
KEY_BY_JOKER_NAME = {name: key for key, name in NAME_BY_JOKER_KEY.items()}
KEY_BY_CONSUMABLE_NAME = {name: key
                          for key, name in NAME_BY_CONSUMABLE_KEY.items()}


# --------------------------------------------------------------------------
# stickers
# --------------------------------------------------------------------------

# Which stickers a joker will take at all. Card:set_eternal and
# Card:set_perishable (card.lua:506, 513) drop the sticker when the centre
# refuses it -- a joker that destroys itself is never eternal, and one whose
# whole value is a counter it would lose is never perishable. The poll happens
# either way; only the sticker is refused, so the stream is unaffected.
STICKER_COMPAT = {name: (eternal_ok, perishable_ok)
                  for name, (_k, _r, _o, _u, _g, _n, _y, eternal_ok,
                             perishable_ok) in JOKER_DATA.items()}


def takes_sticker(name: str) -> tuple[bool, bool]:
    """(eternal, perishable) for this joker, by the game's own centre flags."""
    return STICKER_COMPAT.get(name, (True, True))


def poll_stickers(rng: RunRng, ante: int, in_pack: bool = False,
                  eternals: bool = False, perishables: bool = False,
                  rentals: bool = False) -> dict:
    """Eternal, perishable and rental, as create_card polls them.

    Every joker made for a shop or a Buffoon pack takes this poll, and the
    first draw happens *whether or not any sticker is enabled* -- the game
    reads the roll into a local and only then asks whether the stake allows
    anything. So a White-stake run still spends it, and a simulator that
    skips it stands one draw behind on that pool for the rest of the run.
    The rental roll is different: it sits behind an `and`, so it is only
    spent when rentals are on.

    The names change inside a pack: "packetper" and "packssjr" rather than
    "etperpoll" and "ssjr".
    """
    out = {"eternal": False, "perishable": False, "rental": False}
    poll = rng.pseudorandom("%s%d" % ("packetper" if in_pack else "etperpoll",
                                      ante))
    if eternals and poll > 0.7:
        out["eternal"] = True
    elif perishables and 0.4 < poll <= 0.7:
        out["perishable"] = True
    if rentals and rng.pseudorandom(
            "%s%d" % ("packssjr" if in_pack else "ssjr", ante)) > 0.7:
        out["rental"] = True
    return out
