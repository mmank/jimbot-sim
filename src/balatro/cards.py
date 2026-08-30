"""Playing cards, enhancements, editions and seals."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from itertools import count


class Suit(Enum):
    SPADES = "S"
    HEARTS = "H"
    DIAMONDS = "D"
    CLUBS = "C"

    @property
    def is_red(self) -> bool:
        return self in (Suit.HEARTS, Suit.DIAMONDS)


class Rank(Enum):
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    JACK = 11
    QUEEN = 12
    KING = 13
    ACE = 14

    @property
    def chips(self) -> int:
        if self is Rank.ACE:
            return 11
        return min(self.value, 10)

    @property
    def is_face(self) -> bool:
        return self in (Rank.JACK, Rank.QUEEN, Rank.KING)

    @property
    def short(self) -> str:
        return {
            Rank.TEN: "T", Rank.JACK: "J", Rank.QUEEN: "Q",
            Rank.KING: "K", Rank.ACE: "A",
        }.get(self, str(self.value))


class Enhancement(Enum):
    NONE = "none"
    BONUS = "bonus"      # +30 chips
    MULT = "mult"        # +4 mult
    WILD = "wild"        # counts as every suit
    GLASS = "glass"      # x2 mult, 1 in 4 chance to shatter
    STEEL = "steel"      # x1.5 mult while held in hand
    STONE = "stone"      # +50 chips, no rank/suit
    GOLD = "gold"        # +$3 if held at end of round
    LUCKY = "lucky"      # 1 in 5 for +20 mult, 1 in 15 for $20


class Edition(Enum):
    NONE = "none"
    FOIL = "foil"              # +50 chips
    HOLOGRAPHIC = "holo"       # +10 mult
    POLYCHROME = "polychrome"  # x1.5 mult
    NEGATIVE = "negative"      # +1 joker slot (jokers only)


class Seal(Enum):
    NONE = "none"
    GOLD = "gold"      # +$3 when scored
    RED = "red"        # retrigger the card once
    BLUE = "blue"      # creates a Planet card for the played hand if held
    PURPLE = "purple"  # creates a Tarot card when discarded


_ids = count()


@dataclass(eq=False)
class Card:
    rank: Rank
    suit: Suit
    enhancement: Enhancement = Enhancement.NONE
    edition: Edition = Edition.NONE
    seal: Seal = Seal.NONE
    extra_chips: int = 0  # permanent bonus from Hiker etc.
    uid: int = field(default_factory=lambda: next(_ids))
    debuffed: bool = False

    @property
    def is_stone(self) -> bool:
        return self.enhancement is Enhancement.STONE

    @property
    def base_chips(self) -> int:
        if self.is_stone:
            return 50
        return self.rank.chips + self.extra_chips

    def counts_as_suit(self, suit: Suit) -> bool:
        """Wild cards count as every suit; stone cards have no suit."""
        if self.debuffed or self.is_stone:
            return False
        if self.enhancement is Enhancement.WILD:
            return True
        return self.suit is suit

    def copy(self) -> "Card":
        return replace(self, uid=next(_ids))

    def __repr__(self) -> str:
        if self.is_stone:
            body = "Stone"
        else:
            body = f"{self.rank.short}{self.suit.value}"
        marks = "".join(
            m for m in (
                "" if self.enhancement in (Enhancement.NONE, Enhancement.STONE)
                else f"+{self.enhancement.value}",
                "" if self.edition is Edition.NONE else f"+{self.edition.value}",
                "" if self.seal is Seal.NONE else f"+{self.seal.value}seal",
            ) if m
        )
        return f"<{body}{marks}>"


def standard_deck() -> list[Card]:
    """The 52-card Red/Blue deck contents."""
    return [Card(rank, suit) for suit in Suit for rank in Rank]
