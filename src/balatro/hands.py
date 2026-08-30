"""Poker hand detection and hand-level bookkeeping."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import IntEnum

from .cards import Card, Rank, Suit


class HandType(IntEnum):
    HIGH_CARD = 0
    PAIR = 1
    TWO_PAIR = 2
    THREE_OF_A_KIND = 3
    STRAIGHT = 4
    FLUSH = 5
    FULL_HOUSE = 6
    FOUR_OF_A_KIND = 7
    STRAIGHT_FLUSH = 8
    FIVE_OF_A_KIND = 9
    FLUSH_HOUSE = 10
    FLUSH_FIVE = 11

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()


# level 1 (chips, mult) and the per-level increment (chips, mult)
BASE_VALUES: dict[HandType, tuple[int, int]] = {
    HandType.HIGH_CARD: (5, 1),
    HandType.PAIR: (10, 2),
    HandType.TWO_PAIR: (20, 2),
    HandType.THREE_OF_A_KIND: (30, 3),
    HandType.STRAIGHT: (30, 4),
    HandType.FLUSH: (35, 4),
    HandType.FULL_HOUSE: (40, 4),
    HandType.FOUR_OF_A_KIND: (60, 7),
    HandType.STRAIGHT_FLUSH: (100, 8),
    HandType.FIVE_OF_A_KIND: (120, 12),
    HandType.FLUSH_HOUSE: (140, 14),
    HandType.FLUSH_FIVE: (160, 16),
}

LEVEL_GAIN: dict[HandType, tuple[int, int]] = {
    HandType.HIGH_CARD: (10, 1),
    HandType.PAIR: (15, 1),
    HandType.TWO_PAIR: (20, 1),
    HandType.THREE_OF_A_KIND: (20, 2),
    HandType.STRAIGHT: (30, 3),
    HandType.FLUSH: (15, 2),
    HandType.FULL_HOUSE: (25, 2),
    HandType.FOUR_OF_A_KIND: (30, 3),
    HandType.STRAIGHT_FLUSH: (40, 4),
    HandType.FIVE_OF_A_KIND: (35, 3),
    HandType.FLUSH_HOUSE: (40, 4),
    HandType.FLUSH_FIVE: (50, 3),
}

# Planet card that levels each hand, for shop generation.
PLANET_FOR_HAND: dict[HandType, str] = {
    HandType.HIGH_CARD: "Pluto",
    HandType.PAIR: "Mercury",
    HandType.TWO_PAIR: "Uranus",
    HandType.THREE_OF_A_KIND: "Venus",
    HandType.STRAIGHT: "Saturn",
    HandType.FLUSH: "Jupiter",
    HandType.FULL_HOUSE: "Earth",
    HandType.FOUR_OF_A_KIND: "Mars",
    HandType.STRAIGHT_FLUSH: "Neptune",
    HandType.FIVE_OF_A_KIND: "Planet X",
    HandType.FLUSH_HOUSE: "Ceres",
    HandType.FLUSH_FIVE: "Eris",
}


@dataclass
class HandLevels:
    """Per-run level and play-count for every poker hand."""

    levels: dict[HandType, int]
    plays: dict[HandType, int]

    @classmethod
    def new(cls) -> "HandLevels":
        return cls({h: 1 for h in HandType}, {h: 0 for h in HandType})

    def level_up(self, hand: HandType, times: int = 1) -> None:
        self.levels[hand] += times

    def values(self, hand: HandType) -> tuple[int, int]:
        base_chips, base_mult = BASE_VALUES[hand]
        gain_chips, gain_mult = LEVEL_GAIN[hand]
        extra = self.levels[hand] - 1
        return base_chips + gain_chips * extra, base_mult + gain_mult * extra


@dataclass(frozen=True)
class HandResult:
    hand: HandType
    scoring: tuple[Card, ...]


_SMEARED_PAIRS = {Suit.HEARTS: Suit.DIAMONDS, Suit.DIAMONDS: Suit.HEARTS,
                  Suit.SPADES: Suit.CLUBS, Suit.CLUBS: Suit.SPADES}


def _flush_cards(cards: list[Card], needed: int,
                 smeared: bool = False) -> list[Card] | None:
    """Largest same-suit group (wilds count everywhere), if big enough.

    Smeared Joker collapses four suits into two, which changes what *is* a
    flush rather than what one scores -- A 3 5 7 9 in mixed spades and clubs
    is a flush only because of it.
    """
    def matches(card: Card, suit: Suit) -> bool:
        if card.counts_as_suit(suit):
            return True
        return smeared and card.counts_as_suit(_SMEARED_PAIRS[suit])

    best: list[Card] | None = None
    for suit in Suit:
        group = [c for c in cards if matches(c, suit)]
        if len(group) >= needed and (best is None or len(group) > len(best)):
            best = group
    return best


def _straight_cards(cards: list[Card], needed: int, shortcut: bool) -> list[Card] | None:
    """Longest run of distinct ranks; Ace plays high or low."""
    playable = [c for c in cards if not c.is_stone and not c.debuffed]
    by_rank: dict[int, Card] = {}
    for c in playable:
        by_rank.setdefault(c.rank.value, c)
    if Rank.ACE.value in by_rank:
        by_rank.setdefault(1, by_rank[Rank.ACE.value])

    max_gap = 2 if shortcut else 1
    best: list[Card] | None = None
    for start in sorted(by_rank):
        run = [by_rank[start]]
        current = start
        for nxt in sorted(v for v in by_rank if v > start):
            if nxt - current <= max_gap:
                run.append(by_rank[nxt])
                current = nxt
        if len(run) >= needed and (best is None or len(run) > len(best)):
            best = run
    return best


def evaluate(
    cards: list[Card],
    *,
    four_fingers: bool = False,
    shortcut: bool = False,
    splash: bool = False,
    smeared: bool = False,
) -> HandResult:
    """Classify a played hand and return the cards that score."""
    if not cards:
        raise ValueError("cannot evaluate an empty hand")

    stones = [c for c in cards if c.is_stone]
    ranked = [c for c in cards if not c.is_stone]
    counts = Counter(c.rank for c in ranked)

    needed = 4 if four_fingers else 5
    flush = _flush_cards(cards, needed, smeared)
    straight = _straight_cards(cards, needed, shortcut)

    def of_rank(n: int) -> list[Card] | None:
        for rank, cnt in counts.most_common():
            if cnt >= n:
                return [c for c in ranked if c.rank is rank][:n]
        return None

    five = of_rank(5)
    four = of_rank(4)
    trips = of_rank(3)
    pairs = [rank for rank, cnt in counts.items() if cnt >= 2]

    full_house: list[Card] | None = None
    if trips:
        rest = Counter(c.rank for c in ranked if c not in trips)
        for rank, cnt in rest.most_common(1):
            if cnt >= 2:
                full_house = trips + [c for c in ranked if c.rank is rank][:2]

    def result(hand: HandType, scoring: list[Card]) -> HandResult:
        # Stone cards always score, and scoring keeps the played order.
        # Splash widens the set to everything played without changing which
        # hand it is: a High Card with Splash still scores as a High Card, but
        # all five cards contribute their chips.
        if splash:
            return HandResult(hand, tuple(cards))
        chosen = {c.uid for c in scoring} | {c.uid for c in stones}
        return HandResult(hand, tuple(c for c in cards if c.uid in chosen))

    if five and flush:
        return result(HandType.FLUSH_FIVE, five)
    if full_house and flush:
        return result(HandType.FLUSH_HOUSE, full_house)
    if five:
        return result(HandType.FIVE_OF_A_KIND, five)
    if straight and flush:
        both = [c for c in straight if c in flush]
        if len(both) >= needed:
            # Every card in either part scores, not only the overlap. With
            # Four Fingers a four-card flush can sit inside a five-card
            # straight, and the game scores all five: A 3 5 7 9 with the 5 off
            # suit is a Straight Flush worth (100 + 35) x 8, not (100 + 30).
            union = straight + [c for c in flush if c not in straight]
            return result(HandType.STRAIGHT_FLUSH, union)
    if four:
        return result(HandType.FOUR_OF_A_KIND, four)
    if full_house:
        return result(HandType.FULL_HOUSE, full_house)
    if flush:
        return result(HandType.FLUSH, flush)
    if straight:
        return result(HandType.STRAIGHT, straight)
    if trips:
        return result(HandType.THREE_OF_A_KIND, trips)
    if len(pairs) >= 2:
        top = sorted(pairs, key=lambda r: r.value, reverse=True)[:2]
        return result(HandType.TWO_PAIR, [c for c in ranked if c.rank in top][:4])
    if pairs:
        return result(HandType.PAIR, [c for c in ranked if c.rank is pairs[0]][:2])
    if ranked:
        high = max(ranked, key=lambda c: c.rank.value)
        return result(HandType.HIGH_CARD, [high])
    return result(HandType.HIGH_CARD, [])
