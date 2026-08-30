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
from .rng import TW223, RunRng

UNAVAILABLE = "UNAVAILABLE"

# key -> the enhancement a run must already own before the shop offers it.
GATES = {key: gate
         for _, (key, _r, _o, _u, gate, _n, _y) in JOKER_DATA.items() if gate}

# Flags on the run's history rather than its deck. A joker with no_flag leaves
# the pool once that flag is set, and one with yes_flag only enters after it
# is: Gros Michel and Cavendish are the pair, and which of the two a shop can
# offer depends on whether Gros Michel has gone extinct this run. Neither
# joker's text says so.
NO_FLAG = {key: flag
           for _, (key, _r, _o, _u, _g, flag, _y) in JOKER_DATA.items() if flag}
YES_FLAG = {key: flag
            for _, (key, _r, _o, _u, _g, _n, flag) in JOKER_DATA.items() if flag}


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
    key = "Joker%d%s%d" % (rarity, append, ante)
    center = rng.random_element(pool, key)
    attempt = 1
    while center == UNAVAILABLE:
        attempt += 1
        center = rng.random_element(pool, "%s_resample%d" % (key, attempt))
    return center


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
    key = "%s%s%d" % (card_set, append, ante)
    center = rng.random_element(pool, key)
    attempt = 1
    while center == UNAVAILABLE:
        attempt += 1
        center = rng.random_element(pool, "%s_resample%d" % (key, attempt))
    return center


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
                 no_negative: bool = False, edition_rate: float = 1.0) -> str:
    """The game's poll_edition: one roll, compared against stacked bands.

    Written as descending thresholds off 1.0 rather than as weights, because
    that is how the game writes it and the two are not the same when a
    modifier scales them: `mod` widens every band from the top, so the
    boundaries move relative to each other rather than in proportion.

    Returns "none", "foil", "holo", "polychrome" or "negative".
    """
    poll = rng.pseudorandom(key)
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
        # p_buffoon_normal_1 or _2, chosen with math.random(1, 2) -- the game
        # does not use a named pool for this one.
        index = int(TW223(rng.pseudoseed("buffoon_first")).random(1, 2))
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
    pool = build_tag_pool(ante, discovered)
    key = "Tag%s%d" % (append, ante)
    tag = rng.random_element(pool, key)
    attempt = 1
    while tag == UNAVAILABLE:
        attempt += 1
        tag = rng.random_element(pool, "%s_resample%d" % (key, attempt))
    return tag
