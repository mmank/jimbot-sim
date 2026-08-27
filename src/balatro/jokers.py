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
    scored: ScoredHook | None = None
    held: HeldHook | None = None
    independent: IndepHook | None = None
    round_end: RoundHook | None = None
    discarded: DiscardHook | None = None
    retrigger_scored: RetriggerHook | None = None
    retrigger_held: RetriggerHook | None = None
    copier: str | None = None  # "right" (Blueprint) or "leftmost" (Brainstorm)
    hand_size: int = 0
    extra_hands: int = 0
    extra_discards: int = 0


@dataclass
class JokerInstance:
    spec: JokerSpec
    edition: Edition = Edition.NONE
    counter: float = 0.0
    eternal: bool = False

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
    if any(c.rank.is_face and not c.is_stone for c in ctx.scoring):
        j.counter = 0.0
    else:
        j.counter += 1


def _bump(j: JokerInstance, amount: float, floor: float | None = None) -> None:
    value = j.counter + amount
    j.counter = value if floor is None else max(floor, value)


register("Ride the Bus", Rarity.COMMON,
         "+1 Mult per consecutive hand without a scored face card", cost=6,
         update=_ride_update,
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name))

register("Green Joker", Rarity.COMMON, "+1 Mult per hand played, -1 per discard", cost=4,
         update=lambda j, ctx: _bump(j, 1),
         discarded=lambda j, cards, g: _bump(j, -1, floor=0.0),
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name))

register("Runner", Rarity.COMMON, "+15 Chips, gains +15 Chips per Straight played",
         cost=5, init_counter=15.0,
         update=lambda j, ctx: _bump(j, 15) if ctx.hand in CONTAINS_STRAIGHT else None,
         independent=lambda j, ctx: ctx.add_chips(j.counter, j.name))

register("Ice Cream", Rarity.COMMON, "+100 Chips, -5 Chips per hand played",
         cost=5, init_counter=100.0,
         independent=lambda j, ctx: ctx.add_chips(j.counter, j.name),
         update=lambda j, ctx: _bump(j, -5, floor=0.0))

register("Square Joker", Rarity.COMMON, "+4 Chips per hand played with exactly 4 cards",
         cost=4,
         update=lambda j, ctx: _bump(j, 4) if len(ctx.played) == 4 else None,
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

register("Steel Joker", Rarity.UNCOMMON, "X0.2 Mult per Steel card in your deck", cost=7,
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

register("Loyalty Card", Rarity.UNCOMMON, "X4 Mult every 6th hand played",
         cost=5, update=lambda j, ctx: _bump(j, 1),
         independent=lambda j, ctx: ctx.times_mult(4.0, j.name) if j.counter % 6 == 0 else None)

register("Hologram", Rarity.UNCOMMON, "X0.25 Mult per playing card added to your deck",
         cost=7, init_counter=1.0,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))

register("Baseball Card", Rarity.RARE, "Uncommon Jokers each give X1.5 Mult", cost=8,
         independent=lambda j, ctx: ctx.times_mult(
             1.5 ** sum(1 for o in ctx.game.jokers
                        if o.spec.rarity is Rarity.UNCOMMON), j.name))

# --------------------------------------------------------------------------
# retrigger and copy jokers
# --------------------------------------------------------------------------

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
