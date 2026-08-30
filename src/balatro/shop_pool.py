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
from .rng import RunRng

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


def roll_rarity(rng: RunRng, ante: int) -> int:
    """1 common, 2 uncommon, 3 rare. Legendary never comes from a shop."""
    roll = rng.pseudorandom("rarity%d" % ante)
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
               pool_flags: Iterable[str] = ()) -> str:
    """One shop joker, as the game would roll it."""
    if rarity is None:
        rarity = roll_rarity(rng, ante)
    pool = build_pool(rarity, owned_enhancements, seen_jokers, showman,
                      pool_flags)
    key = "Joker%d%d" % (rarity, ante)
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
                    seen: Iterable[str] = (), showman: bool = False) -> str:
    """One consumable, as the game would roll it."""
    pool = build_consumable_pool(card_set, played_hands, seen, showman)
    key = "%s%d" % (card_set, ante)
    center = rng.random_element(pool, key)
    attempt = 1
    while center == UNAVAILABLE:
        attempt += 1
        center = rng.random_element(pool, "%s_resample%d" % (key, attempt))
    return center
