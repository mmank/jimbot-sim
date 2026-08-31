"""Shop contents: joker/consumable slots, vouchers and booster packs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .cards import Card, Edition
from .consumables import ConsumableKind, ConsumableSpec
from .jokers import BASE_COST, REGISTRY as JOKER_REGISTRY, JokerInstance, JokerSpec, Rarity
from .pack_data import PACK_DATA
from .voucher_data import VOUCHER_DATA

RARITY_WEIGHTS = {Rarity.COMMON: 0.70, Rarity.UNCOMMON: 0.25, Rarity.RARE: 0.05}
SLOT_WEIGHTS = {"joker": 20, "tarot": 4, "planet": 4}

EDITION_WEIGHTS = {
    Edition.NONE: 0.96,
    Edition.FOIL: 0.02,
    Edition.HOLOGRAPHIC: 0.014,
    Edition.POLYCHROME: 0.006,
}
EDITION_PREMIUM = {
    Edition.NONE: 0, Edition.FOIL: 2, Edition.HOLOGRAPHIC: 3,
    Edition.POLYCHROME: 5, Edition.NEGATIVE: 5,
}


@dataclass(frozen=True)
class Voucher:
    """One voucher, with the run state its redemption moves.

    Every field here is something Card:apply_to_run sets. The ones it does not
    set -- Telescope, Observatory, Omen Globe, Director's Cut -- are read where
    they act rather than applied on redemption, so they carry no numbers and
    are recognised by key.
    """

    key: str
    name: str
    cost: int = 10
    requires: str = ""
    shop_slots: int = 0
    consumable_slots: int = 0
    extra_hands: int = 0
    extra_discards: int = 0
    reroll_discount: int = 0
    discount_percent: int = 0
    interest_cap: int = 0
    joker_slots: int = 0
    hand_size: int = 0
    ante_shift: int = 0
    tarot_rate: float = 0.0
    planet_rate: float = 0.0
    edition_rate: float = 0.0
    playing_card_rate: float = 0.0

    @property
    def price_multiplier(self) -> float:
        return 1.0 - self.discount_percent / 100.0


# What each voucher does, keyed by the game's name, as a function of the
# `extra` the game stores alongside it. Taken from Card:apply_to_run --
# an upgrade is usually its base with a bigger `extra`, which is why the
# effects are written once for both.
def _voucher_effect(name: str, extra: float) -> dict:
    if name in ("Overstock", "Overstock Plus"):
        return {"shop_slots": 1}
    if name in ("Tarot Merchant", "Tarot Tycoon"):
        return {"tarot_rate": 4 * extra}
    if name in ("Planet Merchant", "Planet Tycoon"):
        return {"planet_rate": 4 * extra}
    if name in ("Hone", "Glow Up"):
        return {"edition_rate": extra}
    if name in ("Magic Trick", "Illusion"):
        return {"playing_card_rate": extra}
    if name == "Crystal Ball":
        return {"consumable_slots": 1}
    if name in ("Clearance Sale", "Liquidation"):
        return {"discount_percent": int(extra)}
    if name in ("Reroll Surplus", "Reroll Glut"):
        return {"reroll_discount": int(extra)}
    if name in ("Seed Money", "Money Tree"):
        # The game stores the cap in dollars held and pays one interest per
        # five of them: min(floor(dollars/5), interest_cap/5). The simulator
        # counts the payments, so fifty dollars held is a cap of ten.
        return {"interest_cap": int(extra) // 5}
    if name in ("Grabber", "Nacho Tong"):
        return {"extra_hands": int(extra)}
    if name in ("Wasteful", "Recyclomancy"):
        return {"extra_discards": int(extra)}
    if name in ("Paint Brush", "Palette"):
        return {"hand_size": 1}
    if name == "Antimatter":
        return {"joker_slots": 1}
    # Hieroglyph and Petroglyph each take an ante away and pay for it with a
    # hand or a discard. Fewer antes is the whole point of them.
    if name == "Hieroglyph":
        return {"ante_shift": -int(extra), "extra_hands": -int(extra)}
    if name == "Petroglyph":
        return {"ante_shift": -int(extra), "extra_discards": -int(extra)}
    return {}                        # Blank, Telescope, Omen Globe, Retcon...


VOUCHERS: list[Voucher] = [
    Voucher(key, name, cost, requires, **_voucher_effect(name, extra))
    for key, name, cost, requires, extra in VOUCHER_DATA
]
VOUCHER_BY_KEY: dict[str, Voucher] = {v.key: v for v in VOUCHERS}


class PackKind(Enum):
    ARCANA = "arcana"
    CELESTIAL = "celestial"
    STANDARD = "standard"
    BUFFOON = "buffoon"
    SPECTRAL = "spectral"


@dataclass(frozen=True)
class PackSpec:
    kind: PackKind
    size: str          # "normal" | "jumbo" | "mega"
    options: int       # cards shown
    picks: int         # cards you may take
    cost: int
    key: str = ""      # the game's own centre key, e.g. p_arcana_mega_1

    @property
    def name(self) -> str:
        prefix = {"normal": "", "jumbo": "Jumbo ", "mega": "Mega "}[self.size]
        return f"{prefix}{self.kind.value.title()} Pack"


def pack_from_row(row) -> PackSpec:
    """A PackSpec from one row of the game's Booster pool."""
    key, kind, _weight, choose, cards, cost = row
    return PackSpec(PackKind(kind.lower()), key.split("_")[2],
                    cards, choose, cost, key)


PACKS: list[PackSpec] = [pack_from_row(row) for row in PACK_DATA]
BY_KEY: dict[str, PackSpec] = {pack.key: pack for pack in PACKS}


def pack_from_key(key: str) -> PackSpec:
    return BY_KEY[key]


@dataclass
class ShopSlot:
    """One purchasable item in the shop's main row."""

    kind: str  # "joker" | "consumable" | "card"
    price: int
    # An edition tag or the Coupon Tag marks a card couponed, which is the
    # game's way of saying "this one is free" -- set_cost zeroes it.
    couponed: bool = False
    joker: JokerInstance | None = None
    consumable: ConsumableSpec | None = None
    card: Card | None = None

    @property
    def label(self) -> str:
        if self.joker is not None:
            return repr(self.joker)
        if self.consumable is not None:
            return self.consumable.name
        return repr(self.card)


@dataclass
class Shop:
    slots: list[ShopSlot] = field(default_factory=list)
    packs: list[PackSpec] = field(default_factory=list)
    voucher: Voucher | None = None
    voucher_bought: bool = False
    rerolls: int = 0
    # Chaos the Clown's free reroll. current_round.free_rerolls in the game,
    # topped up as the shop opens.
    free_rerolls: int = 0

    def reroll_cost(self, discount: int = 0) -> int:
        """What the next reroll costs.

        A free reroll is free *and* does not raise the price of the next one:
        calculate_reroll_cost returns before it increments, so Chaos the
        Clown's reroll is genuinely a spare rather than a discount on the
        first of a series.
        """
        if self.free_rerolls > 0:
            return 0
        return max(0, 5 + self.rerolls - discount)


def joker_price(spec: JokerSpec, edition: Edition = Edition.NONE) -> int:
    return spec.cost + EDITION_PREMIUM[edition]


def weighted_pick(rng, name: str, weights: dict):
    """A weighted choice over a named pool.

    NOT the game's algorithm. The real one rolls once against summed rates and
    walks fixed bands in a fixed order -- see shop_pool.roll_slot_type, which
    is checked against the engine. This survives only for booster packs, whose
    generation has not been reproduced yet, and it is deliberately left
    obviously ad hoc so it is not mistaken for verified behaviour.
    """
    keys = list(weights)
    total = sum(weights.values())
    roll = rng.pseudorandom(name) * total
    upto = 0.0
    for key in keys:
        upto += weights[key]
        if roll <= upto:
            return key
    return keys[-1]


def random_joker_spec(rng, name: str = "shop_joker",
                      allowed: set[str] | None = None) -> JokerSpec:
    """Unverified. Shop jokers go through shop_pool.draw_joker instead."""
    rarity = weighted_pick(rng, f"{name}_rarity", RARITY_WEIGHTS)
    pool = [s for s in JOKER_REGISTRY.values()
            if s.rarity is rarity and (allowed is None or s.name in allowed)]
    if not pool:
        pool = [s for s in JOKER_REGISTRY.values() if s.rarity is Rarity.COMMON]
    return rng.choice(f"{name}_pick", pool)


def random_edition(rng, name: str = "shop_edition") -> Edition:
    return weighted_pick(rng, name, EDITION_WEIGHTS)
