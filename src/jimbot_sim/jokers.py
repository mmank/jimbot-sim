"""Joker definitions.

Jokers are data, not engine code: each one is a `JokerSpec` with optional hooks
that the scoring pipeline calls. Adding a joker means adding a `register(...)`
call here -- the engine never needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Callable

from .cards import Card, Edition, Enhancement, Rank, Seal, Suit, next_sort_id
# consumables does not import jokers, so this direction is safe; shop
# imports both.
from .consumables import ConsumableKind
from .effects import ScoreContext
from .blinds import BlindKind
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
    init_secondary: float = 0.0
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
    # Money listed on the cash-out screen, as against `round_end` which is
    # decay and growth the moment the round closes.
    round_money: RoundHook | None = None       # Golden Joker, Rocket
    on_reroll: RoundHook | None = None         # Flash Card
    on_pack_skip: RoundHook | None = None      # Red Card
    on_pack_open: RoundHook | None = None      # Hallucination
    # Cards leaving the deck, whatever took them: a shattered glass card, a
    # Hanged Man, an Immolate. Canio and Glass Joker both feed on it.
    on_cards_destroyed: object = None          # Canio
    # Narrower than the above on purpose: only glass cards that shattered
    # while scoring, which is the game's own separate list.
    on_glass_shattered: object = None          # Glass Joker
    # Leaving the shop, which is Perkeo's moment.
    on_shop_end: RoundHook | None = None       # Perkeo
    rerolls_a_hand: bool = False               # To Do List
    before_hand: object = None                 # DNA, Sixth Sense
    # context.debuffed_hand: a hand the boss refused still asks every joker,
    # after scoring nothing (state_events.lua:1015-1027).
    on_debuffed_hand: RoundHook | None = None  # Matador
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
    # True when the hand size this joker gives is its counter rather than a
    # fixed number -- Turtle Bean starts at five and loses one a round.
    hand_size_from_counter: bool = False
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

    def __reduce__(self):
        """Pickle and copy as the registry entry, by name.

        A spec is the *rules* for a joker, not any run's state: it is built
        once at import and every instance in every game points at the same
        one. Copying it is therefore always wrong, and mostly impossible --
        the hooks are closures over the joker's numbers, which pickle refuses
        outright ("Can't get local object '_hand_mult.<locals>.hook'"). That
        is what a run's state hits the moment a Sly Joker is in the row and
        anything tries to serialise the game, which is what forking a
        position does.

        Restored by lookup instead. That keeps identity as well as value:
        the same Blueprint is the same object on both sides of a copy, so
        `is` comparisons between a spec and a registry entry still hold, and
        deepcopy stops silently making second copies of the rules. It applies
        to copy.deepcopy as well as pickle -- both consult __reduce__.
        """
        return (_registered, (self.name,))


# What an edition adds to a card's price, and so to half of it. From
# Card:set_cost, where the same numbers serve buying and selling.
EDITION_VALUE = {Edition.NONE: 0, Edition.FOIL: 2, Edition.HOLOGRAPHIC: 3,
                 Edition.POLYCHROME: 5, Edition.NEGATIVE: 5}


@dataclass
class JokerInstance:
    spec: JokerSpec
    # Age, for the random draws that sort by it -- see cards.next_sort_id.
    # Stamped here, when the joker is built, because that is where Card:init
    # stamps sort_id (card.lua:24-25). A shop builds its shelf in slot order
    # (game.lua:3111-3113) and buying moves that same card into the row
    # (button_callbacks.lua:2417-2435), so a joker bought second out of an
    # earlier slot is the older one: recording 6 buys Misprint (53) with
    # Devious (58) held, recording 8 Astronomer (235) after Hanging Chad (236).
    # A shelf joker never bought just spends an id, as it does in the game.
    uid: int = field(default_factory=next_sort_id)
    edition: Edition = Edition.NONE
    counter: float = 0.0
    eternal: bool = False
    # The stake's stickers. Perishable counts rounds down and debuffs the
    # joker at zero; rental takes three dollars at the end of every round.
    perishable: bool = False
    perish_tally: int = 0
    rental: bool = False
    debuffed: bool = False
    # Jokers that count hands measure from when they were acquired, not from
    # the start of the run -- the game stores this as hands_played_at_create.
    hands_at_create: int = 0
    # A second counter for the jokers that keep two numbers -- Yorick's
    # countdown to its next X1, Invisible Joker's rounds held.
    secondary: float = 0.0
    # Egg grows this on its own; Gift Card grows every joker's.
    extra_sell_value: float = 0.0
    # To Do List's poker hand. The game keeps it in the joker's own ability
    # table -- ability.to_do_poker_hand -- so two of them name two hands.
    named_hand: object = None

    def __post_init__(self) -> None:
        if self.counter == 0.0:
            self.counter = self.spec.init_counter
        if self.secondary == 0.0:
            self.secondary = self.spec.init_secondary

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def sell_value(self) -> int:
        """Half the price, and the price includes the edition.

        Ignoring the edition made a polychrome joker sell for what a plain one
        sells for. It is not a rounding difference: a Hex turns a joker
        polychrome, which is five dollars on its price and two on its sell
        value, and Temperance pays out the sell value of every joker held.
        """
        # The list price, ignoring the run's discount -- GameState.sell_value
        # is the one that knows about Liquidation and should be preferred
        # wherever the run is at hand. A rental costs a dollar however
        # expensive the joker is, so it sells for one.
        cost = 1 if self.rental else (self.spec.cost
                                      + EDITION_VALUE[self.edition])
        return max(1, cost // 2) + int(self.extra_sell_value)

    def __repr__(self) -> str:
        tag = "" if self.edition is Edition.NONE else f"[{self.edition.value}]"
        num = "" if self.counter == self.spec.init_counter else f"({self.counter:g})"
        return f"{self.spec.name}{tag}{num}"


REGISTRY: dict[str, JokerSpec] = {}


def _registered(name: str) -> JokerSpec:
    """The spec of this name, for JokerSpec.__reduce__ to restore through."""
    try:
        return REGISTRY[name]
    except KeyError:
        raise LookupError(
            "no joker named %r is registered, so a copy of one cannot be "
            "restored; every spec is built inside register()" % (name,)
        ) from None


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


def suit_matches_for(card: Card, suit: Suit, game) -> bool:
    """Does this card count as that suit, for this run?

    Smeared Joker makes Hearts and Diamonds one suit and Spades and Clubs
    another, which no property on the card can know about -- so every suit
    test a joker makes has to come through here. Takes the run rather than a
    scoring context, because the tests a joker makes while *discarding* have
    no context to hand.
    """
    if card.counts_as_suit(suit):
        return True
    if game.has_smeared():
        return card.counts_as_suit(_SMEARED_PAIRS[suit])
    return False


def suit_matches(card: Card, suit: Suit, ctx: ScoreContext) -> bool:
    """The same question, asked from inside scoring."""
    return suit_matches_for(card, suit, ctx.game)


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
        if hands & ctx.contains:
            ctx.add_mult(amount, j.name)
    return hook


def _hand_chips(hands: set[HandType], amount: int) -> IndepHook:
    def hook(j: JokerInstance, ctx: ScoreContext) -> None:
        if hands & ctx.contains:
            ctx.add_chips(amount, j.name)
    return hook


def _hand_xmult(hands: set[HandType], factor: float) -> IndepHook:
    def hook(j: JokerInstance, ctx: ScoreContext) -> None:
        if hands & ctx.contains:
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


# Each of these is now the sub-hand itself: the played cards carry a table
# of everything they contain, so "contains a Pair" is a membership test
# rather than a list of the top hands that imply one. That list could not
# express a Flush that happens to hold a pair, which the game counts.
CONTAINS_PAIR = {HandType.PAIR}
CONTAINS_TRIPS = {HandType.THREE_OF_A_KIND}
CONTAINS_TWO_PAIR = {HandType.TWO_PAIR}
CONTAINS_QUADS = {HandType.FOUR_OF_A_KIND}
CONTAINS_STRAIGHT = {HandType.STRAIGHT}
CONTAINS_FLUSH = {HandType.FLUSH}
CONTAINS_STRAIGHT_FLUSH = {HandType.STRAIGHT_FLUSH}

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
         independent=lambda j, ctx: ctx.add_chips(2 * ctx.money, j.name))
register("Bootstraps", Rarity.UNCOMMON, "+2 Mult per $5 held", cost=7,
         independent=lambda j, ctx: ctx.add_mult(2 * (ctx.money // 5), j.name))


def _ride_update(j: JokerInstance, ctx: ScoreContext) -> None:
    # A debuffed face card does not score, so it does not break the streak --
    # against The Club a debuffed King leaves the counter climbing. Missing
    # this only shows up on a boss blind, which is why it survived until the
    # scenario matrix reached one.
    #
    # And `is_face`, not `rank.is_face`: Pareidolia makes every card a face
    # card (card.lua:967), so a Ride the Bus held beside one can never grow
    # at all. The game knows; this counted to eight while the engine sat at
    # zero, which the policy found by playing the engine with a shadow.
    if any(is_face(c, ctx) and not c.debuffed for c in ctx.scoring):
        j.counter = 0.0
    else:
        j.counter += 1


def _bump(j: JokerInstance, amount: float, floor: float | None = None) -> None:
    value = j.counter + amount
    j.counter = value if floor is None else max(floor, value)


def _decay(j: JokerInstance, amount: float, game: "GameState",
           floor: float = 0.0) -> None:
    """Spend a joker down, and destroy it when it runs out.

    Popcorn, Ice Cream, Turtle Bean and Ramen do not sit at zero doing
    nothing -- the game eats them. It checks *before* subtracting, so a
    Popcorn on four mult with four to lose is gone rather than reduced, and a
    run with one of these has a joker slot free again on a schedule. Flooring
    the counter instead, which is what this did, left a dead joker taking up
    a slot for the rest of the run.
    """
    if j.counter + amount <= floor:
        game.destroy_joker(j, "eaten")
        return
    j.counter += amount


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
         update=lambda j, ctx: _bump(j, 15) if CONTAINS_STRAIGHT & ctx.contains else None,
         update_before_scoring=True,
         independent=lambda j, ctx: ctx.add_chips(j.counter, j.name))

register("Ice Cream", Rarity.COMMON, "+100 Chips, -5 Chips per hand played",
         cost=5, init_counter=100.0,
         independent=lambda j, ctx: ctx.add_chips(j.counter, j.name),
         update=lambda j, ctx: _decay(j, -5, ctx.game))

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
         round_end=lambda j, g: _decay(j, -4, g))

register("Swashbuckler", Rarity.COMMON, "+Mult equal to sell value of other Jokers",
         cost=4,
         independent=lambda j, ctx: ctx.add_mult(
             sum(ctx.game.sell_value(o) for o in ctx.game.jokers
                 if o is not j), j.name))

register("Golden Joker", Rarity.COMMON, "Earn $4 at end of round", cost=6,
         round_money=lambda j, g: g.add_money(4, "Golden Joker"))

def _faceless(j: JokerInstance, cards: list, game: "GameState") -> None:
    """$5 when three of the discarded cards are face cards, as is_face says.

    card.lua:2858-2861 counts `v:is_face()` over the whole discard, and
    Card:is_face (card.lua:964-969) is not the printed rank:

        if self.debuff and not from_boss then return end
        local id = self:get_id()
        if id == 11 or id == 12 or id == 13 or next(find_joker("Pareidolia"))

    so a debuffed card is never a face card, a Stone King is not one either
    (get_id is a random negative for Stone, card.lua:958-960), and with
    Pareidolia held every live card is -- Stone included, since that test
    does not look at the id.
    """
    pareidolia = game.has_pareidolia()
    faces = sum(1 for c in cards
                if not c.debuffed
                and (pareidolia or (not c.is_stone and c.rank.is_face)))
    if faces >= 3:
        game.add_money(5, "Faceless Joker")


register("Faceless Joker", Rarity.COMMON, "Earn $5 if 3+ face cards discarded", cost=4,
         discarded=_faceless)


def _gros_michel_end(j: JokerInstance, g: "GameState") -> None:
    """1 in 6 at the end of a round, and extinction is recorded for good.

    card.lua:3037 sets `G.GAME.pool_flags.gros_michel_extinct` in the same
    branch that destroys the joker, and that flag is the whole of what gates
    Cavendish -- `yes_pool_flag = 'gros_michel_extinct'` -- and takes Gros
    Michel out of every later pool. Destroying it without the flag left
    Cavendish unobtainable for the rest of any run. Marcin's live run of
    QWEFRTUZ, Blue Deck, stake 5 stopped on it at decision 73: a reroll
    stocked Cavendish in the game and Delayed Gratification here.
    """
    if g.rng.chance("gros_michel", 1 * g.probability_scale(), 6):
        g.destroy_joker(j, "Gros Michel went extinct")
        g.pool_flags.add("gros_michel_extinct")


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

def counts_for_flush(card: Card, suit: Suit, game) -> bool:
    """`is_suit(suit, nil, true)` -- the flush_calc branch (card.lua:4065).

    The game asks its suit question two ways and they differ on a debuffed
    card. The ordinary test refuses one outright; the flush_calc one reads
    the printed suit anyway, and only a *wild* card loses its everything-suit
    to a debuff. That is what a flush is judged on, and what Blackboard is
    judged on: The Goad debuffs the Queen of Spades held in hand and
    Blackboard still counts it black, which took a flush from 2320 to 6960 in
    the game while the simulator left it at 2320.
    """
    # One implementation, shared with hand detection -- see
    # `hands.flush_suit`. The copy that lived here refused a debuffed Wild
    # card outright, where the game falls back to its printed suit.
    from .hands import flush_suit

    return flush_suit(card, suit, game.has_smeared())


register("Blackboard", Rarity.UNCOMMON,
         "X3 Mult if all cards held in hand are Spades or Clubs", cost=6,
         independent=lambda j, ctx: ctx.times_mult(3.0, j.name)
         if all(counts_for_flush(c, Suit.SPADES, ctx.game)
                or counts_for_flush(c, Suit.CLUBS, ctx.game)
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
    """Strip every enhanced card in the scoring hand, before it scores.

    context.before, alongside Midas Mask: the game walks the scoring hand
    once, up front, and sets each enhanced card back to c_base. So the
    enhancement it takes never pays out at all -- a mult card eaten by a
    Vampire gives its owner X0.1 and the hand nothing. Running this after the
    cards had scored, which is what an ordinary update does, let the
    enhancement pay first and then be removed, which is worth the whole
    enhancement every hand.

    Stone counts as enhanced here. The game's test is `center ~= c_base`, and
    a stone card is not c_base, so a Vampire eats one and hands the card its
    rank and suit back.
    """
    gained = 0
    for c in ctx.scoring:
        if c.enhancement is not Enhancement.NONE:
            ctx.game.set_enhancement(c, Enhancement.NONE)
            gained += 1
    j.counter += 0.1 * gained


register("Vampire", Rarity.UNCOMMON,
         "X0.1 Mult per scored enhanced card, removing the enhancement", cost=7,
         init_counter=1.0, update=_vampire, update_before_scoring=True,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))

register("Ramen", Rarity.UNCOMMON, "X2 Mult, -X0.01 per discarded card",
         cost=6, init_counter=2.0,
         discarded=lambda j, cards, g: _decay(j, -0.01 * len(cards), g, 1.0),
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

def _canio(j: JokerInstance, cards: list, game: "GameState") -> None:
    j.counter += sum(1 for c in cards if c.rank in (Rank.JACK, Rank.QUEEN,
                                                    Rank.KING))


register("Canio", Rarity.LEGENDARY, "X1 Mult, gains X1 Mult per face card destroyed",
         init_counter=1.0, on_cards_destroyed=_canio,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))

def _yorick(j: JokerInstance, cards: list, game: "GameState") -> None:
    """X1 for every twenty-three cards discarded, counted down not up.

    The game keeps yorick_discards ticking towards one and resets it when it
    gets there, which is why the counter is stored on the joker rather than
    derived from a running total: two Yoricks are on their own schedules.
    """
    for _ in cards:
        if j.secondary <= 1:
            j.secondary = 23
            j.counter += 1.0
        else:
            j.secondary -= 1


register("Yorick", Rarity.LEGENDARY, "X1 Mult, gains X1 Mult per 23 cards discarded",
         init_counter=1.0, init_secondary=23.0, discarded=_yorick,
         independent=lambda j, ctx: ctx.times_mult(j.counter, j.name))

register("Chicot", Rarity.LEGENDARY, "Disables the effect of every Boss Blind")

def _perkeo(j: JokerInstance, game: "GameState") -> None:
    """A Negative copy of one consumable held, as the shop closes.

    Negative, so it does not need a slot -- which is the whole point of the
    joker and the reason it is worth a legendary. The copy is drawn from what
    is actually in the slots, so an empty row gets nothing.

    The edition used to go nowhere, because the row held shared registry
    entries with no room for one: the copy took a slot like any other card,
    and a full row got nothing at all.
    """
    if not game.consumables:
        return
    chosen = game.rng.random_element(list(game.consumables), "perkeo")
    game.consumables.append(
        game.hold_consumable(chosen.spec, Edition.NEGATIVE))
    game.log("Perkeo: a negative %s" % chosen.name)


register("Perkeo", Rarity.LEGENDARY,
         "Creates a Negative copy of a random consumable at the end of the shop",
         on_shop_end=_perkeo)


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
def _flash_card(j: JokerInstance, game: "GameState") -> None:
    j.counter += 2.0


register("Flash Card", Rarity.UNCOMMON, "Gains +2 Mult per shop reroll",
         cost=5, on_reroll=_flash_card,
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
def _glass_joker(j: JokerInstance, cards: list, game: "GameState") -> None:
    """X0.75 for each glass card that *shattered*.

    Not each glass card destroyed. The game keeps two lists when a hand
    finishes scoring: `removed`, which is every playing card destroyed and is
    what Canio feeds on, and `glass_shattered`, which is the subset carrying
    `.shattered`. That flag is set in exactly one place -- a Glass Card in the
    scoring hand, undebuffed, whose one-in-four came up. A glass card taken by
    a Hanged Man is marked `destroyed` instead and Glass Joker gets nothing
    for it.
    """
    j.counter += 0.75 * len(cards)


register("Glass Joker", Rarity.UNCOMMON,
         "Gains X0.75 Mult per Glass card destroyed", enhancement_gate="m_glass",
         cost=6, init_counter=1.0, on_glass_shattered=_glass_joker,
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
         if CONTAINS_TWO_PAIR & ctx.contains else None,
         update_before_scoring=True,
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name))
register("Hiker", Rarity.UNCOMMON,
         "Every played card permanently gains +5 Chips when scored", cost=5,
         scored=lambda j, c, ctx: setattr(c, "extra_chips", c.extra_chips + 5))


# -- scaling jokers whose growth comes from outside the played hand ---------

def _ceremonial_dagger(j: JokerInstance, game: "GameState") -> None:
    """Eat the joker to the right, keep twice its sell value as mult.

    "To the right" is the next one along in the row, so this depends on the
    order the player has dragged them into. An eternal joker cannot be eaten
    -- the game checks it here as well, which is another reason to have the
    check in one place. The joker was registered with its mult but nothing
    ever did the eating, so it sat on zero for whole runs while the row kept
    a joker the game had taken away.
    """
    index = next((i for i, o in enumerate(game.jokers) if o is j), None)
    if index is None or index + 1 >= len(game.jokers):
        return
    victim = game.jokers[index + 1]
    # The joker to the right as the row stands mid-pass, a victim of Madness
    # included: that one is getting sliced, and the Dagger eats nothing rather
    # than reaching past it (card.lua:2566). Its own victim's slot comes back
    # at once, through the buffer (card.lua:2569); Madness gives none back.
    if victim.eternal or game.is_getting_sliced(victim):
        return
    game.joker_buffer -= 1
    j.counter += game.sell_value(victim) * 2
    game.slice_joker(victim, "Ceremonial Dagger")
    game.after_setting_blind(lambda: setattr(game, "joker_buffer", 0))


register("Ceremonial Dagger", Rarity.UNCOMMON,
         "When Blind is selected, destroy the Joker to the right and "
         "permanently add double its sell value to Mult", cost=6,
         on_blind_select=_ceremonial_dagger,
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name))
def _castle(j: JokerInstance, cards, game: "GameState") -> None:
    """+3 chips for each discarded card of the round's suit.

    card.lua:2814, under `context.discard` and per discarded card, skipping a
    debuffed one. Nothing incremented this counter, so a Castle scored +0
    chips for a whole run however much was thrown at it -- the same shape as
    Rocket, and found the same way: the policy played the engine with a
    simulator shadowing it, and a Full House came out 6840 against 6624.
    """
    suit = game.castle_suit
    if suit is None:
        return
    for card in cards:
        if not card.debuffed and suit_matches_for(card, suit, game):
            j.counter += 3.0


register("Castle", Rarity.UNCOMMON,
         "Gains +3 Chips per discarded card of a suit that changes each round",
         cost=6, discarded=_castle,
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
def _hit_the_road(j: JokerInstance, cards: list, game: "GameState") -> None:
    """X0.5 for every Jack discarded. A debuffed Jack does not count.

    Nor does a Stone one: card.lua:2835-2837 asks `get_id() == 11`, and get_id
    answers a Stone card with a random negative (card.lua:958-960).
    """
    j.counter += 0.5 * sum(1 for c in cards
                           if c.rank is Rank.JACK and not c.debuffed
                           and not c.is_stone)


register("Hit the Road", Rarity.RARE,
         "Gains X0.5 Mult for every Jack discarded this round", cost=8,
         init_counter=1.0, discarded=_hit_the_road,
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
         rerolls_a_hand=True,
         independent=lambda j, ctx: ctx.__setattr__(
             "money_gained", ctx.money_gained + 4)
         if ctx.hand is j.named_hand else None)


# -- the rest of the scoring batch ------------------------------------------

def _midas_mask(j: JokerInstance, ctx: ScoreContext) -> None:
    """Turn every scoring face card to Gold before anything scores.

    context.before, not per-card: the game walks the scoring hand once, up
    front, and calls set_ability(m_gold) on each face card. That matters
    because a card only has one enhancement -- a glass King turned gold by a
    Midas Mask never gets to be glass, so its X2 is simply gone. Converting
    per card as it scored let the first trigger keep the old enhancement,
    which is a whole X2 on the hand.
    """
    for card in ctx.scoring:
        if is_face(card, ctx):
            ctx.game.set_enhancement(card, Enhancement.GOLD)


register("Midas Mask", Rarity.UNCOMMON,
         "All played face cards become Gold cards when scored", cost=7,
         update=_midas_mask, update_before_scoring=True)
register("Seltzer", Rarity.UNCOMMON,
         "Retrigger all played cards for the next 10 hands", cost=6,
         init_counter=10.0,
         # Ten hands and then it is gone. The countdown runs after the hand
         # it retriggered, so the tenth hand still gets its retrigger and the
         # joker leaves with it. Nothing was counting at all, so a Seltzer
         # bought once retriggered for the rest of the run.
         update=lambda j, ctx: _decay(j, -1, ctx.game),
         retrigger_scored=lambda j, c, ctx: 1 if j.counter > 0 else 0)
def _matador_triggered(game: "GameState") -> bool:
    """`G.GAME.blind.triggered`, and nothing else (card.lua:2737, 3720).

    Not "is this a boss": a Flush into The Head debuffs no hand and triggers
    nothing, and paying for every boss hand made the simulator eight dollars
    a hand richer than the game. GameState._play and score_hand set it.
    """
    return game.blind is not None and game.blind.triggered


register("Matador", Rarity.UNCOMMON,
         "Earn $8 if the played hand triggers the Boss Blind ability", cost=7,
         independent=lambda j, ctx: ctx.__setattr__(
             "money_gained", ctx.money_gained + 8)
         if _matador_triggered(ctx.game) else None,
         # A refused hand scores nothing but still asks every joker, under
         # context.debuffed_hand (state_events.lua:1015-1027), and Matador
         # is the one joker that answers (card.lua:2735-2745).
         on_debuffed_hand=lambda j, g: g.add_money(8, j.name)
         if _matador_triggered(g) else None)
def _red_card(j: JokerInstance, game: "GameState") -> None:
    j.counter += 3.0


register("Red Card", Rarity.COMMON,
         "Gains +3 Mult when any Booster Pack is skipped", cost=5,
         on_pack_skip=_red_card,
         independent=lambda j, ctx: ctx.add_mult(j.counter, j.name))
def _madness(j: JokerInstance, game: "GameState") -> None:
    """Gain X0.5 and eat a joker -- but not on a Boss Blind.

    `not context.blind.boss`, so it feeds on the Small and Big and goes quiet
    for the one that matters. The joker it takes is drawn from the ones that
    are neither itself nor eternal. Nothing was growing it and nothing was
    eating, so it sat at X1 while its own drawback never arrived.

    The draw is pseudorandom_element(destructable_jokers,
    pseudoseed('madness')) (card.lua:2509), which sorts the list by sort_id
    before indexing (misc_functions.lua:260-261) -- by age, not by where the
    jokers sit. Row order ate Popcorn where the game ate Mystic Summit, on
    smoke run N1OA90W1 (test_madness_eats_by_age).
    """
    if game.blind is not None and game.blind.kind is BlindKind.BOSS:
        return
    j.counter += 0.5
    # Not a joker already getting sliced (card.lua:2507). The one taken is
    # only marked, and keeps its place and its slot until the pass is over.
    prey = sorted((o for o in game.jokers if o is not j and not o.eternal
                   and not game.is_getting_sliced(o)),
                  key=lambda o: o.uid)
    if prey:
        game.slice_joker(game.rng.random_element(prey, "madness"), j.name)


register("Madness", Rarity.UNCOMMON,
         "Gains X0.5 Mult when a Small or Big Blind is selected, and destroys "
         "a random Joker", cost=7, init_counter=1.0,
         on_blind_select=_madness,
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
    """Money listed on the cash-out screen, paid when the button is pressed.

    A different moment from `round_end`, which is the game's
    calculate_joker({end_of_round}) -- decay, growth and destruction, all of
    which happen the instant the round closes and before the screen appears.
    These are calculate_dollar_bonus, and they are rows on that screen.
    """
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
         hand_size_from_counter=True,
         round_end=lambda j, g: _decay(j, -1, g))
def _burglar(j: JokerInstance, game: "GameState") -> None:
    """Three hands, and every discard gone.

    Both halves land here rather than in the round's allowance, and the
    difference is only visible against a boss that dictates the allowance
    itself. Burglar is ease_hands_played(+3) on setting_blind, which runs
    *after* the blind has set the round up -- so The Needle, which allows one
    hand, allows four with a Burglar held. Folding the +3 into the base let
    The Needle overwrite it and the run played a single hand. Measured on the
    engine: four.
    """
    game.hands_left += 3
    game.discards_left = 0


register("Burglar", Rarity.UNCOMMON,
         "When Blind is selected, gain +3 Hands and lose all discards",
         cost=6, on_blind_select=_burglar)


# -- money at the end of a round --------------------------------------------

register("Cloud 9", Rarity.UNCOMMON,
         "Earn $1 for each 9 in your full deck at end of round", cost=7,
         round_money=_round_money(
             lambda j, g: sum(1 for c in g.full_deck if c.rank is Rank.NINE)))
def _rocket(j: JokerInstance, game: "GameState") -> None:
    """+$2 a boss, and the boss that raises it is already paying the raise.

    The growth is `calculate_joker({end_of_round})` (card.lua:2896, guarded on
    `G.GAME.blind.boss`), which state_events.lua runs at line 101 -- and the
    cash-out rows are built from calculate_dollar_bonus afterwards, at line
    1176. So a Rocket held through its first boss pays three dollars that
    round, not one. Nothing incremented this counter at all, so it paid a
    dollar a round for whole runs; found by the hand-written policy playing
    the engine with a simulator shadowing it, which is what live.py is for.
    """
    if game.beaten_was_boss:
        j.counter += 2.0


register("Rocket", Rarity.UNCOMMON,
         "Earn $1 at end of round, increasing by $2 per Boss Blind defeated",
         cost=6, init_counter=1.0, round_end=_rocket,
         round_money=_round_money(lambda j, g: j.counter))
register("Satellite", Rarity.UNCOMMON,
         "Earn $1 at end of round per unique Planet card used this run",
         cost=6,
         round_money=_round_money(lambda j, g: len(g.unique_planets)))
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
         round_money=_round_money(
             lambda j, g: 2 * g.discards_left if g.discards_used == 0 else 0))
def _mail_in(j: JokerInstance, cards: list, game: "GameState") -> None:
    """$5 for each discarded card of the round's rank -- a live, ranked one.

    card.lua:2825-2827, per discarded card:

        not context.other_card.debuff and
        context.other_card:get_id() == G.GAME.current_round.mail_card.id

    A debuffed card is skipped, and a Stone card never matches, because get_id
    answers it with `-math.random(100, 1000000)` (card.lua:958-960). The smoke
    test found both, $5 out each time: NXE7XRN1, UBDY5AUG and XGC81J77 threw
    a card of the rank that The Pillar or The Club had debuffed, and WA1RMJNV
    threw a Stone Ace in an Ace round.
    """
    if game.mail_rank is None:
        return
    game.add_money(5 * sum(1 for c in cards
                           if not c.debuffed and not c.is_stone
                           and c.rank is game.mail_rank), j.name)


register("Mail-In Rebate", Rarity.COMMON,
         "Earn $5 for each discarded card of a rank that changes every round",
         cost=4, discarded=_mail_in)
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
def _invisible_sold(j: JokerInstance, game: "GameState") -> None:
    """Sold after two rounds, a copy of a random other joker (card.lua:2371-2390).

        if invis_rounds >= extra (2, game.lua:513) and not context.blueprint
            jokers = G.jokers.cards other than self
            if #jokers > 0 and #G.jokers.cards <= G.jokers.config.card_limit
                chosen = pseudorandom_element(jokers, pseudoseed('invisible'))
                card = copy_card(chosen, ..., chosen.edition.negative)
                if card.ability.invis_rounds then card.ability.invis_rounds = 0

    selling_self fires before the card dissolves (card.lua:1599), so the room
    check counts this joker as still in the row, and a Negative one as still
    giving its slot. The simulator runs this after the pop, so both go back
    in. pseudorandom_element sorts by sort_id first; the row's order is not
    the draw's. A debuffed joker answers no context (card.lua:2292).
    """
    if j.debuffed or j.counter < 2:
        return
    others = sorted(game.jokers, key=lambda o: o.uid)
    if not others:
        return
    held = len(game.jokers) + 1
    limit = game.joker_slots + (1 if j.edition is Edition.NEGATIVE else 0)
    if held > limit:
        return
    chosen = game.rng.choice("invisible", others)
    clone = game.copy_joker(chosen, j.name)
    if clone.spec is j.spec:
        clone.counter = 0


register("Invisible Joker", Rarity.RARE,
         "After 2 rounds, sell this card to duplicate a random Joker", cost=8,
         round_end=lambda j, g: _bump(j, 1), on_sell=_invisible_sold)
def _diet_cola(j: JokerInstance, game: "GameState") -> None:
    game.add_tag_by_key("tag_double")


register("Diet Cola", Rarity.UNCOMMON,
         "Sell this card to create a free Double Tag", cost=6,
         on_sell=_diet_cola)


# -- cards created on a condition -------------------------------------------
#
# The condition each one waits for is written down even where the simulator
# cannot yet make the card, so that the trigger is already right when creation
# arrives rather than being guessed at then.

def _marble(j: JokerInstance, game: "GameState") -> None:
    """A Stone card with a random front, into the deck.

    card.lua:2583 draws the front out of the whole of G.P_CARDS, keyed by
    string and so sorted the way shop_pool.FRONTS is -- the same draw
    Certificate makes, under its own pool name. A Stone card scores no rank
    or suit, but the front is what the hand shows and what orders two Stone
    cards against each other (get_nominal, card.lua:950-955). Always making
    an Ace of Spades showed up on seed TTL5O2HL as a 7C in the game's hand.
    """
    front = game.rng.random_element(shop_pool.FRONTS, "marb_fr")
    suit, rank = front.split("_")
    by_rank = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
               "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT,
               "9": Rank.NINE, "T": Rank.TEN, "J": Rank.JACK,
               "Q": Rank.QUEEN, "K": Rank.KING, "A": Rank.ACE}
    by_suit = {"C": Suit.CLUBS, "D": Suit.DIAMONDS, "H": Suit.HEARTS,
               "S": Suit.SPADES}
    game.add_card(Card(by_rank[rank], by_suit[suit],
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
    game.add_card_to_hand(card)


register("Certificate", Rarity.UNCOMMON,
         "When the round begins, add a random playing card with a random seal "
         "to your hand", cost=6, on_round_start=_certificate)
def _riff_raff(j: JokerInstance, game: "GameState") -> None:
    """Two Common jokers, from Riff-Raff's own streams.

    card.lua:2529-2543 makes them with
    `create_card('Joker', G.jokers, nil, 0, nil, nil, nil, 'rif')`. The 'rif'
    was missing, so the pool drawn was "Joker1<ante>" rather than
    "Joker1rif<ante>": two believable Commons, nearly always the wrong two.
    (The forced rarity of 0 is no roll -- Lua's 0 is truthy and below both
    thresholds -- which Rarity.COMMON already says.)

    And the count is settled before either exists: jokers_to_create is
    min(2, card_limit - (#jokers + joker_buffer)), taken when the blind is
    selected. A Negative first joker raises the limit as it arrives, but one
    free slot has already been turned into one joker.

    Counted against the row as it stands mid-pass: a joker getting sliced
    still takes its slot, and joker_buffer carries what a Dagger handed back
    and what an earlier Riff-raff, or a Blueprint's copy, already promised.
    The jokers themselves arrive in an event, after the pass (card.lua:2532).
    """
    room = game.joker_slots - (len(game.jokers) + game.joker_buffer)
    if room <= 0:
        return
    count = min(2, room)
    game.joker_buffer += count

    def make() -> None:
        # Emplaced without asking (card.lua:2536): the count was the check,
        # and a Dagger's victim may still be sitting in the row.
        for _ in range(count):
            game.add_random_joker("Riff-Raff", Rarity.COMMON, append="rif",
                                  room_checked=True)
        game.joker_buffer = 0

    game.after_setting_blind(make)


register("Riff-Raff", Rarity.COMMON,
         "When Blind is selected, create 2 Common Jokers", cost=6,
         on_blind_select=_riff_raff)
register("8 Ball", Rarity.COMMON,
         "1 in 4 chance for each played 8 to create a Tarot card when scored",
         cost=5,
         # Room first, then the roll (card.lua:3106-3107): a full row spends
         # no draw from '8ball'. Rolling anyway put the stream two draws
         # ahead after a hand of three 8s whose first made a Tarot, and
         # 90WTJQJP missed a High Priestess the game made later. A Tarot made
         # here is added at once, so the length also plays the part of
         # G.GAME.consumeable_buffer for the next 8 in the same hand.
         scored=lambda j, c, ctx: ctx.game.add_consumables(
             ctx.game.random_consumables(ConsumableKind.TAROT, 1, "8ba"))
         if len(ctx.game.consumables) < ctx.game.consumable_slots
         and c.rank is Rank.EIGHT and not c.is_stone
         and _chance(ctx, "8ball", 1, 4) else None)
def _hallucination(j: JokerInstance, game: "GameState") -> None:
    """One in two to make a Tarot whenever a booster pack is opened.

    The odds are drawn against "halu" plus the ante, and the room check comes
    first -- a full row of consumables costs no roll at all.

    The Tarot is built after the pack's own cards (see GameState._open_pack),
    so it is drawn from a pool with the pack's Tarots already blanked.
    """
    if len(game.consumables) >= game.consumable_slots:
        return
    if game.rng.chance("halu%d" % game.ante, game.probability_scale(), 2):
        game.add_consumables(
            game.random_consumables(ConsumableKind.TAROT, 1, "hal"))


register("Hallucination", Rarity.COMMON,
         "1 in 2 chance to create a Tarot card when a Booster Pack is opened",
         cost=4, on_pack_open=_hallucination)
register("Superposition", Rarity.COMMON,
         "Create a Tarot card if the poker hand contains an Ace and a Straight",
         cost=4,
         after_hand=lambda j, ctx: ctx.game.add_consumables(
             ctx.game.random_consumables(ConsumableKind.TAROT, 1, "sup"))
         if CONTAINS_STRAIGHT & ctx.contains
         and any(c.rank is Rank.ACE for c in ctx.scoring) else None)
register('Séance', Rarity.UNCOMMON,
         "If the poker hand contains a Straight Flush, create a Spectral card",
         cost=6,
         # `next(context.poker_hands[...])`, like every other joker that names
         # a hand -- not a test of what the hand *is*. The two agree on any
         # hand vanilla can make, since nothing that outranks a Straight Flush
         # contains one, but reading the top hand is the wrong shape and would
         # be wrong the moment a mod or an unusual joker made one.
         after_hand=lambda j, ctx: ctx.game.add_consumables(
             ctx.game.random_consumables(ConsumableKind.SPECTRAL, 1, "sea"))
         if CONTAINS_STRAIGHT_FLUSH & ctx.contains else None)
register("Vagabond", Rarity.RARE,
         "Create a Tarot card if a hand is played with $4 or less", cost=8,
         after_hand=lambda j, ctx: ctx.game.add_consumables(
             ctx.game.random_consumables(ConsumableKind.TAROT, 1, "vag"))
         if ctx.money <= 4 else None)
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
        game.add_card_to_hand(copy)


register("DNA", Rarity.RARE,
         "If the first hand of a round has only 1 card, add a permanent copy "
         "to the deck and draw it to hand", cost=8, before_hand=_dna)
def _trading_card(j: JokerInstance, cards: list, game: "GameState") -> None:
    if len(cards) == 1:
        # The other place the shatter flag is set before the jokers look.
        game.remove_card(cards[0],
                         shattered=cards[0].enhancement is Enhancement.GLASS)
        game.add_money(3, "Trading Card")


register("Trading Card", Rarity.UNCOMMON,
         "If the first discard of a round has only 1 card, destroy it and "
         "earn $3", cost=6, on_first_discard=_trading_card)
def _burnt(j: JokerInstance, cards: list, game: "GameState") -> None:
    game.hand_levels.level_up(game.evaluate_selection(list(cards)).hand)


register("Burnt Joker", Rarity.RARE,
         "Upgrade the level of the first discarded poker hand each round",
         cost=8, on_first_discard=_burnt)
