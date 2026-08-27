"""Shared scoring context passed to every joker and card effect."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .cards import Card
from .hands import HandType

if TYPE_CHECKING:  # pragma: no cover
    from .game import GameState


@dataclass
class ScoreContext:
    """Mutable chip/mult accumulator for one played hand."""

    hand: HandType
    scoring: tuple[Card, ...]
    played: tuple[Card, ...]
    held: tuple[Card, ...]
    game: "GameState"
    chips: float = 0.0
    mult: float = 0.0
    money_gained: int = 0
    log: list[str] = field(default_factory=list)

    def add_chips(self, amount: float, source: str = "") -> None:
        if amount:
            self.chips += amount
            self.log.append(f"{source}: +{amount:g} chips")

    def add_mult(self, amount: float, source: str = "") -> None:
        if amount:
            self.mult += amount
            self.log.append(f"{source}: +{amount:g} mult")

    def times_mult(self, factor: float, source: str = "") -> None:
        if factor != 1:
            self.mult *= factor
            self.log.append(f"{source}: x{factor:g} mult")

    @property
    def score(self) -> int:
        # Balatro truncates the product of chips and mult.
        return int(self.chips * self.mult)
