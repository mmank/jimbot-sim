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
    # Every hand the played cards contain, which is what the "if hand
    # contains a Pair" jokers actually ask about.
    contains: frozenset = frozenset()
    chips: float = 0.0
    mult: float = 0.0
    money_gained: int = 0

    @property
    def money(self) -> int:
        """The dollars a joker scoring *now* would see.

        `money_gained` is banked once, after the hand -- see
        `GameState._score`, which calls `add_money(ctx.money_gained,
        "cards")`. The game does not wait: `ease_dollars` runs as each card
        pays, so a joker further right in the row reads the larger number.

        Recording 10 stopped on the difference. A Flush of five Diamonds
        with Rough Gem left of Bull: Rough Gem pays $1 a Diamond and Hack
        retriggers the Five, so six dollars land during the hand. Bull is
        "+2 Chips per dollar held" and read $27 instead of $33 -- 54 chips
        against 66, and 5427 against the game's 5913 on a hand that decided
        the blind.
        """
        return max(0, self.game.money + self.money_gained)
    log: list[str] = field(default_factory=list)

    # Every line carries the running totals as well as the change. Without
    # them the log says what happened and not what it added up to, and a hand
    # that scores wrong is a question about the total at each step -- which
    # joker took it away from what the real game reached. Recording 8 stops on
    # exactly that question at step 236, 364845 against 243620.
    def add_chips(self, amount: float, source: str = "") -> None:
        if amount:
            self.chips += amount
            self.log.append("%s: +%g chips -> %g x %g"
                            % (source, amount, self.chips, self.mult))

    def add_mult(self, amount: float, source: str = "") -> None:
        if amount:
            self.mult += amount
            self.log.append("%s: +%g mult -> %g x %g"
                            % (source, amount, self.chips, self.mult))

    def times_mult(self, factor: float, source: str = "") -> None:
        if factor != 1:
            self.mult *= factor
            self.log.append("%s: x%g mult -> %g x %g"
                            % (source, factor, self.chips, self.mult))

    @property
    def score(self) -> int:
        # Balatro truncates the product of chips and mult.
        return int(self.chips * self.mult)
