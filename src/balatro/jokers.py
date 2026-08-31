"""Joker definitions.

Jokers are data, not engine code: each one is a `JokerSpec` with optional hooks
that the scoring pipeline calls. Adding a joker means adding a `register(...)`
call here -- the engine never needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Callable

from .cards import Card, Edition, Enhancement, Rank, Seal, Suit
# consumables does not import jokers, so this direction is safe; shop
# imports both.
from .consumables import ConsumableKind
from .effects import ScoreContext
from .hands import LEVEL_GAIN, HandType
from . import shop_pool

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

    # Triggers outside the scoring of a hand. The game fires these at points
    # the scoring pipeline never reaches, and a joker whose whole effect lives
    # here scores nothing -- which is why they were invisible to the
    # differential and had to be listed as unbuilt rather than assumed done.
    on_blind_select: RoundHook | None = None   # Cartomancer, Marble Joker
    on_round_start: RoundHook | None = None    # Certificate
    on_sell: RoundHook | None = None           # Diet Cola, Luchador
    before_hand: object = None                 # DNA, Sixth Sense
    after_hand: IndepHook | None = None        # Superposition, Séance
    on_first_discard: DiscardHook | None = None   # Burnt Joker, Trading Card
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

    # Jokers that change the shape of a run rather than the score of a hand.
    # These are read by the shop and the round, not by the scoring pipeline,
    # so they are declared rather than hooked -- a hook that never fires
    # during scoring is indistinguishable from a joker that does nothing.
    free_rerolls: int = 0             # Chaos the Clown
    debt_limit: int = 0               # Credit Card
    interest_bonus: int = 0           # To the Moon, per $5 held
    free_planets: bool = False        # Astronomer
    allows_duplicates: bool = False   # Showman
    prevents_death: bool = False      # Mr. Bones
    disables_boss_on_sell: bool = False   # Luchador


@dataclass
class JokerInstance:
    spec: JokerSpec
    edition: Edition = Edition.NONE
    counter: float = 0.0
    eternal: bool = False
    # Jokers that count hands measure from when they were acquired, not from
    # the start of the run -- the game stores this as hands_played_at_create.
    hands_at_create: int = 0
    # Egg grows this on its own; Gift Card grows every joker's.
    extra_sell_value: float = 0.0

    def __post_init__(self) -> None:
        if self.counter == 0.0:
            self.counter = self.spec.init_counter

    @property
    def name(self) -> str:
        return self.spec.name

    # What an edition adds to a card's price, and so to half of it. From
    # Card:set_cost, where the same numbers serve buying and selling.
    _EDITION_VALUE = {Edition.NONE: 0, Edition.FOIL: 2, Edition.HOLOGRAPHIC: 3,
                      Edition.POLYCHROME: 5, Edition.NEGATIVE: 5}

    @property
    def sell_value(self) -> int:
        """Half the price, and the price includes the edition.

        Ignoring the edition made a polychrome joker sell for what a plain one
        sells for. It is not a rounding difference: a Hex turns a joker
        polychrome, which is five dollars on its price and two on its sell
        value, and Temperance pays out the sell value of every joker held.
        """
        cost = self.spec.cost + self._EDITION_VALUE[self.edition]
        return max(1, cost // 2) + int(self.extra_sell_value)

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

_SMEARED_PAIRS = {Suit.HEARTS: Suit.DIAMONDS, Suit.DIAMONDS: Suit.HEARTS,
                  Suit.SPADES: Suit.CLUBS, Suit.CLUBS: Suit.SPADES}


def suit_matches(card: Card, suit: Suit, ctx: ScoreContext) -> bool:
    """Does this card count as that suit, for this run?

    Smeared Joker makes Hearts and Diamonds one suit and Spades and Clubs
    another, which no property on the card can know about -- so every suit
    test a joker makes has to come through here.
    """
    if card.counts_as_suit(suit):
        return True
    if ctx.game.has_smeared():
        return card.counts_as_suit(_SMEARED_PAIRS[suit])
    return False


def is_face(card: Card, ctx: ScoreContext) -> bool:
    """Pareidolia makes every card a face card, stone cards excepted."""
    if card.is_stone:
        return False
    return ctx.game.has_pareidolia() or card.rank.is_face


def _suit_scorer(suit: Suit, amount: int) -> ScoredHook:
    def hook(j: JokerInstance, card: Card, ctx: ScoreContext) -> None:
        if suit_matches(card, suit, ctx):
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
        if not is_face(card, ctx):
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
         retrigger_scored=lambda j, c, ctx: 1 if is_face(c, ctx) else 0)
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
        if suit_matches(card, suit, ctx):
            ctx.add_chips(amount, j.name)
    return hook


def _suit_money(suit: Suit, amount: int) -> ScoredHook:
    def hook(j: JokerInstance, card: Card, ctx: ScoreContext) -> None:
        if suit_matches(card, suit, ctx):
            ctx.money_gained += amount
    return hook


def _first_face(ctx: ScoreContext) -> Card | None:
    """The first scoring face card, which Photograph multiplies."""
    for card in ctx.scoring:
        if is_face(card, ctx) and not card.debuffed:
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


def _lowest_held(ctx: ScoreContext) -> Card | None:
    """The card Raised Fist points at.

    The game walks the hand front to back keeping any card whose id is <= the
    best so far, so on a tie the *rightmost* of the equal-lowest cards wins.
    Taking the first minimum instead is invisible until the two differ --
    until one of them is debuffed, or Mime is retriggering whichever was
    chosen. Stone cards have no rank and are skipped; a Steel card is not,
    and can perfectly well be the lowest.
    """
    chosen, best = None, 15
    for card in ctx.held:
        if card.is_stone:
            continue
        if card.rank.value <= best:
            chosen, best = card, card.rank.value
    return chosen


def _raised_fist(j: JokerInstance, card: Card, ctx: ScoreContext) -> None:
    """Add double that card's nominal value, once it is the one being held.

    This is a held-card trigger rather than an independent one, which is what
    makes Mime retrigger it: Mime repeats abilities of cards held in hand, and
    Raised Fist's mult is attached to the card it points at.
    """
    if card is not _lowest_held(ctx):
        return
    if card.debuffed:      # a debuffed choice pays nothing; it does not
        return             # fall through to the next lowest card
    ctx.add_mult(2 * card.rank.chips, j.name)


register("Raised Fist", Rarity.COMMON,
         "Adds double the rank of the lowest card held in hand to Mult",
         cost=5, held=_raised_fist)

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


# --------------------------------------------------------------------------
# jokers that change what the cards are
# --------------------------------------------------------------------------
#
# These four score nothing themselves. They rewrite the rules the other hooks
# read -- which suit a card counts as, whether it is a face card, which cards
# score at all, how likely a listed chance is -- so the run is asked, not the
# card. See suit_matches, is_face, GameState._splash and probability_scale.

register("Splash", Rarity.COMMON, "Every played card counts in scoring",
         cost=3)
register("Pareidolia", Rarity.UNCOMMON, "All cards are considered face cards",
         cost=5)
register("Smeared Joker", Rarity.UNCOMMON,
         "Hearts and Diamonds count as the same suit, as do Spades and Clubs",
         cost=7)
register("Oops! All 6s", Rarity.UNCOMMON, "Doubles all listed probabilities",
         cost=4)


# -- chance-based scoring ---------------------------------------------------

def _chance(ctx: ScoreContext, key: str, numerator: int, denominator: int
            ) -> bool:
    """A listed probability, scaled by any Oops! All 6s in play."""
    scale = ctx.game.probability_scale()
    return ctx.game.rng.chance(key, numerator * scale, denominator)


register("Bloodstone", Rarity.UNCOMMON,
         "1 in 2 chance for played Hearts to give X1.5 Mult", cost=7,
         scored=lambda j, c, ctx: ctx.times_mult(1.5, j.name)
         if suit_matches(c, Suit.HEARTS, ctx)
         and _chance(ctx, "bloodstone", 1, 2) else None)
register("Business Card", Rarity.COMMON,
         "Played face cards have a 1 in 2 chance to give $2", cost=4,
         scored=lambda j, c, ctx: ctx.__setattr__(
             "money_gained", ctx.money_gained + 2)
         if is_face(c, ctx) and _chance(ctx, "business", 1, 2) else None)
register("Reserved Parking", Rarity.COMMON,
         "Each face card held in hand has a 1 in 2 chance to give $1", cost=6,
         held=lambda j, c, ctx: ctx.__setattr__(
             "money_gained", ctx.money_gained + 1)
         if is_face(c, ctx) and _chance(ctx, "parking", 1, 2) else None)
def _space_joker(j: JokerInstance, ctx: ScoreContext) -> None:
    """Upgrade the played hand, and score it at the new level.

    The game raises the level before the base chips and mult are read, so the
    upgrade pays on the very hand that triggered it. Scoring runs the base in
    first, so the level gain is added to the context by hand -- levelling up
    alone leaves the hand scored at its old value and only the next one
    benefits.
    """
    if not _chance(ctx, "space", 1, 4):
        return
    ctx.game.hand_levels.level_up(ctx.hand)
    chips, mult = LEVEL_GAIN[ctx.hand]
    ctx.add_chips(chips, j.name)
    ctx.add_mult(mult, j.name)


register("Space Joker", Rarity.UNCOMMON,
         "1 in 4 chance to upgrade the level of the played poker hand", cost=5,
         update=_space_joker, update_before_scoring=True)


# -- jokers that name a card or hand the round chose ------------------------

register("The Idol", Rarity.UNCOMMON,
         "Each played card of a rank and suit that changes each round gives "
         "X2 Mult", cost=6,
         scored=lambda j, c, ctx: ctx.times_mult(2.0, j.name)
         if ctx.game.idol_rank is not None and c.rank is ctx.game.idol_rank
         and suit_matches(c, ctx.game.idol_suit, ctx) else None)
register("Ancient Joker", Rarity.RARE,
         "Each played card of a suit that changes each round gives X1.5 Mult",
         cost=8,
         scored=lambda j, c, ctx: ctx.times_mult(1.5, j.name)
         if ctx.game.ancient_suit is not None
         and suit_matches(c, ctx.game.ancient_suit, ctx) else None)
register("To Do List", Rarity.COMMON,
         "Earn $4 if the poker hand is one that changes each round", cost=4,
         independent=lambda j, ctx: ctx.__setattr__(
             "money_gained", ctx.money_gained + 4)
         if ctx.hand is ctx.game.todo_hand else None)


# -- the rest of the scoring batch ------------------------------------------

register("Midas Mask", Rarity.UNCOMMON,
         "All played face cards become Gold cards when scored", cost=7,
         scored=lambda j, c, ctx: setattr(c, "enhancement", Enhancement.GOLD)
         if is_face(c, ctx) else None)
register("Seltzer", Rarity.UNCOMMON,
         "Retrigger all played cards for the next 10 hands", cost=6,
         init_counter=10.0,
         retrigger_scored=lambda j, c, ctx: 1 if j.counter > 0 else 0)
register("Matador", Rarity.UNCOMMON,
         "Earn $8 if the played hand triggers the Boss Blind ability", cost=7,
         independent=lambda j, ctx: ctx.__setattr__(
             "money_gained", ctx.money_gained + 8)
         if ctx.game.boss is not None else None)
register("Red Card", Rarity.COMMON,
         "Gains +3 Mult when any Booster Pack is skipped", cost=5,
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name))
register("Madness", Rarity.UNCOMMON,
         "Gains X0.5 Mult when a Small or Big Blind is selected, and destroys "
         "a random Joker", cost=7, init_counter=1.0,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))


# --------------------------------------------------------------------------
# jokers that shape the run rather than the hand
# --------------------------------------------------------------------------
#
# None of these move chips or mult, so the scoring differential cannot see
# them; they are exercised where their effect actually lands -- the shop, the
# round boundary, the deck. Registering them still matters even where the
# effect is not yet wired: a joker missing from the registry cannot be offered
# at all, and a policy trained on a shop that never contains Credit Card is
# learning a different game from the one it will be tested on.


def _round_money(amount) -> RoundHook:
    """Pay at the end of a round, which is where the game pays these."""
    def hook(j: JokerInstance, game: "GameState") -> None:
        value = amount(j, game) if callable(amount) else amount
        if value:
            game.add_money(int(value), j.name)
    return hook


# -- hand size, hands and discards ------------------------------------------

register("Juggler", Rarity.COMMON, "+1 hand size", cost=4, hand_size=1)
register("Drunkard", Rarity.COMMON, "+1 discard each round", cost=4,
         extra_discards=1)
register("Merry Andy", Rarity.UNCOMMON, "+3 discards each round, -1 hand size",
         cost=7, hand_size=-1, extra_discards=3)
register("Troubadour", Rarity.UNCOMMON, "+2 hand size, -1 hand each round",
         cost=6, hand_size=2, extra_hands=-1)
register("Turtle Bean", Rarity.UNCOMMON,
         "+5 hand size, reduced by 1 every round", cost=6, init_counter=5.0,
         round_end=lambda j, g: _bump(j, -1, floor=0.0))
register("Burglar", Rarity.UNCOMMON,
         "When Blind is selected, gain +3 Hands and lose all discards",
         cost=6, extra_hands=3)


# -- money at the end of a round --------------------------------------------

register("Cloud 9", Rarity.UNCOMMON,
         "Earn $1 for each 9 in your full deck at end of round", cost=7,
         round_end=_round_money(
             lambda j, g: sum(1 for c in g.full_deck if c.rank is Rank.NINE)))
register("Rocket", Rarity.UNCOMMON,
         "Earn $1 at end of round, increasing by $2 per Boss Blind defeated",
         cost=6, init_counter=1.0,
         round_end=_round_money(lambda j, g: j.counter))
register("Satellite", Rarity.UNCOMMON,
         "Earn $1 at end of round per unique Planet card used this run",
         cost=6,
         round_end=_round_money(lambda j, g: len(g.unique_planets)))
register("Egg", Rarity.COMMON, "Gains $3 of sell value at end of round",
         cost=4,
         round_end=lambda j, g: setattr(j, "extra_sell_value",
                                        j.extra_sell_value + 3))
def _gift_card(j: JokerInstance, game: "GameState") -> None:
    for other in game.jokers:
        other.extra_sell_value += 1


register("Gift Card", Rarity.UNCOMMON,
         "Adds $1 of sell value to every Joker and Consumable at end of round",
         cost=6, round_end=_gift_card)
register("Delayed Gratification", Rarity.COMMON,
         "Earn $2 per discard if no discards are used by end of the round",
         cost=4,
         round_end=_round_money(
             lambda j, g: 2 * g.discards_left if g.discards_used == 0 else 0))
register("Mail-In Rebate", Rarity.COMMON,
         "Earn $5 for each discarded card of a rank that changes every round",
         cost=4,
         discarded=lambda j, cards, g: g.add_money(
             5 * sum(1 for c in cards if c.rank is g.mail_rank), j.name)
         if g.mail_rank is not None else None)
register("To the Moon", Rarity.UNCOMMON,
         "Earn an extra $1 of interest for every $5 at end of round", cost=5,
         interest_bonus=1)


# -- shop and run structure -------------------------------------------------

register("Chaos the Clown", Rarity.COMMON, "1 free Reroll per shop", cost=4,
         free_rerolls=1)
register("Credit Card", Rarity.COMMON, "Go up to -$20 in debt", cost=1,
         debt_limit=20)
register("Astronomer", Rarity.UNCOMMON,
         "All Planet cards and Celestial Packs in the shop are free", cost=8,
         free_planets=True)
register("Showman", Rarity.UNCOMMON,
         "Joker, Tarot, Planet and Spectral cards may appear multiple times",
         cost=5, allows_duplicates=True)
register("Mr. Bones", Rarity.UNCOMMON,
         "Prevents death if chips scored are at least 25% of the requirement, "
         "then self destructs", cost=5, prevents_death=True)
register("Luchador", Rarity.UNCOMMON,
         "Sell this card to disable the current Boss Blind", cost=5,
         disables_boss_on_sell=True)
register("Invisible Joker", Rarity.RARE,
         "After 2 rounds, sell this card to duplicate a random Joker", cost=8,
         round_end=lambda j, g: _bump(j, 1))
register("Diet Cola", Rarity.UNCOMMON,
         "Sell this card to create a free Double Tag", cost=6)


# -- cards created on a condition -------------------------------------------
#
# The condition each one waits for is written down even where the simulator
# cannot yet make the card, so that the trigger is already right when creation
# arrives rather than being guessed at then.

def _marble(j: JokerInstance, game: "GameState") -> None:
    game.add_card(Card(Rank.ACE, Suit.SPADES,
                       enhancement=Enhancement.STONE))


register("Marble Joker", Rarity.UNCOMMON,
         "Adds one Stone card to the deck when Blind is selected", cost=6,
         on_blind_select=_marble)
register("Cartomancer", Rarity.UNCOMMON,
         "Create a Tarot card when Blind is selected", cost=6,
         on_blind_select=lambda j, g: g.add_consumables(
             g.random_consumables(ConsumableKind.TAROT, 1, "car")))
def _certificate(j: JokerInstance, game: "GameState") -> None:
    """A random card with a random seal, straight into the hand.

    Two draws, not three: the game picks a face out of G.P_CARDS in one go
    -- rank and suit together, from the same pool -- and then rolls the seal
    against thresholds. Drawing the rank and the suit separately is three
    rolls from names the game does not have.
    """
    front = game.rng.random_element(shop_pool.FRONTS, "cert_fr")
    suit, rank = front.split("_")
    by_rank = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
               "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT,
               "9": Rank.NINE, "T": Rank.TEN, "J": Rank.JACK,
               "Q": Rank.QUEEN, "K": Rank.KING, "A": Rank.ACE}
    by_suit = {"C": Suit.CLUBS, "D": Suit.DIAMONDS, "H": Suit.HEARTS,
               "S": Suit.SPADES}
    roll = game.rng.pseudorandom("certsl")
    seal = (Seal.RED if roll > 0.75 else Seal.BLUE if roll > 0.5
            else Seal.GOLD if roll > 0.25 else Seal.PURPLE)
    card = Card(by_rank[rank], by_suit[suit], seal=seal)
    # Into the hand *and* into the deck. The game makes a real playing card
    # -- create_playing_card registers it in G.playing_cards -- so the run is
    # fifty-three cards from here on and every later draw comes off a
    # different deck. Putting it only in the hand loses it at the end of the
    # round.
    game.full_deck.append(card)
    game.hand.append(card)


register("Certificate", Rarity.UNCOMMON,
         "When the round begins, add a random playing card with a random seal "
         "to your hand", cost=6, on_round_start=_certificate)
def _riff_raff(j: JokerInstance, game: "GameState") -> None:
    for _ in range(2):
        game.add_random_joker("Riff-Raff", Rarity.COMMON)


register("Riff-Raff", Rarity.COMMON,
         "When Blind is selected, create 2 Common Jokers", cost=6,
         on_blind_select=_riff_raff)
register("8 Ball", Rarity.COMMON,
         "1 in 4 chance for each played 8 to create a Tarot card when scored",
         cost=5,
         scored=lambda j, c, ctx: ctx.game.add_consumables(
             ctx.game.random_consumables(ConsumableKind.TAROT, 1, "8ba"))
         if c.rank is Rank.EIGHT and not c.is_stone
         and _chance(ctx, "8ball", 1, 4) else None)
register("Hallucination", Rarity.COMMON,
         "1 in 2 chance to create a Tarot card when a Booster Pack is opened",
         cost=4)
register("Superposition", Rarity.COMMON,
         "Create a Tarot card if the poker hand contains an Ace and a Straight",
         cost=4,
         after_hand=lambda j, ctx: ctx.game.add_consumables(
             ctx.game.random_consumables(ConsumableKind.TAROT, 1, "sup"))
         if ctx.hand in CONTAINS_STRAIGHT
         and any(c.rank is Rank.ACE for c in ctx.scoring) else None)
register('Séance', Rarity.UNCOMMON,
         "If the poker hand is a Straight Flush, create a random Spectral card",
         cost=6,
         after_hand=lambda j, ctx: ctx.game.add_consumables(
             ctx.game.random_consumables(ConsumableKind.SPECTRAL, 1, "sea"))
         if ctx.hand is HandType.STRAIGHT_FLUSH else None)
register("Vagabond", Rarity.RARE,
         "Create a Tarot card if a hand is played with $4 or less", cost=8,
         after_hand=lambda j, ctx: ctx.game.add_consumables(
             ctx.game.random_consumables(ConsumableKind.TAROT, 1, "vag"))
         if ctx.game.money <= 4 else None)
def _sixth_sense(j: JokerInstance, played: list, game: "GameState") -> None:
    if len(played) == 1 and played[0].rank is Rank.SIX:
        game.remove_card(played[0])
        game.add_consumables(
            game.random_consumables(ConsumableKind.SPECTRAL, 1, "sixth"))


register("Sixth Sense", Rarity.UNCOMMON,
         "If the first hand of a round is a single 6, destroy it and create a "
         "Spectral card", cost=6, before_hand=_sixth_sense)
def _dna(j: JokerInstance, played: list, game: "GameState") -> None:
    if len(played) == 1:
        copy = played[0].copy()
        game.full_deck.append(copy)
        game.hand.append(copy)


register("DNA", Rarity.RARE,
         "If the first hand of a round has only 1 card, add a permanent copy "
         "to the deck and draw it to hand", cost=8, before_hand=_dna)
def _trading_card(j: JokerInstance, cards: list, game: "GameState") -> None:
    if len(cards) == 1:
        game.remove_card(cards[0])
        game.add_money(3, "Trading Card")


register("Trading Card", Rarity.UNCOMMON,
         "If the first discard of a round has only 1 card, destroy it and "
         "earn $3", cost=6, on_first_discard=_trading_card)
def _burnt(j: JokerInstance, cards: list, game: "GameState") -> None:
    game.hand_levels.level_up(game.evaluate_selection(list(cards)).hand)


register("Burnt Joker", Rarity.RARE,
         "Upgrade the level of the first discarded poker hand each round",
         cost=8, on_first_discard=_burnt)
