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


def next_sort_id() -> int:
    """The game's G.sort_id: one counter for everything it makes.

    Card:init does

        G.sort_id = (G.sort_id or 0) + 1
        self.sort_id = G.sort_id

    and pseudorandom_element sorts a table by sort_id before indexing into
    it, so a random draw over jokers picks by age and never by where they sit
    in the row. Shared with the cards because the game shares it.
    """
    return next(_ids)


# From the game's own card bases. Suits order Diamonds < Clubs < Hearts <
# Spades, and face_nominal separates the cards that all count as ten chips.
SUIT_NOMINAL = {Suit.DIAMONDS: 0.01, Suit.CLUBS: 0.02,
                Suit.HEARTS: 0.03, Suit.SPADES: 0.04}
# get_nominal adds `suit_nominal_original * 0.0001`, which is the suit the
# card was *built* as. Card:set_base carries it across a suit change, so a
# Club turned into a Spade by the Checkered Deck still remembers being a
# Club, and sorts behind a Spade that was always one. Two identical-looking
# Jacks of Spades are therefore not tied at all -- and the difference is
# larger than the unique_val term that comes after it, so it decides.
ORIGINAL_SUIT_WEIGHT = 0.0001
FACE_NOMINAL = {Rank.ACE: 0.4, Rank.KING: 0.3, Rank.QUEEN: 0.2, Rank.JACK: 0.1}


@dataclass(eq=False)
class Card:
    rank: Rank
    suit: Suit
    enhancement: Enhancement = Enhancement.NONE
    edition: Edition = Edition.NONE
    seal: Seal = Seal.NONE
    extra_chips: int = 0  # permanent bonus from Hiker etc.
    # Set only when something changes the card's suit; see original_suit.
    _original_suit: "Suit | None" = None
    # Whether this card has already been played this ante, which is what The
    # Pillar debuffs. It lives on the card rather than on the run because
    # changing the card's *enhancement* wipes it -- see set_enhancement.
    played_this_ante: bool = False
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

    @property
    def original_suit(self) -> Suit:
        """The suit this card was built as, which a conversion does not clear.

        base.suit_nominal_original in the game. It only ever differs on a
        deck or an effect that changes a card's suit, and it is invisible
        until two cards look identical -- then it is what separates them.
        """
        return self._original_suit if self._original_suit is not None else self.suit

    @property
    def sort_value(self) -> float:
        """The game's get_nominal, which is what orders a hand on screen.

        The order matters beyond looks: actions address cards by position, so
        a simulator holding the same eight cards in a different order plays
        different ones for the same choice. Ranks come first, then face cards
        separate within the tens (Ace .4, King .3, Queen .2, Jack .1), then
        the suit breaks what is left -- Diamonds lowest, Spades highest --
        and finally the suit the card was originally built as.
        """
        original = ORIGINAL_SUIT_WEIGHT * SUIT_NOMINAL[self.original_suit]
        if self.is_stone:
            # The game multiplies the suit term by -1000 for stone cards,
            # which sinks them below everything else.
            return (self.rank.chips - 1000 * SUIT_NOMINAL[self.suit]
                    - 1000 * original)
        return (self.rank.chips + SUIT_NOMINAL[self.suit] + original
                + FACE_NOMINAL.get(self.rank, 0.0))

    @property
    def suit_sort_value(self) -> float:
        """get_nominal('suit'), which is what the sort-by-suit button uses.

        The game multiplies the suit term by a thousand for this, so the suit
        dominates and the rank only breaks ties within it. Everything else is
        the same as the ordinary sort.
        """
        original = 1000 * ORIGINAL_SUIT_WEIGHT * SUIT_NOMINAL[self.original_suit]
        if self.is_stone:
            return (-1000 * SUIT_NOMINAL[self.suit] - original
                    + self.rank.chips)
        return (1000 * SUIT_NOMINAL[self.suit] + original + self.rank.chips
                + FACE_NOMINAL.get(self.rank, 0.0))

    def set_suit(self, suit: Suit) -> None:
        """Change the suit, remembering what it was.

        Card:set_base keeps suit_nominal_original across the change, and that
        remembered suit is part of what orders the hand -- so a Club turned
        into a Spade is not interchangeable with a natural Spade even though
        nothing on the card says so.
        """
        if self._original_suit is None:
            self._original_suit = self.suit
        self.suit = suit

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


# The order the game builds a deck in, which is also its sort_id order. It is
# not a playing order at all: the game keys its cards "C_2", "C_A", "C_T" and
# so on, and lays them out in the alphabetical order of those keys. So suits
# run Clubs, Diamonds, Hearts, Spades -- and within a suit the ranks run
# 2..9, then Ace, Jack, King, Queen, Ten, because that is A, J, K, Q, T.
#
# This matters because the round's shuffle runs over this list. A deck built
# in any other order shuffles reproducibly into a different deck, and every
# hand of the run is then wrong from the same seed while every rule stays
# right -- the failure that is hardest to see.
DECK_SUIT_ORDER = (Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES)
DECK_RANK_ORDER = (Rank.TWO, Rank.THREE, Rank.FOUR, Rank.FIVE, Rank.SIX,
                   Rank.SEVEN, Rank.EIGHT, Rank.NINE, Rank.ACE, Rank.JACK,
                   Rank.KING, Rank.QUEEN, Rank.TEN)


def standard_deck(no_faces: bool = False, erratic=None) -> list[Card]:
    """The 52-card deck, in the game's own build order.

    Two decks change the build rather than what happens afterwards, and both
    have to be done here because a card's place in this list is the id the
    game gives it.

    The Abandoned Deck drops the Kings, Queens and Jacks before the protos are
    sorted, so it is a forty card deck and every id after the first Jack
    shifts. The Erratic Deck replaces each card's face with a draw from
    G.P_CARDS under the pool name "erratic" -- fifty-two draws, keeping
    duplicates -- so pass a generator to get one.
    """
    faces = {Rank.JACK, Rank.QUEEN, Rank.KING}
    if erratic is not None:
        fronts = ["%s_%s" % (s, r) for s in ("C", "D", "H", "S")
                  for r in ("2", "3", "4", "5", "6", "7", "8", "9",
                            "A", "J", "K", "Q", "T")]
        by_rank = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR,
                   "5": Rank.FIVE, "6": Rank.SIX, "7": Rank.SEVEN,
                   "8": Rank.EIGHT, "9": Rank.NINE, "T": Rank.TEN,
                   "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
                   "A": Rank.ACE}
        by_suit = {"C": Suit.CLUBS, "D": Suit.DIAMONDS, "H": Suit.HEARTS,
                   "S": Suit.SPADES}
        # Draw fifty-two, then put them in order.
        #
        # The game builds card_protos and then sorts them by the card's own
        # letters -- `table.sort(card_protos, s..r..e..d..g)` -- which is what
        # gives a deck a deterministic build order at all, since the protos
        # come out of `pairs(P_CARDS)` in no defined order. For an ordinary
        # deck the sort is invisible because the result is the order it was
        # already in. For an Erratic Deck it is the whole difference: fifty-two
        # random draws, then sorted, so C2 C4 C4 C6 rather than the order they
        # were rolled in.
        #
        # The draws themselves were right all along -- the two decks held the
        # same fifty-two cards -- but a card's place in this list is the id it
        # gets, and the ids are what The Hook and every other positional effect
        # read.
        drawn = []
        for _ in range(52):
            drawn.append(erratic.random_element(fronts, "erratic"))
        order = {front: i for i, front in enumerate(sorted(
            fronts, key=lambda f: f.split("_")[0] + f.split("_")[1]))}
        drawn.sort(key=lambda front: order[front])
        out = []
        for front in drawn:
            suit, rank = front.split("_")
            out.append(Card(by_rank[rank], by_suit[suit]))
        return out
    return [Card(rank, suit)
            for suit in DECK_SUIT_ORDER for rank in DECK_RANK_ORDER
            if not (no_faces and rank in faces)]
