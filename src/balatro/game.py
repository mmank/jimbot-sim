"""Run state machine: blinds, playing, cash out, shop, packs.

`GameState.legal_actions()` returns every action available in the current phase,
which doubles as the action mask for the RL environment. `GameState.step()`
applies one. Nothing in here knows about neural networks or observations.
"""

from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass, field
from enum import Enum

from . import consumables as cons
from . import shop as shop_mod
from .blinds import (BOSSES, FINISHER_BOSSES, Blind, BlindKind,
                     BossEffect, make_blind)
from .cards import Card, Edition, Enhancement, Rank, Seal, Suit, standard_deck
from .deck_data import DECK_DATA
from .boss_data import BOSS_DATA, eligible_bosses
from . import shop_pool

# poll_edition speaks the game's names for these.
_BOSS_BY_NAME = {b.name: b for b in BOSSES + FINISHER_BOSSES}

_EDITION_BY_NAME = {"none": Edition.NONE, "foil": Edition.FOIL,
                    "holo": Edition.HOLOGRAPHIC,
                    "polychrome": Edition.POLYCHROME,
                    "negative": Edition.NEGATIVE}

# The game names a playing card by its centre key, C_2 through S_T, and pack
# contents come back in that vocabulary.
_RANK_BY_CODE = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR,
                 "5": Rank.FIVE, "6": Rank.SIX, "7": Rank.SEVEN,
                 "8": Rank.EIGHT, "9": Rank.NINE, "T": Rank.TEN,
                 "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
                 "A": Rank.ACE}
_SUIT_BY_CODE = {"C": Suit.CLUBS, "D": Suit.DIAMONDS, "H": Suit.HEARTS,
                 "S": Suit.SPADES}
from .consumables import ConsumableKind, ConsumableSpec
from .hands import PLANET_FOR_HAND, HandLevels, HandType, evaluate
from .jokers import REGISTRY as JOKER_REGISTRY, JokerInstance, Rarity

# The game's rarity numbers, which its pools are keyed by.
_RARITY_INDEX = {Rarity.COMMON: 1, Rarity.UNCOMMON: 2, Rarity.RARE: 3,
                 Rarity.LEGENDARY: 4}
from .rng import RunRng
from .scoring import score_hand, shattered_glass
from .shop import PackKind, PackSpec, Shop, ShopSlot, Voucher

MAX_PLAYED = 5
BASE_JOKER_SLOTS = 5
BASE_CONSUMABLE_SLOTS = 2
BASE_HAND_SIZE = 8
BASE_HANDS = 4
BASE_DISCARDS = 3
BASE_INTEREST_CAP = 5
# G.GAME.perishable_rounds and G.GAME.rental_rate.
PERISHABLE_ROUNDS = 5
RENTAL_RATE = 3
WIN_ANTE = 8


class Phase(Enum):
    BLIND_SELECT = "blind_select"
    PLAYING = "playing"
    # Between beating a blind and entering the shop the game sits on the
    # cash-out screen, with the payout shown but not yet paid and the deck
    # already restored. Collapsing that into the winning hand made the
    # simulator richer than the engine and its deck shorter, at the same
    # instant, for the whole of that gap.
    ROUND_EVAL = "round_eval"
    SHOP = "shop"
    PACK = "pack"
    GAME_OVER = "game_over"
    WON = "won"


class ActionType(Enum):
    CASH_OUT = "cash_out"
    SELECT_BLIND = "select_blind"
    SKIP_BLIND = "skip_blind"
    PLAY = "play"
    DISCARD = "discard"
    USE_CONSUMABLE = "use_consumable"
    SELL_JOKER = "sell_joker"
    SELL_CONSUMABLE = "sell_consumable"
    BUY = "buy"
    BUY_AND_USE = "buy_and_use"
    BUY_VOUCHER = "buy_voucher"
    REROLL = "reroll"
    BUY_PACK = "buy_pack"
    PICK_PACK = "pick_pack"
    SKIP_PACK = "skip_pack"
    LEAVE_SHOP = "leave_shop"


@dataclass(frozen=True)
class Action:
    type: ActionType
    index: int = -1
    cards: tuple[int, ...] = ()

    def __repr__(self) -> str:
        bits = [self.type.value]
        if self.index >= 0:
            bits.append(f"#{self.index}")
        if self.cards:
            bits.append(str(list(self.cards)))
        return " ".join(bits)


class Tag(Enum):
    """Reward for skipping a blind; applied when the next shop opens."""

    UNCOMMON = "Uncommon Tag"
    RARE = "Rare Tag"
    CHARM = "Charm Tag"
    METEOR = "Meteor Tag"
    BUFFOON = "Buffoon Tag"
    BOSS = "Boss Tag"
    ETHEREAL = "Ethereal Tag"
    STANDARD = "Standard Tag"
    FOIL = "Foil Tag"
    HOLOGRAPHIC = "Holographic Tag"
    POLYCHROME = "Polychrome Tag"
    NEGATIVE = "Negative Tag"
    COUPON = "Coupon Tag"
    INVESTMENT = "Investment Tag"
    ECONOMY = "Economy Tag"
    JUGGLE = "Juggle Tag"


TAG_POOL = list(Tag)

# The game's key for each tag this simulator knows how to apply. The pool has
# twenty-four and these are the eight with an effect here, so a skip can hand
# over a tag that does nothing yet -- which is the truth, and better than
# rolling from eight and handing over one the run was never offered. The gap
# is visible rather than hidden: TAG_BY_KEY.get returns None and nothing
# happens.
TAG_BY_KEY = {
    "tag_uncommon": Tag.UNCOMMON,
    "tag_rare": Tag.RARE,
    "tag_charm": Tag.CHARM,
    "tag_meteor": Tag.METEOR,
    "tag_buffoon": Tag.BUFFOON,
    "tag_boss": Tag.BOSS,
    "tag_ethereal": Tag.ETHEREAL,
    "tag_standard": Tag.STANDARD,
    "tag_foil": Tag.FOIL,
    "tag_holo": Tag.HOLOGRAPHIC,
    "tag_polychrome": Tag.POLYCHROME,
    "tag_negative": Tag.NEGATIVE,
    "tag_coupon": Tag.COUPON,
    "tag_investment": Tag.INVESTMENT,
    "tag_economy": Tag.ECONOMY,
    "tag_juggle": Tag.JUGGLE,
}


@dataclass
class GameState:
    seed: int = 0
    # The back the run is played with. Not decoration: Red Deck grants an
    # extra discard every round, Blue an extra hand, Black a joker slot at the
    # cost of a hand. A run that ignores the deck is a different run.
    deck: str = "Red Deck"
    rng: RunRng = field(init=False)

    ante: int = 1
    blind_index: int = 0          # 0 small, 1 big, 2 boss
    round_number: int = 0
    money: int = 4

    full_deck: list[Card] = field(default_factory=list)
    draw_pile: list[Card] = field(default_factory=list)
    hand: list[Card] = field(default_factory=list)
    discard_pile: list[Card] = field(default_factory=list)

    jokers: list[JokerInstance] = field(default_factory=list)
    consumables: list[ConsumableSpec] = field(default_factory=list)
    vouchers: list[Voucher] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    ante_tags: list = field(default_factory=list)   # skip rewards [small, big]
    # The same two as the game's keys, kept because a tag this simulator has
    # no effect for is still the tag the run was offered.
    ante_tag_keys: list = field(default_factory=list)

    hand_levels: HandLevels = field(default_factory=HandLevels.new)

    base_hand_size: int = BASE_HAND_SIZE
    base_joker_slots: int = BASE_JOKER_SLOTS
    # The Nebula Deck takes a consumable slot away, the Painted Deck a joker
    # slot; both are set once from the deck rather than derived.
    extra_consumable_slots: int = 0

    blind: Blind | None = None
    # Held between beating a blind and cashing out: the game shows the payout
    # on that screen before any of it is paid.
    beaten_blind: Blind | None = None
    pending_payout: int = 0
    beaten_was_boss: bool = False
    # Which sort the player last asked for. The game keeps this on the hand's
    # CardArea and reapplies it to every draw.
    hand_sort: str = "rank"
    # The poker hand most recently played, which is what the game shows and
    # what a recording carries. Cheap to keep and the fastest way to see that
    # two engines played different cards from the same choice.
    last_hand: str = ""
    hands_played: int = 0          # total for the run, as G.GAME.hands_played
    # Run totals that jokers scale on. The game keeps these on G.GAME, and a
    # joker that counts them scores zero without them -- which looks like
    # agreement in any test where both sides are at zero.
    # Targets the game rerolls each round, which the jokers that name a card
    # or a suit read. Kept on the run because that is where the game keeps
    # them -- two Idols name the same card.
    idol_rank: object = None
    idol_suit: object = None
    ancient_suit: object = None
    todo_hand: object = None
    mail_rank: object = None
    castle_suit: object = None

    discards_used: int = 0    # this round, for Delayed Gratification
    # Every centre the run has already produced. The game keeps this as
    # G.GAME.used_jokers and blanks those entries from the pools, so a shop
    # that ignores it offers repeats the real game never would.
    # How often each boss has been drawn. The game narrows the eligible set to
    # the least-used before rolling, so this is part of the selection rather
    # than bookkeeping.
    bosses_used: dict = field(default_factory=dict)
    tarots_used: int = 0
    planets_used: int = 0
    unique_planets: set = field(default_factory=set)
    rerolls: int = 0
    blinds_skipped: int = 0
    cards_sold: int = 0
    glass_destroyed: int = 0
    lucky_triggers: int = 0
    chips_scored: int = 0
    hands_left: int = 0
    discards_left: int = 0
    hands_played_this_round: set[HandType] = field(default_factory=set)
    played_this_ante: set[int] = field(default_factory=set)

    phase: Phase = Phase.BLIND_SELECT
    shop: Shop | None = None
    pack: PackSpec | None = None
    # The game gives every run a Buffoon pack in its first shop, short-
    # circuiting before any roll -- see get_pack. This remembers whether that
    # has happened, which is G.GAME.first_shop_buffoon.
    first_shop_buffoon: bool = False
    # The voucher this round's shop will offer, drawn when the round starts.
    round_voucher: str = ""
    # Whether the hand on screen was dealt by a pack rather than by a round,
    # which decides whether closing the pack takes it away again.
    _pack_dealt_hand: bool = False
    # G.GAME.shop_free -- the Coupon Tag, which lasts for the one shop.
    shop_free: bool = False
    # G.GAME.last_tarot_planet -- the key The Fool copies.
    last_tarot_planet: str = ""
    # round_resets.blind_choices.Boss: this ante's boss, drawn at its start.
    ante_boss: str = ""
    # The stake, one to eight. It is not a difficulty label: it changes the
    # chips every ante asks for, the discards a round starts with, whether
    # the Small Blind pays, and what stickers the shop puts on its jokers.
    stake: int = 1
    pack_options: list = field(default_factory=list)
    pack_picks_left: int = 0

    logs: list[str] = field(default_factory=list)
    verbose: bool = False
    _satisfiable_cache: tuple | None = field(default=None, repr=False)

    @property
    def blind_target(self) -> int:
        """The chips required *right now*, which is zero outside a round.

        `blind` holds the next blind through the select screen and the shop so
        that it can be offered and skipped, but it is not in force until the
        round starts -- the game reports no target until then, and reading the
        pending one as active made the simulator look like it was mid-round
        while sitting in a shop.
        """
        if self.blind is None:
            return 0
        # A run that ends does so *during* a blind, and the game still reports
        # that blind's target on the game-over screen.
        if self.phase not in (Phase.PLAYING, Phase.GAME_OVER):
            return 0
        return self.blind.target

    @property
    def blind_name(self) -> str:
        """The blind in force, which is nothing outside a round.

        Same rule as blind_target: `blind` holds the next one through the
        select screen and the shop so it can be offered, but the game names no
        blind until one is actually being played.
        """
        if self.blind is None:
            return ""
        if self.phase not in (Phase.PLAYING, Phase.GAME_OVER):
            return ""
        return self.blind.name

    @property
    def deck_config(self) -> dict:
        return DECK_DATA.get(self.deck, ("", {}))[1]

    def __post_init__(self) -> None:
        self.rng = RunRng(self.seed)
        config = self.deck_config
        self.money += config.get("dollars", 0)
        if not self.full_deck:
            self.full_deck = standard_deck()
        self._apply_deck_config(config)
        # The order the game starts a run in: the boss, then the voucher, then
        # the two skip tags. Every one of them draws, so the order is part of
        # the seed.
        self._roll_boss()
        self._roll_voucher()
        self._roll_ante_tags()
        self._reset_round_cards()
        self._next_blind()

    # ------------------------------------------------------------------
    # small helpers used by joker and consumable hooks
    # ------------------------------------------------------------------

    def log(self, message: str) -> None:
        self.logs.append(message)
        if self.verbose:
            print(message)

    def add_money(self, amount: int, source: str = "") -> None:
        self.money += amount
        if amount and source:
            self.log(f"{source}: {'+' if amount >= 0 else '-'}${abs(amount)}")

    def destroy_joker(self, joker: JokerInstance, reason: str = "") -> None:
        """Remove a joker, unless it is eternal.

        Nothing removes an eternal joker: not selling it, not Hex, not Ankh,
        not Madness, not going extinct. The game checks at each call site and
        the checks are easy to miss one of, so the gate is here instead --
        anything that wants a joker gone has to come through this.
        """
        if joker.eternal:
            return
        if joker in self.jokers:
            self.jokers.remove(joker)
            self.log(f"{joker.name} destroyed{f' ({reason})' if reason else ''}")

    def remove_card(self, card: Card) -> None:
        for pile in (self.full_deck, self.draw_pile, self.hand, self.discard_pile):
            if card in pile:
                pile.remove(card)

    def add_card(self, card: Card) -> None:
        # CardArea:emplace puts a card at the *front* of a deck, which is its
        # bottom -- drawing takes from the back. A card added mid-round is
        # therefore the last one you will see, not the next.
        self.full_deck.append(card)
        self.draw_pile.insert(0, card)
        for joker in self.jokers:
            if joker.name == "Hologram":
                joker.counter += 0.25

    def random_face_card(self) -> Card:
        rank = self.rng.choice("face_card", [Rank.JACK, Rank.QUEEN, Rank.KING])
        return Card(rank, self.rng.choice("face_suit", list(Suit)))

    def random_consumables(self, kind: ConsumableKind, count: int,
                           append: str = "") -> list[ConsumableSpec]:
        """Consumables from the game's pool, under the creator's own name.

        This drew uniformly from every card of the kind under a name of its
        own, which is wrong the same way the packs were: it ignores what the
        run has already seen and what a Planet is gated on, and it draws from
        a stream the game does not have. `append` is the key_append of
        whatever is creating the card -- "8ba" for a purple seal, "emp" for
        The Emperor, "pri" for The High Priestess -- and each is a separate
        stream.
        """
        card_set = {ConsumableKind.TAROT: "Tarot",
                    ConsumableKind.PLANET: "Planet",
                    ConsumableKind.SPECTRAL: "Spectral"}[kind]
        played = [h.label for h, n in self.hand_levels.plays.items() if n > 0]
        showman = any(j.name == "Showman" for j in self.jokers)
        # The game tests for a free slot *before* it creates the card, so a
        # full row of consumables costs nothing at all. Drawing and then
        # dropping the card, which is what this did, spends a roll the game
        # never spends and puts every later draw from that pool one place
        # along -- which is how a purple seal handed over Strength where the
        # run was given the Wheel of Fortune.
        room = self.consumable_slots - len(self.consumables)
        out = []
        for _ in range(max(0, min(count, room))):
            key = shop_pool.draw_consumable(
                self.rng, card_set, self.ante, played_hands=played,
                seen=self.seen_centers, showman=showman, append=append)
            out.append(cons.REGISTRY[shop_pool.NAME_BY_CONSUMABLE_KEY[key]])
        return out

    def add_consumables(self, specs: list[ConsumableSpec]) -> None:
        for spec in specs:
            if len(self.consumables) < self.consumable_slots:
                self.consumables.append(spec)

    def add_random_joker(self, source: str = "", rarity: Rarity | None = None,
                         legendary: bool = False, append: str = "") -> None:
        """A joker from the game's own pool, not from a list of every joker.

        `append` is the key_append the thing creating it uses -- "jud" for
        Judgement, "sou" for The Soul, "wra" for Wraith -- and it names the
        stream, so getting it wrong draws the right joker from the wrong
        place. A forced rarity skips the rarity roll entirely, which is how
        Wraith is always rare and never legendary.
        """
        if len(self.jokers) >= self.joker_slots:
            return
        key = shop_pool.draw_joker(
            self.rng, self.ante, seen_jokers=self.seen_centers,
            rarity=4 if legendary else _RARITY_INDEX.get(rarity),
            append=append,
            owned_enhancements={"m_%s" % c.enhancement.value
                                for c in self.full_deck})
        spec = JOKER_REGISTRY[shop_pool.NAME_BY_JOKER_KEY[key]]
        self.jokers.append(JokerInstance(spec))
        self.log(f"{source}: gained {spec.name}")

    def refuses_use(self, spec: ConsumableSpec) -> bool:
        """Card:check_use -- the one card the game refuses at the last moment.

        Ankh is the only entry in it, and it disagrees with the check that
        enables the button: can_use_consumeable asks only for a joker and a
        limit above one, while this asks for a *free slot*. So with a full
        row the button is live, you press it, and the game says No Room.
        """
        return (spec.name == "Ankh"
                and len(self.jokers) >= self.joker_slots)

    def buy_and_use(self, index: int) -> None:
        """The shop's buy-and-use button, quirk included.

        The game charges for the card, takes it out of the shop, and -- on
        this path only -- never files it anywhere: the line that would put it
        in a consumable slot is guarded against buy_and_use, because it is
        about to be used and destroyed. If the use is then refused, the card
        has been paid for and belongs to no card area at all. It is gone.

        That is a bug in the game rather than a rule, but it is a bug you can
        hit -- buy-and-use an Ankh with a full joker row and you are out the
        money and the card -- so the simulator has to lose it too.
        """
        assert self.shop is not None
        slot = self.shop.slots.pop(index)
        self.add_money(-slot.price, f"bought {slot.label}")
        spec = slot.consumable
        if spec is None:                     # a joker: buy-and-use is a buy
            if slot.joker is not None:
                self.jokers.append(slot.joker)
            return
        if self.refuses_use(spec):
            self.log(f"{spec.name}: No Room -- bought, used by nothing, lost")
            return
        self.use_consumable(spec, [])

    def use_consumable(self, spec: ConsumableSpec,
                       targets: list[Card] | None = None) -> None:
        """Apply a consumable and remember it if it was a Tarot or a Planet.

        The remembering is what The Fool reads, and it was never written --
        the field existed and nothing ever set it, so The Fool copied nothing
        for the whole of a run. The game records it after the effect has run,
        which is why using The Fool leaves The Fool as the last one used and
        the game refuses to let you use it twice in a row.
        """
        if spec.apply is not None:
            spec.apply(self, list(targets or []))
        self.log(f"Used {spec.name}")
        if spec.kind in (ConsumableKind.TAROT, ConsumableKind.PLANET):
            self.last_tarot_planet = shop_pool.KEY_BY_CONSUMABLE_NAME.get(
                spec.name, "")

    def add_joker_copy(self, joker: JokerInstance, source: str = "") -> None:
        """A copy of a joker already held, editions and all."""
        if len(self.jokers) >= self.joker_slots:
            return
        self.jokers.append(copy.deepcopy(joker))
        self.log(f"{source}: copied {joker.name}")

    # ------------------------------------------------------------------
    # derived state
    # ------------------------------------------------------------------

    @property
    def boss(self) -> BossEffect | None:
        if self.blind is None or self.blind.kind is not BlindKind.BOSS:
            return None
        if self.blind.disabled:
            return None
        if any(j.name == "Chicot" for j in self.jokers):
            return None
        return self.blind.boss

    def disable_blind(self, source: str) -> None:
        """Turn the boss off mid-round, the way Blind:disable does it.

        Not simply a flag. The game undoes each boss by hand, and the undo is
        not always the mirror image of the effect: a blind that was made
        larger is divided back down to the ordinary boss size, a hand or a
        discard the blind took away is handed back to the counter that is
        already running, and The Manacle -- which took a card out of the hand
        when the blind began -- gives back two.

        That last one looks like a mistake in the game and is worth spelling
        out, because it is the sort of thing a simulator written from the
        rules would never produce. Blind:disable calls change_size(1), which
        raises the limit and deals a card into the space it just made, and
        then calls draw_from_deck_to_hand(1) as well. The hand ends the
        transaction one card *over* its own limit. A recording of a real run
        shows exactly that: limit eight, nine cards in hand.
        """
        blind = self.blind
        if blind is None or blind.boss is None or blind.disabled:
            return
        boss = blind.boss
        blind.disabled = True

        # A blind made larger goes back to the ordinary boss size rather than
        # to no boss at all: the game divides, so The Wall's four times
        # becomes two and Violet Vessel's six becomes two.
        if boss.chip_mult != 2.0 and boss.chip_mult:
            blind.target = int(blind.target * 2.0 / boss.chip_mult)

        # Hands and discards go back on the live counters, not on the next
        # round's allowance -- the round is still running.
        hands, discards = self._round_allowance()
        if boss.hands_delta:
            self.hands_left = max(self.hands_left, hands)
        if boss.discards_delta:
            self.discards_left = max(self.discards_left, discards)

        if boss.hand_size_delta < 0:
            self._draw_to_hand_size()
            self._draw_cards(-boss.hand_size_delta)

        self.log(f"{source}: {boss.name} is disabled")

    @property
    def joker_slots(self) -> int:
        """How many jokers the row holds.

        A negative joker does not take a slot -- add_to_deck raises the limit
        by one for it and remove_from_deck lowers it again -- so a row of five
        with a negative among them has room for a sixth. The simulator had no
        idea, so a Judgement that should have made a joker made nothing, and
        the run went on a joker short.
        """
        return (self.base_joker_slots
                + sum(1 for j in self.jokers if j.edition is Edition.NEGATIVE)
                + sum(v.joker_slots for v in self.vouchers))

    @property
    def active_jokers(self) -> list:
        """The jokers that still do anything.

        A perishable joker is switched off once its five rounds are up -- the
        game debuffs it, which leaves it sitting in the row taking a slot and
        contributing nothing. Reading `jokers` for effects therefore keeps a
        dead joker working: a debuffed Stuntman went on taking two off the
        hand size, so the run dealt seven cards where the game dealt nine.
        """
        return [j for j in self.jokers if not j.debuffed]

    @property
    def seen_centers(self) -> set:
        """The centres that currently exist, which is what blanks a pool.

        This was a set that only ever grew -- every card the run had ever
        drawn -- and that is not what the game keeps. G.GAME.used_jokers is
        set when a card is built and *cleared* when the last card of that name
        is removed, so it is closer to an inventory than a history: the shop's
        current cards count, the shop before the reroll does not, and a Tarot
        the player used is available again immediately.

        The difference is not small. Twenty-eight actions into a real run the
        engine had three entries where the simulator had twenty, so the
        simulator was drawing from a pool with seventeen cards wrongly blanked
        -- which is how a purple seal handed over Strength where the run was
        given the Wheel of Fortune.
        """
        joker_key = shop_pool.KEY_BY_JOKER_NAME.get
        cons_key = shop_pool.KEY_BY_CONSUMABLE_NAME.get
        keys = {joker_key(j.name) for j in self.jokers}
        keys |= {cons_key(c.name) for c in self.consumables}
        if self.shop is not None:
            for slot in self.shop.slots:
                if slot.joker is not None:
                    keys.add(joker_key(slot.joker.name))
                elif slot.consumable is not None:
                    keys.add(cons_key(slot.consumable.name))
        for option in self.pack_options:
            name = getattr(option, "name", None)
            keys.add(joker_key(name) or cons_key(name))
        keys.discard(None)
        return keys

    @property
    def sticker_rules(self) -> dict:
        """Which stickers the stake lets the shop put on a joker."""
        return {"eternals": self.stake >= 4, "perishables": self.stake >= 7,
                "rentals": self.stake >= 8}

    def _apply_stickers(self, joker: JokerInstance, price: int,
                        in_pack: bool = False) -> int:
        """Poll a shop joker's stickers and return what it now costs.

        The first poll happens whether or not any sticker is enabled, so it
        is made on every stake -- see shop_pool.poll_stickers. A rental costs
        a dollar however expensive the joker is, which is six dollars a
        recording said the run still had.
        """
        stickers = shop_pool.poll_stickers(self.rng, self.ante, in_pack,
                                           **self.sticker_rules)
        joker.eternal = stickers["eternal"]
        joker.perishable = stickers["perishable"]
        joker.rental = stickers["rental"]
        if joker.perishable:
            joker.perish_tally = PERISHABLE_ROUNDS
        return 1 if joker.rental else price

    @property
    def blind_scaling(self) -> int:
        """G.GAME.modifiers.scaling: 1, 2 from Green stake, 3 from Purple."""
        return 3 if self.stake >= 6 else 2 if self.stake >= 3 else 1

    @property
    def edition_rate(self) -> float:
        """G.GAME.edition_rate, which Hone and Glow Up raise."""
        rates = [v.edition_rate for v in self.vouchers if v.edition_rate]
        return max(rates) if rates else 1.0

    @property
    def consumable_slots(self) -> int:
        return (BASE_CONSUMABLE_SLOTS + self.extra_consumable_slots
                + sum(v.consumable_slots for v in self.vouchers))

    @property
    def hand_size(self) -> int:
        size = self.base_hand_size
        size += sum(v.hand_size for v in self.vouchers)
        size += sum(j.spec.hand_size for j in self.active_jokers)
        size += self.deck_config.get("hand_size", 0)
        if self.boss is not None:
            size += self.boss.hand_size_delta
        return max(1, size)

    @property
    def interest_cap(self) -> int:
        caps = [v.interest_cap for v in self.vouchers if v.interest_cap]
        return max(caps) if caps else BASE_INTEREST_CAP

    @property
    def price_multiplier(self) -> float:
        mult = 1.0
        for voucher in self.vouchers:
            mult *= voucher.price_multiplier
        return mult

    def price(self, base: int) -> int:
        return max(1, round(base * self.price_multiplier))

    @property
    def is_over(self) -> bool:
        return self.phase in (Phase.GAME_OVER, Phase.WON)

    # ------------------------------------------------------------------
    # blind flow
    # ------------------------------------------------------------------

    def _reset_round_cards(self) -> None:
        """Re-roll the card and the suits that some jokers name.

        Four of these, all declared on the run and none of them ever set, so
        The Idol, Ancient Joker, Mail-In Rebate and Castle scored nothing at
        all. That is the quiet kind of wrong: the joker is registered, the
        hook runs, the condition is never true, and every test where both
        sides score zero agrees.

        The game rolls them from G.playing_cards -- the whole deck, not the
        hand -- sorted by sort_id, skipping Stone cards, once at run start and
        again at the end of every round. Ancient Joker is the odd one: it
        draws a suit from the three it is *not* already on, so it never
        repeats itself.
        """
        pool = sorted((c for c in self.full_deck
                       if c.enhancement is not Enhancement.STONE),
                      key=lambda card: card.uid)
        if pool:
            idol = self.rng.random_element(pool, "idol%d" % self.ante)
            self.idol_rank, self.idol_suit = idol.rank, idol.suit
            self.mail_rank = self.rng.random_element(
                pool, "mail%d" % self.ante).rank
            self.castle_suit = self.rng.random_element(
                pool, "cas%d" % self.ante).suit

        suits = [s for s in (Suit.SPADES, Suit.HEARTS, Suit.CLUBS,
                             Suit.DIAMONDS) if s is not self.ancient_suit]
        self.ancient_suit = self.rng.random_element(suits, "anc%d" % self.ante)

    def _apply_deck_config(self, config: dict) -> None:
        """What the chosen deck starts the run holding.

        The deck's numbers were being read where they were needed -- hand
        size, ante scaling, the spectral rate -- but the things it *gives* a
        run were not applied at all. The Ghost Deck starts with a Hex, the
        Magic Deck with two Fools and Crystal Ball already redeemed, the
        Nebula Deck with Telescope. Missing the cards is worse than missing
        the effect: every consumable slot after the first is numbered one
        place out, so "use the first consumable" uses the wrong card for the
        rest of the run.
        """
        for key in config.get("consumables", ()):
            name = shop_pool.NAME_BY_CONSUMABLE_KEY.get(key)
            if name in cons.REGISTRY:
                self.consumables.append(cons.REGISTRY[name])

        starting = list(config.get("vouchers", ()))
        if config.get("voucher"):
            starting.append(config["voucher"])
        for key in starting:
            voucher = shop_mod.VOUCHER_BY_KEY.get(key)
            if voucher is not None:
                self.vouchers.append(voucher)

        self.base_joker_slots += config.get("joker_slot", 0)
        self.extra_consumable_slots += config.get("consumable_slot", 0)

        # A few decks change the cards themselves rather than the numbers,
        # and the game does it by walking the deck it has just built rather
        # than by building a different one -- so a card keeps its place, and
        # its id, and changes suit where it stands.
        if self.deck == "Checkered Deck":
            for card in self.full_deck:
                if card.suit is Suit.CLUBS:
                    card.suit = Suit.SPADES
                elif card.suit is Suit.DIAMONDS:
                    card.suit = Suit.HEARTS

    def _roll_ante_tags(self) -> None:
        """Both skip rewards for the ante, rolled together.

        The game shows them both on the blind select screen -- deciding
        whether to skip the Small Blind means knowing what skipping the Big
        one would pay -- so both are drawn at once, at run start and again
        when a boss falls.
        """
        self.ante_tag_keys = [shop_pool.draw_tag(self.rng, self.ante)
                              for _ in range(2)]
        self.ante_tags = [TAG_BY_KEY.get(k) for k in self.ante_tag_keys]

    def _roll_voucher(self) -> None:
        """The voucher every shop this ante will offer.

        Once per ante, not once per shop: the game rolls it as the boss falls
        and all three shops of the next ante show the same one. Rolling per
        shop gave a run three vouchers an ante and put every later voucher
        draw in the wrong place.
        """
        self.round_voucher = shop_pool.draw_voucher(
            self.rng, self.ante, redeemed=[v.key for v in self.vouchers])

    def _next_blind(self) -> None:
        kind = [BlindKind.SMALL, BlindKind.BIG, BlindKind.BOSS][self.blind_index]
        boss = self._pick_boss() if kind is BlindKind.BOSS else None
        self.blind = make_blind(
            kind, self.ante, boss,
            ante_scaling=self.deck_config.get("ante_scaling", 1),
            scaling=self.blind_scaling,
            no_reward=(kind is BlindKind.SMALL and self.stake >= 2))
        # The counters, not the cards. A recording's first snapshot reads
        # hands_left 4 and discards_left 3 with hand_size 0 and an empty hand
        # -- nothing is dealt until the blind is taken, but the allowance is
        # already on the HUD. Waiting until the round starts left the
        # simulator reporting nought of each against a screen showing both.
        self.hands_left, self.discards_left = self._round_allowance(False)
        self.phase = Phase.BLIND_SELECT
        self._apply_blind_select_tags()

    @property
    def offered_tag(self) -> Tag | None:
        """The tag skipping the current blind would award, if it is skippable."""
        if self.blind is None or self.blind.kind is BlindKind.BOSS:
            return None
        return self.ante_tags[self.blind_index]

    def _start_round(self) -> None:
        assert self.blind is not None
        self.round_number += 1
        self.chips_scored = 0
        self.discards_used = 0
        self.hands_played_this_round = set()
        # Selecting the blind is its own moment in the game, before any card
        # is dealt: Marble Joker's Stone card is in the deck for the first
        # draw, and Riff-Raff's Jokers are there for the first hand.
        for joker in list(self.jokers):
            if joker.spec.on_blind_select is not None:
                joker.spec.on_blind_select(joker, self)
        self.hands_left, self.discards_left = self._round_allowance()

        self.draw_pile = list(self.full_deck)
        # The game shuffles with pseudoseed("nr" .. ante) at the start of a
        # round, and draws from the end of the result. The pool is named by
        # the ante, not the round, so the two blinds of an ante draw from the
        # same stream at different points in it.
        self.rng.shuffle(self.draw_pile, f"nr{self.ante}")
        self.hand = []
        self.discard_pile = []
        self._apply_debuffs()
        self._draw_to_hand_size()

        # After the deal, not before. The game fires these on
        # `first_hand_drawn`, so Certificate's card lands on top of a hand
        # that is already full and the round starts one card over the limit.
        # Running them first put the card in a hand that was then thrown away
        # and dealt again.
        for joker in list(self.jokers):
            if joker.spec.on_round_start is not None:
                joker.spec.on_round_start(joker, self)

        self.phase = Phase.PLAYING
        self.log(f"--- Ante {self.ante} {self.blind.name}: need {self.blind.target} ---")

    def _apply_debuffs(self) -> None:
        boss = self.boss
        for card in self.full_deck:
            card.debuffed = False
        if boss is None:
            return
        for card in self.full_deck:
            if boss.debuff_suit is not None and card.suit is boss.debuff_suit:
                card.debuffed = True
            if boss.debuff_face and card.rank.is_face:
                card.debuffed = True
            if boss.debuff_previously_played and card.uid in self.played_this_ante:
                card.debuffed = True

    def _roll_boss(self) -> None:
        """Draw the boss for this ante and hold on to it.

        The game decides it at the start of the ante and keeps it in
        round_resets.blind_choices.Boss, which is what makes a Boss Tag able
        to re-roll it -- there is something to replace. Drawing it lazily when
        the boss blind comes up, which is what this did, left nothing for the
        tag to act on and made the run one draw short on that pool.

        The draw itself is the game's: eligible bosses, narrowed to the ones
        used least, then a roll. Picking uniformly from every boss gives a run
        that meets the same boss twice while others go unseen.
        """
        pool = eligible_bosses(self.ante, self.bosses_used)
        if not pool:
            self.ante_boss = ""
            return
        key = self.rng.random_element(pool, "boss")
        self.bosses_used[key] = self.bosses_used.get(key, 0) + 1
        self.ante_boss = key

    def _pick_boss(self):
        if not self.ante_boss:
            return None
        return _BOSS_BY_NAME.get(BOSS_DATA[self.ante_boss][0])

    def _round_allowance(self, with_boss: bool = True) -> tuple[int, int]:
        """Hands and discards for a round, from the deck, vouchers and boss.

        The boss is optional because the counters exist before it applies. On
        the blind select screen the game shows the plain allowance -- the
        boss's effect lands in set_blind, when the blind is actually taken --
        so a Water Blind on offer still reads three discards there and zero
        the moment it is selected.
        """
        config = self.deck_config
        hands = BASE_HANDS + sum(v.extra_hands for v in self.vouchers)
        hands += sum(j.spec.extra_hands for j in self.active_jokers)
        hands += config.get("hands", 0)
        # Blue stake and up start a round with one discard fewer.
        discards = BASE_DISCARDS - (1 if self.stake >= 5 else 0)
        discards += sum(v.extra_discards for v in self.vouchers)
        discards += sum(j.spec.extra_discards for j in self.active_jokers)
        discards += config.get("discards", 0)
        boss = self.boss if with_boss else None
        if boss is not None:
            hands = (max(1, hands + boss.hands_delta)
                     if boss.hands_delta > -50 else 1)
            discards = (max(0, discards + boss.discards_delta)
                        if boss.discards_delta > -50 else 0)
        return hands, discards

    def _sort_hand(self) -> None:
        """Keep the hand in the order the game shows it.

        G.hand is sorted "desc" by get_nominal, and every action addresses
        cards by position, so an unsorted hand turns the same choice into a
        different play.
        """
        key = (Card.suit_sort_value.fget if self.hand_sort == "suit"
               else Card.sort_value.fget)
        self.hand.sort(key=key, reverse=True)

    def sort_hand(self, by: str = "rank") -> None:
        """Reorder the hand the way the sort buttons do, and keep doing it.

        Not cosmetic: actions address cards by position, so sorting changes
        what every later choice means. Treating these as no-ops because "the
        hand is already sorted" was wrong -- by suit is a different order.

        The choice sticks, as it does in the game: CardArea keeps the method
        on itself and applies it to every later draw, so a hand sorted by suit
        stays that way as cards come in. Sorting once and letting the next
        draw undo it puts the cards back in rank order behind the player's
        back.
        """
        self.hand_sort = "suit" if by == "suit" else "rank"
        self._sort_hand()

    def _draw_cards(self, count: int) -> None:
        """Deal `count` cards regardless of the hand limit.

        The game has draw_from_deck_to_hand, which takes a number and does not
        consult the limit -- which is how a disabled Manacle leaves the hand
        one card over it.
        """
        for _ in range(count):
            if not self.draw_pile:
                return
            self.hand.append(self.draw_pile.pop())
        self._sort_hand()

    def _draw_to_hand_size(self) -> None:
        while len(self.hand) < self.hand_size and self.draw_pile:
            self.hand.append(self.draw_pile.pop())
        self._sort_hand()
        if not self.hand and self.phase is Phase.PLAYING:
            # Deck exhausted mid-blind: nothing left to play with.
            self.phase = Phase.GAME_OVER
            self.log("Ran out of cards")

    # ------------------------------------------------------------------
    # playing
    # ------------------------------------------------------------------

    def _four_fingers(self) -> bool:
        return any(j.name == "Four Fingers" for j in self.jokers)

    def _splash(self) -> bool:
        return any(j.name == "Splash" for j in self.jokers)

    def has_pareidolia(self) -> bool:
        """Every card counts as a face card."""
        return any(j.name == "Pareidolia" for j in self.jokers)

    def has_smeared(self) -> bool:
        """Hearts count as Diamonds and Spades as Clubs, both ways."""
        return any(j.name == "Smeared Joker" for j in self.jokers)

    def probability_scale(self) -> int:
        """Oops! All 6s doubles every listed probability, and stacks."""
        return 2 ** sum(1 for j in self.jokers if j.name == "Oops! All 6s")

    def _shortcut(self) -> bool:
        return any(j.name == "Shortcut" for j in self.jokers)

    def evaluate_selection(self, cards: list[Card]):
        return evaluate(cards, splash=self._splash(),
                        smeared=self.has_smeared(),
                        four_fingers=self._four_fingers(),
                        shortcut=self._shortcut())

    def preview_score(self, indices: tuple[int, ...]) -> int:
        """Score a candidate play without advancing the run.

        Joker counters and card enhancements that scoring would mutate are
        snapshotted and restored, and a throwaway RNG stands in so previewing
        does not consume the run's random stream.
        """
        played = [self.hand[i] for i in indices]
        held = [c for i, c in enumerate(self.hand) if i not in indices]
        real_jokers, real_rng = self.jokers, self.rng
        enhancements = [(c, c.enhancement) for c in played]
        self.jokers = [copy.copy(j) for j in real_jokers]
        # A throwaway generator, named off the run's own seed. This used to
        # xor the seed with a constant, which worked only while seeds were
        # integers -- every preview against a real run's seed raised
        # TypeError.
        self.rng = RunRng("%s_preview" % self.seed)
        try:
            return score_hand(self, self.evaluate_selection(played), played, held).score
        finally:
            for card, enhancement in enhancements:
                card.enhancement = enhancement
            self.jokers, self.rng = real_jokers, real_rng

    def _play(self, indices: tuple[int, ...]) -> None:
        played = [self.hand[i] for i in indices]
        held = [c for i, c in enumerate(self.hand) if i not in indices]
        result = self.evaluate_selection(played)

        # DNA and Sixth Sense act on the played cards before they score, and
        # only on the round's first hand.
        if self.hands_played_this_round == set():
            for joker in list(self.jokers):
                if joker.spec.before_hand is not None:
                    joker.spec.before_hand(joker, played, self)
        self.hands_left -= 1
        self.hands_played += 1
        self.hand_levels.plays[result.hand] += 1

        self.last_hand = result.hand.label

        # The Arm takes the level off *before* the hand scores. The game calls
        # debuff_hand and only then reads G.GAME.hands[text].mult, so the hand
        # is already a level down by the time it is worth anything. Doing it
        # after scoring, which is what this did, gave the round one free hand
        # at the old level -- worth about a third here.
        boss = self.boss
        if boss is not None and boss.level_down_played_hand:
            self.hand_levels.levels[result.hand] = max(
                1, self.hand_levels.levels[result.hand] - 1)

        ctx = score_hand(self, result, played, held)
        # Jokers that make a card off the back of a hand -- Superposition,
        # Séance, Vagabond -- run once the hand has resolved, so they can ask
        # what it turned out to be.
        for joker in list(self.jokers):
            if joker.spec.after_hand is not None:
                joker.spec.after_hand(joker, ctx)
        gained = ctx.score
        self.chips_scored += gained
        self.log(f"{result.hand.label} scored {gained} "
                 f"({ctx.chips:g} x {ctx.mult:g}) -> {self.chips_scored}")
        if ctx.money_gained:
            self.add_money(ctx.money_gained, "cards")

        boss = self.boss
        if boss is not None:
            if boss.money_per_card_played:
                self.add_money(boss.money_per_card_played * len(played), boss.name)
            if boss.zero_money_on_most_played and self._is_most_played(result.hand):
                self.money = 0
                self.log(f"{boss.name}: money set to $0")

        self.hands_played_this_round.add(result.hand)
        for card in played:
            self.played_this_ante.add(card.uid)

        for card in shattered_glass(self, result.scoring):
            self.remove_card(card)
            self.log(f"{card} shattered")

        for card in played:
            if card in self.hand:
                self.hand.remove(card)
                self.discard_pile.append(card)

        if boss is not None and boss.discard_random_on_play and self.hand:
            # By creation order, not by what is on screen. The game draws
            # these with pseudorandom_element, which sorts the table by
            # sort_id before picking an index -- so the two cards The Hook
            # takes depend on when the cards were made, and a hand the player
            # has dragged around loses the same two either way.
            pool = sorted(self.hand, key=lambda card: card.uid)
            for card in self.rng.sample("hook", pool,
                                        min(boss.discard_random_on_play,
                                            len(pool))):
                self.hand.remove(card)
                self.discard_pile.append(card)

        if self.chips_scored >= self.blind.target:
            self._beat_blind()
        elif self.hands_left <= 0:
            self.phase = Phase.GAME_OVER
            self.log(f"Lost on ante {self.ante} {self.blind.name}")
        else:
            self._draw_to_hand_size()

    def _is_most_played(self, hand: HandType) -> bool:
        top = max(self.hand_levels.plays.values())
        return self.hand_levels.plays[hand] == top

    def _discard(self, indices: tuple[int, ...]) -> None:
        cards = [self.hand[i] for i in indices]
        self.discards_left -= 1
        first = self.discards_used == 0
        self.discards_used += 1
        for joker in list(self.jokers):
            if joker.spec.discarded is not None:
                joker.spec.discarded(joker, cards, self)
        if first:
            for joker in list(self.jokers):
                if joker.spec.on_first_discard is not None:
                    joker.spec.on_first_discard(joker, cards, self)
        for card in cards:
            if card.seal is Seal.PURPLE:
                self.add_consumables(
                    self.random_consumables(ConsumableKind.TAROT, 1, "8ba"))
            self.hand.remove(card)
            self.discard_pile.append(card)
        self._draw_to_hand_size()

    def _beat_blind(self) -> None:
        """Close the round and stop on the cash-out screen.

        The payout is worked out here but not paid: the game shows it and
        waits, and paying early makes the two engines disagree about money for
        the whole of that window. The deck comes back now, though -- the
        engine has all fifty-two cards again the moment the round ends.
        """
        assert self.blind is not None
        gold = sum(3 for c in self.hand
                   if c.enhancement is Enhancement.GOLD)

        # A blue seal makes the Planet for the *last hand played this round*,
        # once, at the end of it, for each sealed card still in hand. The
        # simulator fired it on every hand played instead, which is both too
        # often and a round too early -- and it read the hand being played
        # rather than the one the round ended on.
        if self.last_hand is not None:
            hand = next((h for h in HandType if h.label == self.last_hand),
                        None)
            for card in self.hand:
                if card.seal is Seal.BLUE and hand is not None:
                    self.add_consumables(
                        [cons.REGISTRY[PLANET_FOR_HAND[hand]]])
        self.pending_payout = (self.blind.reward
                               + max(0, self.hands_left)
                               + min(self.interest_cap,
                                     max(0, self.money) // 5)
                               + gold)

        # Every card returns to the deck as the round closes -- but in the
        # game's order, not in the order the deck was built. The hand goes to
        # the discard a card at a time from the front, and the discard then
        # goes to the deck from the back, each card inserted at the deck's
        # front, which leaves the discard's own order sitting under the cards
        # that were never drawn.
        #
        # The order matters because the next thing to draw from this deck is
        # not the next round -- that reshuffles -- but an Arcana or Spectral
        # pack opened in the shop, whose hand is what a Tarot from that pack
        # is used on. Rebuilding the deck in build order dealt that hand from
        # the wrong end of it entirely.
        self.discard_pile.extend(self.hand)
        self.hand = []
        self.draw_pile = self.discard_pile + self.draw_pile
        self.discard_pile = []

        self.beaten_blind = self.blind
        self.beaten_was_boss = self.blind.kind is BlindKind.BOSS
        self.blind = None
        self.phase = Phase.ROUND_EVAL

        # The ante turns over the moment the boss round closes -- the engine
        # already reads the next ante on the cash-out screen, before a penny
        # has been paid.
        if self.beaten_was_boss:
            self.blind_index = 0
            self.ante += 1
            # After the ante turns over, not before: the game raises the ante
            # and *then* rolls, so the voucher for the ante about to start is
            # drawn from that ante's pool. Rolling a step earlier draws it
            # from the pool of the ante just finished, which is a different
            # voucher from the same seed.
            self._roll_voucher()
        else:
            self.blind_index += 1

        # Every round, after the ante has turned over and the voucher has been
        # drawn -- the game's own order in update_round_eval.
        self._reset_round_cards()

        # The cash-out screen holds nothing the policy decides. You can
        # reorder or sell jokers there, which is real but niche, and the
        # env has never offered it as a choice -- it advances by itself. So
        # the simulator does too, and the two stay in step without a screen
        # existing on one side and not the other.
        #
        # Recorded in the README as a thing to revisit if selling on the
        # cash-out screen ever matters.
        self._cash_out()

    def _cash_out(self) -> None:
        """Take the payout and move on, as pressing Cash Out does."""
        assert self.beaten_blind is not None
        self.add_money(self.pending_payout, f"{self.beaten_blind.name} payout")
        self.pending_payout = 0

        # A beaten boss ends the ante, and the next one's two skip tags are
        # rolled here, on the cash-out screen -- after the voucher, which went
        # a moment earlier when the boss fell.
        if self.beaten_blind.kind is BlindKind.BOSS:
            self._roll_ante_tags()
            # reset_blinds runs after the tags, and draws the next boss.
            self._roll_boss()

        # End-of-round joker money is part of what the cash-out screen pays,
        # not something already in the bankroll when it appears. Golden Joker's
        # $4 is listed there beside the blind's reward; running these when the
        # blind was beaten paid it a whole screen early.
        for joker in list(self.jokers):
            if joker.spec.round_end is not None:
                joker.spec.round_end(joker, self)

        # The stake's stickers are paid for here: a rental takes three
        # dollars every round, and a perishable counts one round closer to
        # being switched off for good.
        for joker in self.jokers:
            if joker.rental:
                self.add_money(-RENTAL_RATE, f"{joker.name} rental")
            if joker.perishable and joker.perish_tally > 0:
                joker.perish_tally -= 1
                if joker.perish_tally == 0:
                    joker.debuffed = True
                    self.log(f"{joker.name} perished")

        # Cashing out is also where the round's counters go back: the engine
        # already reads a full complement of hands and discards, and no chips
        # scored, before the shop opens.
        self.chips_scored = 0
        self.hands_left, self.discards_left = self._round_allowance()
        # Cleared as the shop opens, before any tag runs: the Coupon Tag pays
        # for one shop, not for every shop after it.
        self.shop_free = False

        # The blind stays cleared through the shop -- the engine reports no
        # blind and no target until the next one is chosen, so putting it back
        # here left the simulator still showing the beaten blind's target.
        was_boss = self.beaten_was_boss
        self.beaten_blind = None
        if was_boss:
            if Tag.INVESTMENT in self.tags:
                self.tags.remove(Tag.INVESTMENT)
                self.add_money(25, "Investment Tag")
            self.played_this_ante = set()
            if self.ante >= WIN_ANTE:
                self.phase = Phase.WON
                self.log(f"Run won at ante {self.ante}")
                return



        self._open_shop()

    # ------------------------------------------------------------------
    # shop
    # ------------------------------------------------------------------

    def _shop_slot_count(self) -> int:
        return 2 + sum(v.shop_slots for v in self.vouchers)

    def _roll_slot(self) -> ShopSlot:
        """One shop slot, rolled the way the game rolls it.

        This used to invent its own weights and pools. It now goes through
        balatro.shop_pool, which is checked against the engine draw for draw
        -- the distribution a policy trains against is as much a part of
        fidelity as the scoring, and it is the half that fails silently.
        """
        played = [h.label for h, n in self.hand_levels.plays.items() if n > 0]
        owned = {c.enhancement.value for c in self.full_deck}
        kind, key = shop_pool.draw_shop_card(
            self.rng, self.ante, rates=self._shop_rates(),
            seen_jokers=self.seen_centers, played_hands=played,
            owned_enhancements={"m_%s" % e for e in owned})

        if kind == "Joker":
            spec = JOKER_REGISTRY[shop_pool.NAME_BY_JOKER_KEY[key]]
            # "edi" + the append + the ante. The ante was missing, so every
            # shop in the run polled the same pool and got the wrong answer:
            # a Holographic Loyalty Card came out plain, and holographic is
            # ten mult.
            edition = _EDITION_BY_NAME[shop_pool.poll_edition(
                self.rng, "edi%s%d" % (shop_pool.SHOP_APPEND, self.ante),
                edition_rate=self.edition_rate)]
            joker = JokerInstance(spec, edition=edition)
            price = self._apply_stickers(
                joker, self.price(shop_mod.joker_price(spec, edition)))
            return ShopSlot("joker", price, joker=joker)
        if kind in ("Tarot", "Planet", "Spectral"):
            spec = cons.REGISTRY[shop_pool.NAME_BY_CONSUMABLE_KEY[key]]
            return ShopSlot("consumable", self.price(spec.cost), consumable=spec)
        # A playing card, which only appears once Magic Trick or Illusion has
        # raised the playing card rate. This used to hand back an arbitrary
        # Tarot as a placeholder, so a shop that offered a card offered the
        # wrong thing entirely and the purchase went to the wrong slot.
        #
        # Illusion is the reason the type is decided by a roll: with it, a
        # shop card is Enhanced rather than Base six times in ten, and may
        # carry an edition and a seal besides. Without it the roll is not
        # made at all -- the game short-circuits on used_vouchers.
        illusion = any(v.key == "v_illusion" for v in self.vouchers)
        enhanced = illusion and self.rng.pseudorandom("illusion") > 0.6
        enhancement = Enhancement.NONE
        if enhanced:
            key = self.rng.random_element(
                shop_pool.ENHANCEMENTS,
                "Enhanced%s%d" % (shop_pool.SHOP_APPEND, self.ante))
            enhancement = Enhancement(key[2:])
        front = self.rng.random_element(
            shop_pool.FRONTS,
            "front%s%d" % (shop_pool.SHOP_APPEND, self.ante))
        suit, rank = front.split("_")
        card = Card(_RANK_BY_CODE[rank], _SUIT_BY_CODE[suit])
        card.enhancement = enhancement
        if illusion and self.rng.pseudorandom("illusion") > 0.8:
            roll = self.rng.pseudorandom("illusion")
            card.edition = (Edition.POLYCHROME if roll > 1 - 0.15
                            else Edition.HOLOGRAPHIC if roll > 0.5
                            else Edition.FOIL)
        return ShopSlot("card", self.price(1), card=card)

    def _shop_rates(self) -> dict:
        """The run's card-type rates, which the deck and vouchers move.

        A voucher *sets* its rate rather than adding to it -- Tarot Tycoon
        replaces Tarot Merchant's number rather than stacking with it -- so
        the last one redeemed wins, which for an upgrade is always the bigger.
        """
        rates = dict(shop_pool.BASE_RATES)
        rates["Spectral"] += self.deck_config.get("spectral_rate", 0)
        for voucher in self.vouchers:
            if voucher.tarot_rate:
                rates["Tarot"] = voucher.tarot_rate
            if voucher.planet_rate:
                rates["Planet"] = voucher.planet_rate
            if voucher.playing_card_rate:
                rates["Base"] = voucher.playing_card_rate
        return rates

    # The four edition tags, and what each one puts on a joker. They fire on
    # a shop card as it is made -- store_joker_modify -- and only on a joker
    # that has no edition yet.
    EDITION_TAGS = {Tag.FOIL: Edition.FOIL, Tag.HOLOGRAPHIC: Edition.HOLOGRAPHIC,
                    Tag.POLYCHROME: Edition.POLYCHROME,
                    Tag.NEGATIVE: Edition.NEGATIVE}

    def _modify_shop_slot(self, slot: ShopSlot) -> ShopSlot:
        """Let an edition tag claim a shop card as it is made.

        The tag does two things and the second is easy to miss: it puts the
        edition on, and it marks the card couponed, which sets its price to
        nothing. A run that skipped a blind for a Polychrome Tag gets a
        polychrome joker *free*, and the simulator was charging for it.

        Only the first tag that applies fires, and only on a joker with no
        edition of its own.
        """
        if slot.joker is None or slot.joker.edition is not Edition.NONE:
            return slot
        for tag in list(self.tags):
            edition = self.EDITION_TAGS.get(tag)
            if edition is None:
                continue
            self.tags.remove(tag)
            slot.joker.edition = edition
            slot.price = 0
            self.log(f"{tag.value}: {slot.joker.name} is {edition.value}, free")
            break
        return slot

    def _fill_shop(self, shop: Shop) -> None:
        shop.slots = []
        for _ in range(self._shop_slot_count()):
            forced = self._forced_shop_slot()
            slot = forced if forced is not None else self._roll_slot()
            shop.slots.append(self._modify_shop_slot(slot))
        # The Coupon Tag runs last, over the finished shop: everything in it
        # is free, including the packs.
        if Tag.COUPON in self.tags:
            self.tags.remove(Tag.COUPON)
            self.shop_free = True
            self.log("Coupon Tag: the shop is free")
        if self.shop_free:
            for slot in shop.slots:
                slot.price = 0

    def _open_shop(self) -> None:
        shop = Shop()
        self._fill_shop(shop)
        shop.packs = [self._roll_pack() for _ in range(2)]
        shop.voucher = (shop_mod.VOUCHER_BY_KEY[self.round_voucher]
                        if self.round_voucher else None)
        self.shop = shop
        self.phase = Phase.SHOP
        self._apply_shop_tags()

    def _roll_pack(self) -> PackSpec:
        """One shop pack, rolled from the game's own Booster pool.

        This used to pick a kind from weights invented here and then a size,
        which gives the right kinds at the wrong sizes -- and a pack's price
        follows its size, so a mega where the game offers a normal costs the
        run four dollars it never spent.
        """
        first = not self.first_shop_buffoon
        self.first_shop_buffoon = True
        return shop_mod.pack_from_row(
            shop_pool.draw_pack(self.rng, self.ante, first_shop=first))

    # A tag does not wait for the shop. Each one names the moment it fires,
    # and the simulator used to fire all of them when the shop opened, which
    # is the wrong screen for most and the wrong order for the rest:
    #
    #   immediate           on the blind select screen, all of them
    #   new_blind_choice    same screen, but only the first that triggers
    #   store_joker_create  while a shop slot is being filled, in its place
    #   round_start_bonus   when the round begins
    #   eval                at cash-out
    #
    # Getting this wrong is not a timing nicety: a Charm Tag opens its pack
    # before the blind, so the player takes a Tarot into the round the tag was
    # skipped for, and the shop three screens later is drawn from pools that
    # have already moved.
    PACK_TAGS = (Tag.CHARM, Tag.METEOR, Tag.BUFFOON, Tag.ETHEREAL,
                 Tag.STANDARD)

    def _apply_blind_select_tags(self) -> None:
        for tag in list(self.tags):
            if tag is Tag.ECONOMY:
                self.add_money(min(40, max(0, self.money)), tag.value)
                self.tags.remove(tag)
        # Only the first tag that actually does something fires here -- the
        # game breaks out of the loop -- so two Charm Tags open one pack now
        # and the other at the next blind.
        for tag in list(self.tags):
            if tag is Tag.BOSS:
                # Re-rolls the boss, free -- the paid reroll is the Director's
                # Cut button, which costs ten.
                self.tags.remove(tag)
                self._roll_boss()
                self.log("Boss Tag: the boss is re-rolled")
                return
            if tag in self.PACK_TAGS:
                self.tags.remove(tag)
                self._open_pack(
                    shop_mod.pack_from_key(self._tag_pack_key(tag)), free=True)
                return

    def _forced_shop_slot(self) -> ShopSlot | None:
        """A shop slot an Uncommon or Rare Tag fills instead of a roll.

        The tag does not hand the player a joker -- it puts a free one in the
        shop, in place of a card that is then never rolled. The simulator used
        to add it straight to the joker row, so a run held a joker it had
        never bought and the shop offered one card too many.
        """
        for tag in list(self.tags):
            if tag not in (Tag.UNCOMMON, Tag.RARE):
                continue
            rarity, append = ((2, "uta") if tag is Tag.UNCOMMON
                              else (3, "rta"))
            self.tags.remove(tag)
            key = shop_pool.draw_joker(self.rng, self.ante,
                                       seen_jokers=self.seen_centers,
                                       rarity=rarity, append=append)
            spec = JOKER_REGISTRY[shop_pool.NAME_BY_JOKER_KEY[key]]
            edition = _EDITION_BY_NAME[
                shop_pool.poll_edition(self.rng, "edi%s%d" % (append, self.ante))]
            return ShopSlot("joker", 0, joker=JokerInstance(spec,
                                                            edition=edition))
        return None

    def _apply_shop_tags(self) -> None:
        # What is left for the shop itself: the D6 Tag's free rerolls, the
        # Voucher Tag's extra voucher and the Coupon Tag's free cards. None
        # are modelled yet, and they stay in self.tags rather than being
        # silently dropped.
        return

    def _redeem_voucher(self, voucher: Voucher) -> None:
        """What redeeming does beyond the fields read off self.vouchers.

        Most of a voucher is passive -- shop size, hand size, interest cap and
        the rest are summed wherever they are needed. Two are not. Hieroglyph
        and Petroglyph take an ante away there and then, and Grabber and
        Wasteful hand over their extra hand or discard for the round in
        progress rather than only from the next one.
        """
        if voucher.ante_shift:
            self.ante = max(1, self.ante + voucher.ante_shift)
        self.hands_left += voucher.extra_hands
        self.discards_left += voucher.extra_discards

    def _leave_shop(self) -> None:
        # The blind index and the ante moved on at cash-out; leaving the shop
        # only chooses which blind is now on offer.
        self.shop = None
        self._next_blind()

    # ------------------------------------------------------------------
    # packs
    # ------------------------------------------------------------------

    # Which pack each pack tag hands over. They are fixed keys rather than
    # rolls, and they are mega packs -- five cards, choose two -- where the
    # simulator used to open a normal one of three. Charm and Meteor pick
    # between two identical centres with a bare math.random, which reads the
    # live stream rather than a pool of its own.
    def _tag_pack_key(self, tag: Tag) -> str:
        if tag is Tag.CHARM:
            return "p_arcana_mega_%d" % int(self.rng.math_random(1, 2))
        if tag is Tag.METEOR:
            return "p_celestial_mega_%d" % int(self.rng.math_random(1, 2))
        return {Tag.ETHEREAL: "p_spectral_normal_1",
                Tag.STANDARD: "p_standard_mega_1",
                Tag.BUFFOON: "p_buffoon_mega_1"}[tag]

    def _open_pack(self, spec: PackSpec, free: bool = False) -> None:
        """Fill a pack the way Card:open fills it.

        The contents used to be drawn uniformly from a whole card set, which
        offered Tarots the run had already been given and Planet X for hands
        nobody had played, and -- because a real pack takes a known number of
        rolls from known pools -- left every later draw in the run standing in
        the wrong place. shop_pool.pack_contents is checked against the engine
        card for card.
        """
        self.pack = spec
        self.pack_picks_left = spec.picks
        played = [h.label for h, n in self.hand_levels.plays.items() if n > 0]
        owned = {"m_%s" % c.enhancement.value for c in self.full_deck}
        contents = shop_pool.pack_contents(
            self.rng, spec.kind.value.title(), spec.options, self.ante,
            played_hands=played, seen=self.seen_centers,
            seen_jokers=self.seen_centers, owned_enhancements=owned,
            showman=any(j.name == "Showman" for j in self.jokers),
            stickers=self.sticker_rules)

        self.pack_options = []
        for entry in contents:
            self.pack_options.append(self._pack_card(entry))
        self.phase = Phase.PACK

        # An Arcana or a Spectral pack deals a hand. Its cards need targets --
        # a Tarot converts cards, Cryptid copies one -- so the game draws to
        # the hand limit when the pack opens even in the middle of a shop, and
        # sends the hand back to the deck when it closes. The other three
        # packs deal nothing.
        if spec.kind in (PackKind.ARCANA, PackKind.SPECTRAL) and not self.hand:
            self._pack_dealt_hand = True
            self._draw_to_hand_size()

    def _pack_card(self, entry: dict):
        """One entry from shop_pool.pack_contents, as a simulator object."""
        if entry["set"] == "Joker":
            spec = JOKER_REGISTRY[shop_pool.NAME_BY_JOKER_KEY[entry["key"]]]
            joker = JokerInstance(spec,
                                  edition=_EDITION_BY_NAME[entry["edition"]])
            joker.eternal = entry.get("eternal", False)
            joker.perishable = entry.get("perishable", False)
            joker.rental = entry.get("rental", False)
            if joker.perishable:
                joker.perish_tally = PERISHABLE_ROUNDS
            return joker
        if entry["set"] == "Playing":
            card = Card(_RANK_BY_CODE[entry["rank"]], _SUIT_BY_CODE[entry["suit"]])
            if entry["enhancement"]:
                card.enhancement = Enhancement(entry["enhancement"][2:])
            card.edition = _EDITION_BY_NAME[entry["edition"]]
            if entry["seal"]:
                card.seal = Seal(entry["seal"].lower())
            return card
        return cons.REGISTRY[shop_pool.NAME_BY_CONSUMABLE_KEY[entry["key"]]]

    def _close_pack(self) -> None:
        self.pack = None
        self.pack_options = []
        self.pack_picks_left = 0
        if self._pack_dealt_hand:
            # draw_from_hand_to_deck: the hand the pack dealt goes back to the
            # deck, a card at a time from the front of the hand, each one
            # emplaced at the deck's front -- which is its bottom, since
            # drawing takes from the back. So the next pack in the same shop
            # deals a different hand, and this one is under everything.
            for card in self.hand:
                self.draw_pile.insert(0, card)
            self.hand = []
            self._pack_dealt_hand = False
        self.phase = Phase.SHOP if self.shop is not None else Phase.BLIND_SELECT

    def _pick_pack(self, index: int, card_indices: tuple[int, ...]) -> None:
        choice = self.pack_options[index]
        if isinstance(choice, JokerInstance):
            self.jokers.append(choice)
            self.log(f"Pack: took {choice.name}")
        elif isinstance(choice, Card):
            self.add_card(choice)
            self.log(f"Pack: added {choice} to deck")
        else:
            # A consumable taken from a pack is used there and then. It never
            # reaches a slot -- the game's pack screen calls use_card, not
            # buy -- so a simulator that stores it leaves the run holding a
            # card the player already spent, and every slot after it numbered
            # one place out.
            targets = [self.hand[i] for i in card_indices
                       if i < len(self.hand)]
            self.use_consumable(choice, targets)
        self.pack_options.pop(index)
        self.pack_picks_left -= 1
        if self.pack_picks_left <= 0 or not self.pack_options:
            self._close_pack()

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    def _card_subsets(self, max_size: int) -> list[tuple[int, ...]]:
        idx = range(len(self.hand))
        out: list[tuple[int, ...]] = []
        for size in range(1, min(max_size, len(self.hand)) + 1):
            out.extend(itertools.combinations(idx, size))
        return out

    def _play_actions(self) -> list[Action]:
        """Playable subsets, honouring boss restrictions where possible.

        A boss must never leave the player with nothing to do, so if its
        restriction rules out every hand the restriction is dropped for that
        decision rather than deadlocking the run.
        """
        subsets = self._card_subsets(MAX_PLAYED)
        strict = [Action(ActionType.PLAY, cards=s)
                  for s in subsets if self._restriction_ok(s)]
        if strict:
            return strict
        return [Action(ActionType.PLAY, cards=s) for s in subsets]

    def legal_actions(self) -> list[Action]:
        if self.phase is Phase.BLIND_SELECT:
            actions = [Action(ActionType.SELECT_BLIND)]
            if self.blind is not None and self.blind.kind is not BlindKind.BOSS:
                actions.append(Action(ActionType.SKIP_BLIND))
            return actions

        if self.phase is Phase.PLAYING:
            actions: list[Action] = self._play_actions()
            if self.discards_left > 0:
                actions += [Action(ActionType.DISCARD, cards=s)
                            for s in self._card_subsets(MAX_PLAYED)]
            actions += self._consumable_actions()
            actions += [Action(ActionType.SELL_JOKER, index=i)
                        for i, j in enumerate(self.jokers) if not j.eternal]
            return actions

        if self.phase is Phase.SHOP:
            assert self.shop is not None
            actions = [Action(ActionType.LEAVE_SHOP)]
            for i, slot in enumerate(self.shop.slots):
                if slot.price > self.money:
                    continue
                if slot.kind == "joker" and len(self.jokers) >= self.joker_slots:
                    continue
                if slot.kind == "consumable" and len(self.consumables) >= self.consumable_slots:
                    continue
                actions.append(Action(ActionType.BUY, index=i))
            for i, pack in enumerate(self.shop.packs):
                if self.price(pack.cost) <= self.money:
                    actions.append(Action(ActionType.BUY_PACK, index=i))
            if (self.shop.voucher is not None and not self.shop.voucher_bought
                    and self.price(self.shop.voucher.cost) <= self.money):
                actions.append(Action(ActionType.BUY_VOUCHER))
            discount = sum(v.reroll_discount for v in self.vouchers)
            if self.shop.reroll_cost(discount) <= self.money:
                actions.append(Action(ActionType.REROLL))
            actions += self._consumable_actions()
            actions += [Action(ActionType.SELL_JOKER, index=i)
                        for i, j in enumerate(self.jokers) if not j.eternal]
            actions += [Action(ActionType.SELL_CONSUMABLE, index=i)
                        for i in range(len(self.consumables))]
            return actions

        if self.phase is Phase.PACK:
            actions = [Action(ActionType.SKIP_PACK)]
            for i, option in enumerate(self.pack_options):
                if isinstance(option, JokerInstance):
                    if len(self.jokers) < self.joker_slots:
                        actions.append(Action(ActionType.PICK_PACK, index=i))
                elif isinstance(option, Card):
                    actions.append(Action(ActionType.PICK_PACK, index=i))
                else:
                    actions += self._pack_consumable_actions(i, option)
            return actions

        return []

    def _pack_consumable_actions(self, index: int,
                                 spec: ConsumableSpec) -> list[Action]:
        if spec.targets == 0:
            return [Action(ActionType.PICK_PACK, index=index)]
        if self.phase is Phase.PACK and not self.hand:
            # Opened from the shop: bank it instead of applying it now.
            if len(self.consumables) < self.consumable_slots:
                return [Action(ActionType.PICK_PACK, index=index)]
            return []
        return [Action(ActionType.PICK_PACK, index=index, cards=subset)
                for subset in self._card_subsets(
                    spec.max_targets or spec.targets)
                if spec.accepts(len(subset))]

    def _consumable_actions(self) -> list[Action]:
        actions: list[Action] = []
        for i, spec in enumerate(self.consumables):
            if spec.targets == 0:
                actions.append(Action(ActionType.USE_CONSUMABLE, index=i))
                continue
            if not self.hand:
                continue
            for subset in self._card_subsets(spec.max_targets or spec.targets):
                if spec.accepts(len(subset)):
                    actions.append(Action(ActionType.USE_CONSUMABLE, index=i,
                                          cards=subset))
        return actions

    # ------------------------------------------------------------------
    # single-action legality (what the env masks with; no enumeration)
    # ------------------------------------------------------------------

    def _valid_indices(self, cards: tuple[int, ...], max_size: int) -> bool:
        return (1 <= len(cards) <= max_size
                and all(0 <= i < len(self.hand) for i in cards)
                and all(a < b for a, b in zip(cards, cards[1:])))

    def _restriction_ok(self, cards: tuple[int, ...]) -> bool:
        boss = self.boss
        if boss is None:
            return True
        if boss.min_cards_played and len(cards) != boss.min_cards_played:
            return False
        if boss.no_repeat_hand or boss.lock_first_hand_type:
            hand = self.evaluate_selection([self.hand[i] for i in cards]).hand
            if boss.no_repeat_hand and hand in self.hands_played_this_round:
                return False
            if (boss.lock_first_hand_type and self.hands_played_this_round
                    and hand not in self.hands_played_this_round):
                return False
        return True

    def _restriction_satisfiable(self) -> bool:
        """Whether any subset satisfies the boss restriction (see _play_actions)."""
        boss = self.boss
        if boss is None:
            return True
        if boss.min_cards_played and len(self.hand) < boss.min_cards_played:
            return False
        if not (boss.no_repeat_hand or boss.lock_first_hand_type):
            return True
        key = (len(self.hand), frozenset(self.hands_played_this_round),
               tuple(c.uid for c in self.hand))
        if self._satisfiable_cache and self._satisfiable_cache[0] == key:
            return self._satisfiable_cache[1]
        answer = any(self._restriction_ok(s) for s in self._card_subsets(MAX_PLAYED))
        self._satisfiable_cache = (key, answer)
        return answer

    def _consumable_legal(self, index: int, cards: tuple[int, ...]) -> bool:
        if not 0 <= index < len(self.consumables):
            return False
        spec = self.consumables[index]
        if not spec.accepts(len(cards)):
            return False
        if spec.targets == 0:
            return not cards
        return self._valid_indices(cards, spec.max_targets or spec.targets)

    def is_legal(self, action: Action) -> bool:
        """Exact membership test for `legal_actions()` without building the list."""
        t, index, cards = action.type, action.index, action.cards

        if self.phase is Phase.BLIND_SELECT:
            if t is ActionType.SELECT_BLIND:
                return not cards
            if t is ActionType.SKIP_BLIND:
                return self.blind is not None and self.blind.kind is not BlindKind.BOSS
            return False

        if self.phase is Phase.PLAYING:
            if t is ActionType.PLAY:
                if not self._valid_indices(cards, MAX_PLAYED):
                    return False
                return (self._restriction_ok(cards)
                        or not self._restriction_satisfiable())
            if t is ActionType.DISCARD:
                return self.discards_left > 0 and self._valid_indices(cards, MAX_PLAYED)
            if t is ActionType.USE_CONSUMABLE:
                return self._consumable_legal(index, cards)
            if t is ActionType.SELL_JOKER:
                return (0 <= index < len(self.jokers)
                        and not self.jokers[index].eternal)
            return False

        if self.phase is Phase.SHOP:
            shop = self.shop
            if shop is None:
                return False
            if t is ActionType.LEAVE_SHOP:
                return True
            if t is ActionType.BUY:
                if not 0 <= index < len(shop.slots):
                    return False
                slot = shop.slots[index]
                if slot.price > self.money:
                    return False
                if slot.kind == "joker":
                    return len(self.jokers) < self.joker_slots
                if slot.kind == "consumable":
                    return len(self.consumables) < self.consumable_slots
                return True
            if t is ActionType.BUY_PACK:
                return (0 <= index < len(shop.packs)
                        and self.price(shop.packs[index].cost) <= self.money)
            if t is ActionType.BUY_VOUCHER:
                return (shop.voucher is not None and not shop.voucher_bought
                        and self.price(shop.voucher.cost) <= self.money)
            if t is ActionType.REROLL:
                discount = sum(v.reroll_discount for v in self.vouchers)
                return shop.reroll_cost(discount) <= self.money
            if t is ActionType.USE_CONSUMABLE:
                return self._consumable_legal(index, cards)
            if t is ActionType.SELL_JOKER:
                return (0 <= index < len(self.jokers)
                        and not self.jokers[index].eternal)
            if t is ActionType.SELL_CONSUMABLE:
                return 0 <= index < len(self.consumables)
            return False

        if self.phase is Phase.PACK:
            if t is ActionType.SKIP_PACK:
                return True
            if t is ActionType.PICK_PACK:
                if not 0 <= index < len(self.pack_options):
                    return False
                option = self.pack_options[index]
                if isinstance(option, JokerInstance):
                    return not cards and len(self.jokers) < self.joker_slots
                if isinstance(option, Card):
                    return not cards
                return any(a.cards == cards
                           for a in self._pack_consumable_actions(index, option))
            return False

        return False

    def step(self, action: Action) -> None:
        t = action.type
        if t is ActionType.SELECT_BLIND:
            self._start_round()
        elif t is ActionType.SKIP_BLIND:
            tag = self.ante_tags[self.blind_index]
            key = self.ante_tag_keys[self.blind_index]
            if tag is not None:
                self.tags.append(tag)
            # Sixteen of the twenty-four tags have no effect here yet. The
            # skip still happens and the tag is still what the game offered --
            # naming it in the log keeps the gap visible instead of crashing
            # on it.
            self.log("Skipped %s, gained %s"
                     % (self.blind.name, tag.value if tag else key))
            self.blind_index += 1
            self._next_blind()
        elif t is ActionType.PLAY:
            self._play(action.cards)
        elif t is ActionType.DISCARD:
            self._discard(action.cards)
        elif t is ActionType.USE_CONSUMABLE:
            spec = self.consumables.pop(action.index)
            targets = [self.hand[i] for i in action.cards]
            self.use_consumable(spec, targets)
        elif t is ActionType.SELL_JOKER:
            joker = self.jokers.pop(action.index)
            self.add_money(joker.sell_value, f"sold {joker.name}")
            self.cards_sold += 1
            # Selling is the whole point of some jokers -- Luchador disables
            # the boss, Diet Cola leaves a tag behind -- so the effect fires
            # after it has left the list, as the game does it.
            if joker.spec.disables_boss_on_sell:
                self.disable_blind(joker.name)
            if joker.spec.on_sell is not None:
                joker.spec.on_sell(joker, self)
        elif t is ActionType.SELL_CONSUMABLE:
            spec = self.consumables.pop(action.index)
            self.add_money(max(1, spec.cost // 2), f"sold {spec.name}")
            self.cards_sold += 1
        elif t is ActionType.BUY:
            self._buy(action.index)
        elif t is ActionType.BUY_AND_USE:
            self.buy_and_use(action.index)
        elif t is ActionType.BUY_VOUCHER:
            assert self.shop is not None and self.shop.voucher is not None
            voucher = self.shop.voucher
            self.add_money(-self.price(voucher.cost),
                           f"bought {voucher.name}")
            self.vouchers.append(voucher)
            self.shop.voucher_bought = True
            self.shop.voucher = None
            self._redeem_voucher(voucher)
        elif t is ActionType.REROLL:
            assert self.shop is not None
            discount = sum(v.reroll_discount for v in self.vouchers)
            self.add_money(-self.shop.reroll_cost(discount), "reroll")
            self.shop.rerolls += 1
            # The jokers that count rerolls are told before the new cards are
            # made, which is the game's order -- calculate_joker fires on the
            # button, not on the shop that comes back.
            for joker in list(self.jokers):
                if joker.spec.on_reroll is not None:
                    joker.spec.on_reroll(joker, self)
            self._fill_shop(self.shop)
        elif t is ActionType.BUY_PACK:
            assert self.shop is not None
            pack = self.shop.packs.pop(action.index)
            cost = 0 if self.shop_free else self.price(pack.cost)
            self.add_money(-cost, f"bought {pack.name}")
            self._open_pack(pack)
        elif t is ActionType.PICK_PACK:
            self._pick_pack(action.index, action.cards)
        elif t is ActionType.SKIP_PACK:
            self._close_pack()
        elif t is ActionType.CASH_OUT:
            # A no-op: beating a blind cashes out by itself. Kept as an action
            # so a caller written against the old shape is not broken, and so
            # it reads as deliberate rather than missing.
            pass
        elif t is ActionType.LEAVE_SHOP:
            self._leave_shop()
        else:  # pragma: no cover
            raise ValueError(f"unhandled action {action}")

    def _buy(self, index: int) -> None:
        assert self.shop is not None
        slot = self.shop.slots.pop(index)
        self.add_money(-slot.price, f"bought {slot.label}")
        if slot.joker is not None:
            self.jokers.append(slot.joker)
        elif slot.consumable is not None:
            self.consumables.append(slot.consumable)
        elif slot.card is not None:
            self.add_card(slot.card)

    # ------------------------------------------------------------------
    # convenience
    # ------------------------------------------------------------------

    def summary(self) -> str:
        blind = self.blind.name if self.blind else "-"
        return (f"ante {self.ante} {blind} | {self.phase.value} | ${self.money} | "
                f"{self.chips_scored}/{self.blind.target if self.blind else 0} | "
                f"hands {self.hands_left} discards {self.discards_left} | "
                f"jokers {self.jokers}")
