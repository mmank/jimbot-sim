"""Joker definitions.

Jokers are data, not engine code: each one is a `JokerSpec` with optional hooks
that the scoring pipeline calls. Adding a joker means adding a `register(...)`
call here -- the engine never needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Callable

from .cards import Card, Edition, Enhancement, Rank, Suit
from .effects import ScoreContext
from .hands import HandType

if TYPE_CHECKING:  # pragma: no cover
    from .game import GameState


class Rarity(IntEnum):
    COMMON = 1
    UNCOMMON = 2
    RARE = 3
    LEGENDARY = 4


BASE_COST = {Rarity.COMMON: 4, Rarity.UNCOMMON: 6, Rarity.RARE: 8, Rarity.LEGENDARY: 20}

ScoredHook = Callable[["JokerInstance", Card, ScoreContext], None]
HeldHook = Callable[["JokerInstance", Card, ScoreContext], None]
IndepHook = Callable[["JokerInstance", ScoreContext], None]
UpdateHook = Callable[["JokerInstance", ScoreContext], None]
RoundHook = Callable[["JokerInstance", "GameState"], None]
DiscardHook = Callable[["JokerInstance", list[Card], "GameState"], None]
RetriggerHook = Callable[["JokerInstance", Card, ScoreContext], int]


@dataclass(frozen=True)
class JokerSpec:
    name: str
    rarity: Rarity
    text: str
    cost: int = 0
    init_counter: float = 0.0
    update: UpdateHook | None = None
    # When `update` runs relative to the hand it is part of. The game is not
    # consistent about this and the difference is visible on the very first
    # hand: Ice Cream, Runner and Square Joker grow under context.after, so
    # they score their old value, while Green Joker increments while the last
    # played card is scoring and so pays its new one immediately.
    update_before_scoring: bool = False   # most scaling jokers want True
    scored: ScoredHook | None = None
    held: HeldHook | None = None
    independent: IndepHook | None = None
    round_end: RoundHook | None = None
    discarded: DiscardHook | None = None
    retrigger_scored: RetriggerHook | None = None
    retrigger_held: RetriggerHook | None = None
    copier: str | None = None  # "right" (Blueprint) or "leftmost" (Brainstorm)
    # The shop will not offer these unless the run already has a card with
    # that enhancement -- no Lucky Cat without a lucky card. Taken from the
    # game's own enhancement_gate field, and needed once shops are generated:
    # offering a gated joker to a deck that cannot use it is a distribution
    # error the policy would learn from.
    enhancement_gate: str = ""
    hand_size: int = 0
    extra_hands: int = 0
    extra_discards: int = 0


@dataclass
class JokerInstance:
    spec: JokerSpec
    edition: Edition = Edition.NONE
    counter: float = 0.0
    eternal: bool = False
    # Jokers that count hands measure from when they were acquired, not from
    # the start of the run -- the game stores this as hands_played_at_create.
    hands_at_create: int = 0

    def __post_init__(self) -> None:
        if self.counter == 0.0:
            self.counter = self.spec.init_counter

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def sell_value(self) -> int:
        return max(1, self.spec.cost // 2)

    def __repr__(self) -> str:
        tag = "" if self.edition is Edition.NONE else f"[{self.edition.value}]"
        num = "" if self.counter == self.spec.init_counter else f"({self.counter:g})"
        return f"{self.spec.name}{tag}{num}"


REGISTRY: dict[str, JokerSpec] = {}


def register(name: str, rarity: Rarity, text: str, **kwargs) -> JokerSpec:
    spec = JokerSpec(name=name, rarity=rarity, text=text,
                     cost=kwargs.pop("cost", BASE_COST[rarity]), **kwargs)
    REGISTRY[name] = spec
    return spec


def make(name: str, **kwargs) -> JokerInstance:
    return JokerInstance(REGISTRY[name], **kwargs)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _suit_scorer(suit: Suit, amount: int) -> ScoredHook:
    def hook(j: JokerInstance, card: Card, ctx: ScoreContext) -> None:
        if card.counts_as_suit(suit):
            ctx.add_mult(amount, j.name)
    return hook


def _hand_mult(hands: set[HandType], amount: int) -> IndepHook:
    def hook(j: JokerInstance, ctx: ScoreContext) -> None:
        if ctx.hand in hands:
            ctx.add_mult(amount, j.name)
    return hook


def _hand_chips(hands: set[HandType], amount: int) -> IndepHook:
    def hook(j: JokerInstance, ctx: ScoreContext) -> None:
        if ctx.hand in hands:
            ctx.add_chips(amount, j.name)
    return hook


def _hand_xmult(hands: set[HandType], factor: float) -> IndepHook:
    def hook(j: JokerInstance, ctx: ScoreContext) -> None:
        if ctx.hand in hands:
            ctx.times_mult(factor, j.name)
    return hook


def _rank_scorer(ranks: set[Rank], chips: int = 0, mult: int = 0) -> ScoredHook:
    def hook(j: JokerInstance, card: Card, ctx: ScoreContext) -> None:
        if card.is_stone or card.rank not in ranks:
            return
        ctx.add_chips(chips, j.name)
        ctx.add_mult(mult, j.name)
    return hook


def _face_scorer(chips: int = 0, mult: int = 0) -> ScoredHook:
    def hook(j: JokerInstance, card: Card, ctx: ScoreContext) -> None:
        if card.is_stone or not card.rank.is_face:
            return
        ctx.add_chips(chips, j.name)
        ctx.add_mult(mult, j.name)
    return hook


CONTAINS_PAIR = {HandType.PAIR, HandType.TWO_PAIR, HandType.THREE_OF_A_KIND,
                 HandType.FULL_HOUSE, HandType.FOUR_OF_A_KIND, HandType.FIVE_OF_A_KIND,
                 HandType.FLUSH_HOUSE, HandType.FLUSH_FIVE}
CONTAINS_TRIPS = {HandType.THREE_OF_A_KIND, HandType.FULL_HOUSE, HandType.FOUR_OF_A_KIND,
                  HandType.FIVE_OF_A_KIND, HandType.FLUSH_HOUSE, HandType.FLUSH_FIVE}
CONTAINS_TWO_PAIR = {HandType.TWO_PAIR, HandType.FULL_HOUSE, HandType.FLUSH_HOUSE}
CONTAINS_QUADS = {HandType.FOUR_OF_A_KIND, HandType.FIVE_OF_A_KIND, HandType.FLUSH_FIVE}
CONTAINS_STRAIGHT = {HandType.STRAIGHT, HandType.STRAIGHT_FLUSH}
CONTAINS_FLUSH = {HandType.FLUSH, HandType.STRAIGHT_FLUSH, HandType.FLUSH_HOUSE,
                  HandType.FLUSH_FIVE}

_EVEN = {Rank.TEN, Rank.EIGHT, Rank.SIX, Rank.FOUR, Rank.TWO}
_ODD = {Rank.ACE, Rank.NINE, Rank.SEVEN, Rank.FIVE, Rank.THREE}
_FIB = {Rank.ACE, Rank.TWO, Rank.THREE, Rank.FIVE, Rank.EIGHT}

# --------------------------------------------------------------------------
# common
# --------------------------------------------------------------------------

register("Joker", Rarity.COMMON, "+4 Mult", cost=2,
         independent=lambda j, ctx: ctx.add_mult(4, j.name))

for _name, _suit in [("Greedy Joker", Suit.DIAMONDS), ("Lusty Joker", Suit.HEARTS),
                     ("Wrathful Joker", Suit.SPADES), ("Gluttonous Joker", Suit.CLUBS)]:
    register(_name, Rarity.COMMON, f"+3 Mult per scored {_suit.name.title()}", cost=5,
             scored=_suit_scorer(_suit, 3))

register("Jolly Joker", Rarity.COMMON, "+8 Mult if hand contains a Pair", cost=3,
         independent=_hand_mult(CONTAINS_PAIR, 8))
register("Zany Joker", Rarity.COMMON, "+12 Mult if hand contains Three of a Kind", cost=4,
         independent=_hand_mult(CONTAINS_TRIPS, 12))
register("Mad Joker", Rarity.COMMON, "+10 Mult if hand contains Two Pair", cost=4,
         independent=_hand_mult(CONTAINS_TWO_PAIR, 10))
register("Crazy Joker", Rarity.COMMON, "+12 Mult if hand contains a Straight", cost=4,
         independent=_hand_mult(CONTAINS_STRAIGHT, 12))
register("Droll Joker", Rarity.COMMON, "+10 Mult if hand contains a Flush", cost=4,
         independent=_hand_mult(CONTAINS_FLUSH, 10))
register("Sly Joker", Rarity.COMMON, "+50 Chips if hand contains a Pair", cost=3,
         independent=_hand_chips(CONTAINS_PAIR, 50))
register("Wily Joker", Rarity.COMMON, "+100 Chips if hand contains Three of a Kind", cost=4,
         independent=_hand_chips(CONTAINS_TRIPS, 100))
register("Clever Joker", Rarity.COMMON, "+80 Chips if hand contains Two Pair", cost=4,
         independent=_hand_chips(CONTAINS_TWO_PAIR, 80))
register("Devious Joker", Rarity.COMMON, "+100 Chips if hand contains a Straight", cost=4,
         independent=_hand_chips(CONTAINS_STRAIGHT, 100))
register("Crafty Joker", Rarity.COMMON, "+80 Chips if hand contains a Flush", cost=4,
         independent=_hand_chips(CONTAINS_FLUSH, 80))

register("Half Joker", Rarity.COMMON, "+20 Mult if 3 or fewer cards played", cost=5,
         independent=lambda j, ctx: ctx.add_mult(20, j.name) if len(ctx.played) <= 3 else None)
register("Banner", Rarity.COMMON, "+30 Chips per remaining discard", cost=5,
         independent=lambda j, ctx: ctx.add_chips(30 * ctx.game.discards_left, j.name))
register("Mystic Summit", Rarity.COMMON, "+15 Mult with 0 discards remaining", cost=5,
         independent=lambda j, ctx: ctx.add_mult(15, j.name)
         if ctx.game.discards_left == 0 else None)
register("Misprint", Rarity.COMMON, "+0 to +23 Mult", cost=4,
         independent=lambda j, ctx: ctx.add_mult(
             ctx.game.rng.randint("misprint", 0, 23), j.name))

register("Even Steven", Rarity.COMMON, "+4 Mult per scored even card", cost=4,
         scored=_rank_scorer(_EVEN, mult=4))
register("Odd Todd", Rarity.COMMON, "+31 Chips per scored odd card", cost=4,
         scored=_rank_scorer(_ODD, chips=31))
register("Fibonacci", Rarity.UNCOMMON, "+8 Mult per scored A, 2, 3, 5 or 8", cost=8,
         scored=_rank_scorer(_FIB, mult=8))
register("Scholar", Rarity.COMMON, "Scored Aces give +20 Chips and +4 Mult", cost=4,
         scored=_rank_scorer({Rank.ACE}, chips=20, mult=4))
register("Walkie Talkie", Rarity.COMMON, "Scored 10s and 4s give +10 Chips and +4 Mult",
         cost=4, scored=_rank_scorer({Rank.TEN, Rank.FOUR}, chips=10, mult=4))
register("Scary Face", Rarity.COMMON, "+30 Chips per scored face card", cost=4,
         scored=_face_scorer(chips=30))
register("Smiley Face", Rarity.COMMON, "+5 Mult per scored face card", cost=4,
         scored=_face_scorer(mult=5))

register("Abstract Joker", Rarity.COMMON, "+3 Mult per Joker held", cost=4,
         independent=lambda j, ctx: ctx.add_mult(3 * len(ctx.game.jokers), j.name))
register("Blue Joker", Rarity.COMMON, "+2 Chips per card left in deck", cost=5,
         independent=lambda j, ctx: ctx.add_chips(2 * len(ctx.game.draw_pile), j.name))
register("Bull", Rarity.UNCOMMON, "+2 Chips per dollar held", cost=6,
         independent=lambda j, ctx: ctx.add_chips(2 * max(0, ctx.game.money), j.name))
register("Bootstraps", Rarity.UNCOMMON, "+2 Mult per $5 held", cost=7,
         independent=lambda j, ctx: ctx.add_mult(2 * (max(0, ctx.game.money) // 5), j.name))


def _ride_update(j: JokerInstance, ctx: ScoreContext) -> None:
    # A debuffed face card does not score, so it does not break the streak --
    # against The Club a debuffed King leaves the counter climbing. Missing
    # this only shows up on a boss blind, which is why it survived until the
    # scenario matrix reached one.
    if any(c.rank.is_face and not c.is_stone and not c.debuffed
           for c in ctx.scoring):
        j.counter = 0.0
    else:
        j.counter += 1


def _bump(j: JokerInstance, amount: float, floor: float | None = None) -> None:
    value = j.counter + amount
    j.counter = value if floor is None else max(floor, value)


register("Ride the Bus", Rarity.COMMON,
         "+1 Mult per consecutive hand without a scored face card", cost=6,
         update=_ride_update, update_before_scoring=True,
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name))

register("Green Joker", Rarity.COMMON, "+1 Mult per hand played, -1 per discard", cost=4,
         update=lambda j, ctx: _bump(j, 1), update_before_scoring=True,
         discarded=lambda j, cards, g: _bump(j, -1, floor=0.0),
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name))

register("Runner", Rarity.COMMON, "+15 Chips, gains +15 Chips per Straight played",
         cost=5, init_counter=0.0,   # the game starts extra.chips at 0
         update=lambda j, ctx: _bump(j, 15) if ctx.hand in CONTAINS_STRAIGHT else None,
         update_before_scoring=True,
         independent=lambda j, ctx: ctx.add_chips(j.counter, j.name))

register("Ice Cream", Rarity.COMMON, "+100 Chips, -5 Chips per hand played",
         cost=5, init_counter=100.0,
         independent=lambda j, ctx: ctx.add_chips(j.counter, j.name),
         update=lambda j, ctx: _bump(j, -5, floor=0.0))

register("Square Joker", Rarity.COMMON, "+4 Chips per hand played with exactly 4 cards",
         cost=4,
         update=lambda j, ctx: _bump(j, 4) if len(ctx.played) == 4 else None,
         update_before_scoring=True,
         independent=lambda j, ctx: ctx.add_chips(j.counter, j.name))

register("Supernova", Rarity.COMMON, "+Mult equal to times this hand has been played",
         cost=5,
         independent=lambda j, ctx: ctx.add_mult(
             ctx.game.hand_levels.plays[ctx.hand], j.name))

register("Popcorn", Rarity.COMMON, "+20 Mult, -4 Mult per round played",
         cost=5, init_counter=20.0,
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name),
         round_end=lambda j, g: _bump(j, -4, floor=0.0))

register("Swashbuckler", Rarity.COMMON, "+Mult equal to sell value of other Jokers",
         cost=4,
         independent=lambda j, ctx: ctx.add_mult(
             sum(o.sell_value for o in ctx.game.jokers if o is not j), j.name))

register("Golden Joker", Rarity.COMMON, "Earn $4 at end of round", cost=6,
         round_end=lambda j, g: g.add_money(4, "Golden Joker"))

register("Faceless Joker", Rarity.COMMON, "Earn $5 if 3+ face cards discarded", cost=4,
         discarded=lambda j, cards, g: g.add_money(5, "Faceless Joker")
         if sum(1 for c in cards if c.rank.is_face) >= 3 else None)


def _gros_michel_end(j: JokerInstance, g: "GameState") -> None:
    if g.rng.chance("gros_michel", 1, 6):
        g.destroy_joker(j, "Gros Michel went extinct")


register("Gros Michel", Rarity.COMMON, "+15 Mult, 1 in 6 chance to be destroyed", cost=5,
         independent=lambda j, ctx: ctx.add_mult(15, j.name),
         round_end=_gros_michel_end)

register("Cavendish", Rarity.COMMON, "X3 Mult", cost=4,
         independent=lambda j, ctx: ctx.times_mult(3.0, j.name))

# --------------------------------------------------------------------------
# uncommon / rare scaling and conditional jokers
# --------------------------------------------------------------------------

register("The Duo", Rarity.RARE, "X2 Mult if hand contains a Pair", cost=8,
         independent=_hand_xmult(CONTAINS_PAIR, 2.0))
register("The Trio", Rarity.RARE, "X3 Mult if hand contains Three of a Kind", cost=8,
         independent=_hand_xmult(CONTAINS_TRIPS, 3.0))
register("The Family", Rarity.RARE, "X4 Mult if hand contains Four of a Kind", cost=8,
         independent=_hand_xmult(CONTAINS_QUADS, 4.0))
register("The Order", Rarity.RARE, "X3 Mult if hand contains a Straight", cost=8,
         independent=_hand_xmult(CONTAINS_STRAIGHT, 3.0))
register("The Tribe", Rarity.RARE, "X2 Mult if hand contains a Flush", cost=8,
         independent=_hand_xmult(CONTAINS_FLUSH, 2.0))

register("Baron", Rarity.RARE, "Kings held in hand give X1.5 Mult", cost=8,
         held=lambda j, c, ctx: ctx.times_mult(1.5, j.name)
         if c.rank is Rank.KING and not c.is_stone else None)

register("Blackboard", Rarity.UNCOMMON,
         "X3 Mult if all cards held in hand are Spades or Clubs", cost=6,
         independent=lambda j, ctx: ctx.times_mult(3.0, j.name)
         if all(c.counts_as_suit(Suit.SPADES) or c.counts_as_suit(Suit.CLUBS)
                for c in ctx.held) else None)

register("Card Sharp", Rarity.UNCOMMON,
         "X3 Mult if this hand type was already played this round", cost=6,
         independent=lambda j, ctx: ctx.times_mult(3.0, j.name)
         if ctx.hand in ctx.game.hands_played_this_round else None)

register("Flower Pot", Rarity.UNCOMMON, "X3 Mult if scoring hand has all 4 suits", cost=6,
         independent=lambda j, ctx: ctx.times_mult(3.0, j.name)
         if all(any(c.counts_as_suit(s) for c in ctx.scoring) for s in Suit) else None)

register("Stuntman", Rarity.RARE, "+250 Chips, -2 hand size", cost=7, hand_size=-2,
         independent=lambda j, ctx: ctx.add_chips(250, j.name))

register("Steel Joker", Rarity.UNCOMMON, "X0.2 Mult per Steel card in your deck", enhancement_gate="m_steel", cost=7,
         independent=lambda j, ctx: ctx.times_mult(
             1.0 + 0.2 * sum(1 for c in ctx.game.full_deck
                             if c.enhancement is Enhancement.STEEL), j.name))


def _vampire(j: JokerInstance, ctx: ScoreContext) -> None:
    gained = 0
    for c in ctx.scoring:
        if c.enhancement not in (Enhancement.NONE, Enhancement.STONE):
            c.enhancement = Enhancement.NONE
            gained += 1
    j.counter += 0.1 * gained


register("Vampire", Rarity.UNCOMMON,
         "X0.1 Mult per scored enhanced card, removing the enhancement", cost=7,
         init_counter=1.0, update=_vampire,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))

register("Ramen", Rarity.UNCOMMON, "X2 Mult, -X0.01 per discarded card",
         cost=6, init_counter=2.0,
         discarded=lambda j, cards, g: _bump(j, -0.01 * len(cards), floor=1.0),
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))

def _loyalty_remaining(j: "JokerInstance", game: "GameState") -> int:
    """The game's own formula, not a modulo-6 counter.

    loyalty_remaining = (every-1 - hands since created) % (every+1), with the
    X4 firing when that equals `every`. A plain counter starting at zero fires
    on the *first* hand instead of the sixth, which is what the simulator did.
    """
    every = 5
    return (every - 1 - (game.hands_played - j.hands_at_create)) % (every + 1)


register("Loyalty Card", Rarity.UNCOMMON, "X4 Mult every 6th hand played",
         cost=5,
         independent=lambda j, ctx: ctx.times_mult(4.0, j.name)
         if _loyalty_remaining(j, ctx.game) == 5 else None)

register("Hologram", Rarity.UNCOMMON, "X0.25 Mult per playing card added to your deck",
         cost=7, init_counter=1.0,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))

register("Baseball Card", Rarity.RARE, "Uncommon Jokers each give X1.5 Mult", cost=8,
         independent=lambda j, ctx: ctx.times_mult(
             1.5 ** sum(1 for o in ctx.game.jokers
                        if o.spec.rarity is Rarity.UNCOMMON), j.name))

# --------------------------------------------------------------------------
_HACK_RANKS = {Rank.TWO, Rank.THREE, Rank.FOUR, Rank.FIVE}

# retrigger and copy jokers
# --------------------------------------------------------------------------

register("Hack", Rarity.UNCOMMON, "Retrigger each played 2, 3, 4 or 5", cost=6,
         retrigger_scored=lambda j, c, ctx: 1
         if not c.is_stone and c.rank in _HACK_RANKS else 0)
register("Sock and Buskin", Rarity.UNCOMMON, "Retrigger all scored face cards", cost=6,
         retrigger_scored=lambda j, c, ctx: 1 if c.rank.is_face and not c.is_stone else 0)
register("Hanging Chad", Rarity.COMMON, "Retrigger the first scored card 2 extra times",
         cost=4,
         retrigger_scored=lambda j, c, ctx: 2 if ctx.scoring and c is ctx.scoring[0] else 0)
register("Dusk", Rarity.UNCOMMON,
         "Retrigger all scored cards on the final hand of the round", cost=5,
         retrigger_scored=lambda j, c, ctx: 1 if ctx.game.hands_left == 0 else 0)
register("Mime", Rarity.UNCOMMON, "Retrigger all card abilities held in hand", cost=5,
         retrigger_held=lambda j, c, ctx: 1)

# Hand-shape jokers. These score nothing themselves; they change what the
# played cards *are*, which the evaluator asks the game about. Together they
# make A 3 5 7 9 of mostly one suit a straight flush -- four cards is enough
# for the flush, and one-rank gaps are enough for the straight.
register("Four Fingers", Rarity.UNCOMMON,
         "Flushes and Straights need only 4 cards", cost=7)
register("Shortcut", Rarity.UNCOMMON,
         "Straights can be made with gaps of 1 rank", cost=7)

register("Blueprint", Rarity.RARE, "Copies the ability of the Joker to the right", cost=10,
         copier="right")
register("Brainstorm", Rarity.RARE, "Copies the ability of the leftmost Joker", cost=10,
         copier="leftmost")

# --------------------------------------------------------------------------
# legendary
# --------------------------------------------------------------------------

register("Triboulet", Rarity.LEGENDARY, "Played Kings and Queens each give X2 Mult",
         scored=lambda j, c, ctx: ctx.times_mult(2.0, j.name)
         if c.rank in (Rank.KING, Rank.QUEEN) and not c.is_stone else None)

register("Canio", Rarity.LEGENDARY, "X1 Mult, gains X1 Mult per face card destroyed",
         init_counter=1.0,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))

register("Yorick", Rarity.LEGENDARY, "X1 Mult, gains X1 Mult per 23 cards discarded",
         init_counter=1.0,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))

register("Chicot", Rarity.LEGENDARY, "Disables the effect of every Boss Blind")

register("Perkeo", Rarity.LEGENDARY,
         "Creates a Negative copy of a random consumable at the end of the shop")


def by_rarity(rarity: Rarity) -> list[JokerSpec]:
    return [s for s in REGISTRY.values() if s.rarity is rarity]


# --------------------------------------------------------------------------
# suit and rank scorers, second batch
# --------------------------------------------------------------------------

def _suit_chips(suit: Suit, amount: int) -> ScoredHook:
    def hook(j: JokerInstance, card: Card, ctx: ScoreContext) -> None:
        if card.counts_as_suit(suit):
            ctx.add_chips(amount, j.name)
    return hook


def _suit_money(suit: Suit, amount: int) -> ScoredHook:
    def hook(j: JokerInstance, card: Card, ctx: ScoreContext) -> None:
        if card.counts_as_suit(suit):
            ctx.money_gained += amount
    return hook


def _first_face(ctx: ScoreContext) -> Card | None:
    """The first scoring face card, which Photograph multiplies."""
    for card in ctx.scoring:
        if card.rank.is_face and not card.is_stone and not card.debuffed:
            return card
    return None


register("Arrowhead", Rarity.UNCOMMON, "Played Spades give +50 Chips", cost=7,
         scored=_suit_chips(Suit.SPADES, 50))
register("Onyx Agate", Rarity.UNCOMMON, "Played Clubs give +7 Mult", cost=7,
         scored=_suit_scorer(Suit.CLUBS, 7))
register("Rough Gem", Rarity.UNCOMMON, "Played Diamonds earn $1", cost=7,
         scored=_suit_money(Suit.DIAMONDS, 1))
register("Golden Ticket", Rarity.COMMON, "Played Gold cards earn $4", enhancement_gate="m_gold", cost=5,
         scored=lambda j, c, ctx: ctx.__setattr__(
             "money_gained", ctx.money_gained + 4)
         if c.enhancement is Enhancement.GOLD else None)
register("Photograph", Rarity.COMMON,
         "First played face card gives X2 Mult", cost=5,
         scored=lambda j, c, ctx: ctx.times_mult(2.0, j.name)
         if c is _first_face(ctx) else None)

register("Shoot the Moon", Rarity.COMMON,
         "Each Queen held in hand gives +13 Mult", cost=5,
         held=lambda j, c, ctx: ctx.add_mult(13, j.name)
         if c.rank is Rank.QUEEN and not c.debuffed else None)


def _raised_fist(j: JokerInstance, ctx: ScoreContext) -> None:
    """Double the rank of the lowest card held in hand.

    "Rank" is the card's chip value, so an Ace is 11 and counts as the highest
    rather than the lowest. Stone cards have no rank and are skipped.
    """
    ranked = [c for c in ctx.held if not c.is_stone and not c.debuffed]
    if ranked:
        lowest = min(ranked, key=lambda c: c.rank.value)
        ctx.add_mult(2 * lowest.rank.chips, j.name)


register("Raised Fist", Rarity.COMMON,
         "Adds double the rank of the lowest card held in hand to Mult",
         cost=5, independent=_raised_fist)

register("Acrobat", Rarity.UNCOMMON, "X3 Mult on the final hand of the round",
         cost=6,
         independent=lambda j, ctx: ctx.times_mult(3.0, j.name)
         if ctx.game.hands_left == 0 else None)


def _seeing_double(j: JokerInstance, ctx: ScoreContext) -> None:
    """X2 when a scoring Club sits alongside a scoring card of another suit.

    A single Wild card cannot satisfy both halves: the game wants two cards.
    """
    live = [c for c in ctx.scoring if not c.debuffed and not c.is_stone]
    for club in (c for c in live if c.counts_as_suit(Suit.CLUBS)):
        for other in live:
            if other is club:
                continue
            if any(other.counts_as_suit(s) for s in Suit if s is not Suit.CLUBS):
                ctx.times_mult(2.0, j.name)
                return


register("Seeing Double", Rarity.UNCOMMON,
         "X2 Mult if the hand scores a Club and a card of any other suit",
         cost=6, independent=_seeing_double)


# -- jokers that scale on the deck ------------------------------------------

register("Erosion", Rarity.UNCOMMON,
         "+4 Mult for each card below 52 in your full deck", cost=6,
         independent=lambda j, ctx: ctx.add_mult(
             4 * max(0, 52 - len(ctx.game.full_deck)), j.name))
register("Stone Joker", Rarity.UNCOMMON,
         "+25 Chips for each Stone card in your full deck", enhancement_gate="m_stone", cost=6,
         independent=lambda j, ctx: ctx.add_chips(
             25 * sum(1 for c in ctx.game.full_deck if c.is_stone), j.name))
register("Driver's License", Rarity.RARE,
         "X3 Mult if you have at least 16 Enhanced cards", cost=7,
         independent=lambda j, ctx: ctx.times_mult(3.0, j.name)
         if sum(1 for c in ctx.game.full_deck
                if c.enhancement is not Enhancement.NONE) >= 16 else None)
register("Joker Stencil", Rarity.UNCOMMON,
         "X1 Mult for each empty Joker slot, itself included", cost=8,
         independent=lambda j, ctx: ctx.times_mult(
             float(ctx.game.joker_slots - len(ctx.game.jokers) + 1), j.name))


# -- jokers that scale on what the run has done -----------------------------

register("Fortune Teller", Rarity.COMMON,
         "+1 Mult per Tarot card used this run", cost=6,
         independent=lambda j, ctx: ctx.add_mult(ctx.game.tarots_used, j.name))
# These keep their growth on the joker, not on the run: the game stores it in
# ability.x_mult, so two Constellations scale independently and selling one
# does not reset the other. Reading a run-wide counter instead would look
# right until a second copy appeared.
register("Constellation", Rarity.UNCOMMON,
         "Gains X0.1 Mult per Planet card used", cost=6, init_counter=1.0,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))
register("Flash Card", Rarity.UNCOMMON, "Gains +2 Mult per shop reroll",
         cost=5,
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name))
register("Throwback", Rarity.UNCOMMON, "X0.25 Mult per Blind skipped this run",
         cost=6,
         # Card:update recomputes this as 1 + skips * 0.25 every frame, so the
         # joker keeps no growth of its own however much the tooltip looks
         # like it does.
         independent=lambda j, ctx: ctx.times_mult(
             1.0 + 0.25 * ctx.game.blinds_skipped, j.name))
register("Campfire", Rarity.RARE,
         "Gains X0.25 Mult per card sold, resets on a defeated Boss Blind",
         cost=9, init_counter=1.0,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))
register("Glass Joker", Rarity.UNCOMMON,
         "Gains X0.75 Mult per Glass card destroyed", enhancement_gate="m_glass", cost=6, init_counter=1.0,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))
register("Lucky Cat", Rarity.UNCOMMON,
         "Gains X0.25 Mult each time a Lucky card triggers", enhancement_gate="m_lucky", cost=6,
         init_counter=1.0,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))


# -- scaling on the hand just played ----------------------------------------

register("Wee Joker", Rarity.RARE, "Gains +8 Chips when each played 2 scores",
         cost=8,
         scored=lambda j, c, ctx: _bump(j, 8) if c.rank is Rank.TWO
         and not c.is_stone else None,
         independent=lambda j, ctx: ctx.add_chips(j.counter, j.name))
register("Spare Trousers", Rarity.UNCOMMON,
         "Gains +2 Mult if the played hand contains a Two Pair", cost=6,
         update=lambda j, ctx: _bump(j, 2)
         if ctx.hand in CONTAINS_TWO_PAIR else None,
         update_before_scoring=True,
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name))
register("Hiker", Rarity.UNCOMMON,
         "Every played card permanently gains +5 Chips when scored", cost=5,
         scored=lambda j, c, ctx: setattr(c, "extra_chips", c.extra_chips + 5))


# -- scaling jokers whose growth comes from outside the played hand ---------

register("Ceremonial Dagger", Rarity.UNCOMMON,
         "When Blind is selected, destroy the Joker to the right and "
         "permanently add double its sell value to Mult", cost=6,
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name))
register("Castle", Rarity.UNCOMMON,
         "Gains +3 Chips per discarded card of a suit that changes each round",
         cost=6,
         independent=lambda j, ctx: ctx.add_chips(j.counter, j.name))
def _obelisk_update(j: JokerInstance, ctx: ScoreContext) -> None:
    """Grow unless the hand just played is the run's most played.

    The game resets only when no *other* hand has been played at least as
    often, so a tie keeps it growing. Reading it as a stored counter -- which
    it is, in ability.x_mult -- misses that it is rewritten before every hand.
    """
    plays = ctx.game.hand_levels.plays
    mine = plays[ctx.hand]
    if any(hand is not ctx.hand and count >= mine
           for hand, count in plays.items()):
        j.counter += 0.2
    elif j.counter > 1.0:
        j.counter = 1.0


register("Obelisk", Rarity.RARE,
         "Gains X0.2 Mult per consecutive hand played without playing your "
         "most played poker hand", cost=8, init_counter=1.0,
         update=_obelisk_update, update_before_scoring=True,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))
register("Hit the Road", Rarity.RARE,
         "Gains X0.5 Mult for every Jack discarded this round", cost=8,
         init_counter=1.0,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))
