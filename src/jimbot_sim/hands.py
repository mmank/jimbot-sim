"""Poker hand detection and hand-level bookkeeping."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import IntEnum

from .cards import Card, Enhancement, Rank, Suit


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
        """The game's own name for the hand, from G.handlist.

        Title-casing the enum gives "Five Of A Kind" where the game says
        "Five of a Kind", and the name is not cosmetic: it is the key into
        G.GAME.hands, so anything comparing levels or plays by name misses.
        """
        words = self.name.replace("_", " ").title().split()
        return " ".join(w if i == 0 or w not in ("Of", "A") else w.lower()
                        for i, w in enumerate(words))


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

# The three the game starts with `visible = false`. evaluate_play switches a
# hand visible the first time it is made, and anything picking a poker hand at
# random -- To Do List, Telescope -- draws from the visible ones only.
SECRET_HANDS = frozenset({HandType.FIVE_OF_A_KIND, HandType.FLUSH_HOUSE,
                          HandType.FLUSH_FIVE})

# G.handlist, strongest first. What anything walking the hands in the game's
# stated order sees -- Telescope reads it with ipairs and a strict >, so a tie
# on plays goes to the strongest hand rather than the weakest.
HANDLIST = tuple(sorted(HandType, reverse=True))

# To Do List builds its pool by walking `pairs(G.GAME.hands)` and appending,
# then indexes into the result -- so the iteration order decides which hand a
# given roll names. That order is not defined: it is a Lua hash table, and
# measuring it three times in three processes gave
#
#   Straight Flush | Four of a Kind | ... | Pair | High Card
#   High Card | Straight Flush | Four of a Kind | ... | Pair
#   Straight Flush | Four of a Kind | ... | Pair | High Card
#
# -- the same within one process, rotated between them, because LuaJIT seeds
# its string hash per process. So the hand To Do List names is not
# reproducible from the run's seed in the real game either, and no simulator
# can match it every time. The stream is fine -- the raw draws agree exactly;
# it is only which name that index lands on that moves.
#
# HANDLIST is what we use instead: the game's own stated order, deterministic,
# and the one the two matching processes above happened to produce.

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
    # Every hand the played cards *contain*, not just the best one. The game
    # keeps a table of all of them -- results["Pair"] is set whenever two
    # cards share a rank, whatever the top hand turns out to be -- and the
    # jokers that say "if hand contains a Pair" read that table. A Flush with
    # two Kings in it contains a Pair, so Sly Joker fires on it, and testing
    # the best hand instead silently misses every one of those.
    contains: frozenset = frozenset()


_SMEARED_PAIRS = {Suit.HEARTS: Suit.DIAMONDS, Suit.DIAMONDS: Suit.HEARTS,
                  Suit.SPADES: Suit.CLUBS, Suit.CLUBS: Suit.SPADES}


_RED = (Suit.HEARTS, Suit.DIAMONDS)


def flush_suit(card: Card, suit: Suit, smeared: bool = False) -> bool:
    """`Card:is_suit(suit, nil, true)` -- the question a flush is judged on.

    The game asks its suit question two ways (card.lua:4064), and they part
    on a debuffed card. The ordinary one refuses it outright. This one --
    `flush_calc` -- reads the printed suit anyway, and only a Wild card loses
    its every-suit to a debuff, falling back to the suit it was printed as:

        if flush_calc then
            if self.ability.effect == 'Stone Card' then return false end
            if self.ability.name == "Wild Card" and not self.debuff then
                return true end
            if next(find_joker('Smeared Joker')) and
                (self.base.suit == 'Hearts' or self.base.suit == 'Diamonds')
                == (suit == 'Hearts' or suit == 'Diamonds') then
                return true end
            return self.base.suit == suit

    Hand detection used the ordinary question, so a hand of debuffed cards
    was never a flush. Seed QWERTYUI, Blue Deck, stake 1, on the headless
    engine at decision 101: The Club debuffs Clubs, Smeared Joker makes
    Spades Clubs, and A-Q-Q-9-6 of Spades and Clubs was a level-three Flush
    in the game at 135 x 26 = 3510 and a level-two Pair here at 95 x 21 =
    1995 -- exactly the 1515 chips the run parted by. Blackboard asks the
    same question, so `jokers.counts_for_flush` reads this too.
    """
    if card.is_stone:
        return False
    if card.enhancement is Enhancement.WILD and not card.debuffed:
        return True
    if smeared:
        return (card.suit in _RED) == (suit in _RED)
    return card.suit is suit


# The order get_flush tries the suits in (misc_functions.lua:525-530).
_FLUSH_ORDER = (Suit.SPADES, Suit.HEARTS, Suit.CLUBS, Suit.DIAMONDS)


def _flush_cards(cards: list[Card], needed: int,
                 smeared: bool = False) -> list[Card] | None:
    """get_flush (functions/misc_functions.lua:522): the first suit, in the
    game's order, with enough cards by `flush_suit`.

    Smeared Joker collapses four suits into two, which changes what *is* a
    flush rather than what one scores -- A 3 5 7 9 in mixed spades and clubs
    is a flush only because of it.

    The first suit to reach the count is the flush, not the biggest group
    (532-542). Only Wild cards make two suits reach it at once, and then it
    decides which cards score: with Four Fingers, four Wild cards and the
    King of Clubs are a flush of Spades -- the four Wilds -- and the King
    scores nothing. Taking the biggest group scored all five, 280 against
    the game's 240 on the headless engine; three Wilds, a Diamond and a Club
    are the Wilds and the Club, where it took the Diamond
    (tests/test_hand_evaluation_matches_engine.py).

    More than five cards are no flush at all (531), whatever their suits.
    """
    if len(cards) > 5:
        return None
    for suit in _FLUSH_ORDER:
        group = [c for c in cards if flush_suit(c, suit, smeared)]
        if len(group) >= needed:
            return group
    return None


def _straight_cards(cards: list[Card], needed: int, shortcut: bool) -> list[Card] | None:
    """get_straight (functions/misc_functions.lua:548), line for line.

    Two things the game does that a "longest run of distinct ranks" does not:

    * every card of a rank in the run is part of the straight, not one per
      rank. With Four Fingers 9 8 7 7 6 is a straight and *both* sevens score
      (`for k, v in ipairs(IDS[...]) do t[#t+1] = v end`). Keeping one card
      per rank left the second seven scoring nothing -- seed 64PUKM3K at
      decision 78, 8272 here against the game's 8580, and U1AYP8BC at
      decision 53, 3080 against 3164.
    * a debuffed card counts. get_id does not look at debuff (card.lua:957),
      so a debuffed card holds its place in a straight the way it holds its
      suit in a flush; evaluate_play then skips it when it comes to score.

    Stone cards are out: get_id gives them a negative id, outside 2..14.
    More than five cards are no straight (551), as they are no flush.
    """
    if len(cards) > 5:
        return None
    ids: dict[int, list[Card]] = {}
    for c in cards:
        if not c.is_stone:
            ids.setdefault(c.rank.value, []).append(c)

    run: list[Card] = []
    length, straight, skipped = 0, False, False
    for j in range(1, 15):
        rank = Rank.ACE.value if j == 1 else j
        if rank in ids:
            length += 1
            skipped = False
            run.extend(ids[rank])
        elif shortcut and not skipped and j != 14:
            skipped = True
        else:
            length, skipped = 0, False
            if straight:
                break
            run = []
        if length >= needed:
            straight = True
    return run if straight else None


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

    # Containment is built from groups of an *exact* size, not "at least".
    # get_X_same(3, hand) matches a rank with three cards and skips one with
    # four or five, and the game then patches a cascade on the end: a Five of
    # a Kind counts as a Four of a Kind, which counts as a Three of a Kind,
    # which counts as a Pair. Nothing in that chain reaches Two Pair.
    #
    # Reading "at least three" instead made a Five of a Kind contain a Full
    # House -- three of the five plus two of the same five -- which the game
    # never does, because both halves would be the same rank. Checked against
    # the engine across twelve hands, that and the Flush Five that followed
    # from it were the only two places the tables disagreed.
    #
    # The top hand is still chosen from the at-least groups below, which is
    # what the game does too: it reads _5 for a Five of a Kind whether or not
    # _3 also matched.
    sizes = Counter(counts.values())
    n_five, n_four, n_trips = sizes[5], sizes[4], sizes[3]
    n_pairs = sizes[2]

    held = {HandType.HIGH_CARD}
    if n_five:
        held.add(HandType.FIVE_OF_A_KIND)
        if flush:
            held.add(HandType.FLUSH_FIVE)
    if n_trips and n_pairs:
        held.add(HandType.FULL_HOUSE)
        if flush:
            held.add(HandType.FLUSH_HOUSE)
    if n_four:
        held.add(HandType.FOUR_OF_A_KIND)
    if flush:
        held.add(HandType.FLUSH)
    if straight:
        held.add(HandType.STRAIGHT)
    if flush and straight:
        held.add(HandType.STRAIGHT_FLUSH)
    if n_trips:
        held.add(HandType.THREE_OF_A_KIND)
    # Two Pair takes two pairs, or a set and a pair -- the one place a Full
    # House reaches down. A Four of a Kind never gets here.
    if n_pairs == 2 or (n_trips == 1 and n_pairs == 1):
        held.add(HandType.TWO_PAIR)
    if n_pairs:
        held.add(HandType.PAIR)
    # The cascade, in the game's own order and stopping where it stops.
    if HandType.FIVE_OF_A_KIND in held:
        held.add(HandType.FOUR_OF_A_KIND)
    if HandType.FOUR_OF_A_KIND in held:
        held.add(HandType.THREE_OF_A_KIND)
    if HandType.THREE_OF_A_KIND in held:
        held.add(HandType.PAIR)

    def result(hand: HandType, scoring: list[Card]) -> HandResult:
        # Stone cards always score, and scoring keeps the played order.
        # Splash widens the set to everything played without changing which
        # hand it is: a High Card with Splash still scores as a High Card, but
        # all five cards contribute their chips.
        if splash:
            return HandResult(hand, tuple(cards), frozenset(held))
        chosen = {c.uid for c in scoring} | {c.uid for c in stones}
        return HandResult(hand, tuple(c for c in cards if c.uid in chosen),
                          frozenset(held))

    if five and flush:
        return result(HandType.FLUSH_FIVE, five)
    if full_house and flush:
        return result(HandType.FLUSH_HOUSE, full_house)
    if five:
        return result(HandType.FIVE_OF_A_KIND, five)
    if straight and flush:
        # Every card in either part scores, not only the overlap. With Four
        # Fingers a four-card flush can sit inside a five-card straight, and
        # the game scores all five: A 3 5 7 9 with the 5 off suit is a
        # Straight Flush worth (100 + 35) x 8, not (100 + 30).
        #
        # Nor does the game ask the two parts to overlap at all
        # (misc_functions.lua:428): `if next(parts._flush) and
        # next(parts._straight)`. 2S 3S 4S 5H 9S is a four-card straight and a
        # four-card flush, and so a Straight Flush. Requiring four shared
        # cards made it a Flush here -- while `held` above already said it
        # contained a Straight Flush.
        union = flush + [c for c in straight if c not in flush]
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
