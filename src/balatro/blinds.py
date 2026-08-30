"""Blind schedule, ante scaling and boss blind effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .cards import Suit

# White-stake base chip requirement per ante.
ANTE_BASE: dict[int, int] = {
    0: 100, 1: 300, 2: 800, 3: 2000, 4: 5000,
    5: 11000, 6: 20000, 7: 35000, 8: 50000,
}


def ante_base_chips(ante: int) -> int:
    """Endless mode keeps growing roughly geometrically past ante 8."""
    if ante in ANTE_BASE:
        return ANTE_BASE[ante]
    chips = ANTE_BASE[8]
    for _ in range(ante - 8):
        chips = int(chips * 1.6)
    return chips


class BlindKind(Enum):
    SMALL = "small"
    BIG = "big"
    BOSS = "boss"


BLIND_MULT = {BlindKind.SMALL: 1.0, BlindKind.BIG: 1.5, BlindKind.BOSS: 2.0}
BLIND_REWARD = {BlindKind.SMALL: 3, BlindKind.BIG: 4, BlindKind.BOSS: 5}


@dataclass(frozen=True)
class BossEffect:
    """Declarative boss modifiers; the engine reads these fields directly.

    Bosses whose effect is purely about face-down cards are represented with no
    mechanical modifier, since this engine has full information anyway.
    """

    name: str
    text: str
    chip_mult: float = 2.0
    debuff_suit: Suit | None = None
    debuff_face: bool = False
    hand_size_delta: int = 0
    hands_delta: int = 0
    discards_delta: int = 0
    min_cards_played: int = 0
    money_per_card_played: int = 0
    zero_money_on_most_played: bool = False
    discard_random_on_play: int = 0
    level_down_played_hand: bool = False
    no_repeat_hand: bool = False
    lock_first_hand_type: bool = False
    debuff_previously_played: bool = False
    halve_base: bool = False
    is_finisher: bool = False


BOSSES: list[BossEffect] = [
    BossEffect("The Hook", "Discards 2 random cards per hand played",
               discard_random_on_play=2),
    BossEffect("The Ox", "Playing your most played hand sets money to $0",
               zero_money_on_most_played=True),
    BossEffect("The House", "First hand is drawn face down"),
    BossEffect("The Wall", "Extra large blind", chip_mult=4.0),
    BossEffect("The Wheel", "1 in 7 cards get drawn face down"),
    BossEffect("The Arm", "Decrease level of played poker hand",
               level_down_played_hand=True),
    BossEffect("The Club", "All Club cards are debuffed", debuff_suit=Suit.CLUBS),
    BossEffect("The Fish", "Cards drawn face down after each hand played"),
    BossEffect("The Psychic", "Must play 5 cards", min_cards_played=5),
    BossEffect("The Goad", "All Spade cards are debuffed", debuff_suit=Suit.SPADES),
    BossEffect("The Water", "Start with 0 discards", discards_delta=-99),
    BossEffect("The Window", "All Diamond cards are debuffed", debuff_suit=Suit.DIAMONDS),
    BossEffect("The Manacle", "-1 hand size", hand_size_delta=-1),
    BossEffect("The Eye", "No repeat hand types this round", no_repeat_hand=True),
    BossEffect("The Mouth", "Play only one hand type this round",
               lock_first_hand_type=True),
    BossEffect("The Plant", "All face cards are debuffed", debuff_face=True),
    BossEffect("The Serpent", "After play or discard, always draw 3 cards"),
    BossEffect("The Pillar", "Cards played earlier this ante are debuffed",
               debuff_previously_played=True),
    BossEffect("The Needle", "Play only 1 hand", hands_delta=-99),
    BossEffect("The Head", "All Heart cards are debuffed", debuff_suit=Suit.HEARTS),
    BossEffect("The Tooth", "Lose $1 per card played", money_per_card_played=-1),
    BossEffect("The Flint", "Base Chips and Mult are halved", halve_base=True),
    BossEffect("The Mark", "All face cards are drawn face down"),
]

FINISHER_BOSSES: list[BossEffect] = [
    BossEffect("Amber Acorn", "Flips and shuffles all Jokers", is_finisher=True),
    BossEffect("Verdant Leaf", "All cards debuffed until a Joker is sold",
               is_finisher=True),
    BossEffect("Violet Vessel", "Very large blind", chip_mult=6.0, is_finisher=True),
    BossEffect("Crimson Heart", "One random Joker disabled each hand", is_finisher=True),
    BossEffect("Cerulean Bell", "Forces one card to always be selected",
               is_finisher=True),
]


@dataclass
class Blind:
    kind: BlindKind
    ante: int
    target: int
    reward: int
    boss: BossEffect | None = None

    @property
    def name(self) -> str:
        if self.boss is not None:
            return self.boss.name
        return f"{self.kind.value.title()} Blind"


def make_blind(kind: BlindKind, ante: int, boss: BossEffect | None = None) -> Blind:
    mult = boss.chip_mult if (kind is BlindKind.BOSS and boss) else BLIND_MULT[kind]
    return Blind(
        kind=kind,
        ante=ante,
        target=int(ante_base_chips(ante) * mult),
        reward=BLIND_REWARD[kind],
        boss=boss,
    )


def pick_boss(rng, ante: int) -> BossEffect:
    if ante % 8 == 0:
        return rng.choice("boss", FINISHER_BOSSES)
    return rng.choice("boss", BOSSES)
