"""Shop contents: joker/consumable slots, vouchers and booster packs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .cards import Card, Edition
from .consumables import ConsumableKind, ConsumableSpec
from .jokers import BASE_COST, REGISTRY as JOKER_REGISTRY, JokerInstance, JokerSpec, Rarity
from .pack_data import PACK_DATA

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
    name: str
    text: str
    cost: int = 10
    shop_slots: int = 0
    consumable_slots: int = 0
    extra_hands: int = 0
    extra_discards: int = 0
    reroll_discount: int = 0
    price_multiplier: float = 1.0
    interest_cap: int = 0
    joker_slots: int = 0
    hand_size: int = 0


VOUCHERS: list[Voucher] = [
    Voucher("Overstock", "+1 card slot available in shop", shop_slots=1),
    Voucher("Clearance Sale", "All cards and packs in shop are 25% off",
            price_multiplier=0.75),
    Voucher("Crystal Ball", "+1 consumable slot", consumable_slots=1),
    Voucher("Reroll Surplus", "Rerolls cost $2 less", reroll_discount=2),
    Voucher("Grabber", "+1 hand each round", extra_hands=1),
    Voucher("Wasteful", "+1 discard each round", extra_discards=1),
    Voucher("Seed Money", "Raise the interest cap to $10", interest_cap=10),
    Voucher("Money Tree", "Raise the interest cap to $20", interest_cap=20),
    Voucher("Antimatter", "+1 Joker slot", cost=20, joker_slots=1),
    Voucher("Paint Brush", "+1 hand size", hand_size=1),
    Voucher("Blank", "Does nothing?"),
]


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

    def reroll_cost(self, discount: int = 0) -> int:
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
