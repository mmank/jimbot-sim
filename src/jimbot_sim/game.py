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
from .consumables import ConsumableInstance, ConsumableKind, ConsumableSpec
from .hands import (HANDLIST, PLANET_FOR_HAND, SECRET_HANDS, HandLevels,
                    HandType, evaluate)
from .jokers import (EDITION_VALUE, REGISTRY as JOKER_REGISTRY,
                     JokerInstance, Rarity, is_face_for, suit_matches_for)

# The game's rarity numbers, which its pools are keyed by.
_RARITY_INDEX = {Rarity.COMMON: 1, Rarity.UNCOMMON: 2, Rarity.RARE: 3,
                 Rarity.LEGENDARY: 4}
from .rng import RunRng
from .scoring import held_triggers, score_hand, shattered_glass
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
# What the Director's Cut / Retcon button charges to re-roll the boss.
BOSS_REROLL_COST = 10

# The consumables whose use is gated on something other than how many cards
# are selected. Grouped by what they need, from Card:can_use_consumeable.
FREE_JOKER_NEEDED = frozenset({"Judgement", "The Soul", "Wraith"})
FREE_CONSUMABLE_NEEDED = frozenset({"The Emperor", "The High Priestess",
                                    "The Fool"})
PLAIN_JOKER_NEEDED = frozenset({"The Wheel of Fortune", "Ectoplasm", "Hex"})
# These destroy a card picked at random, and want one to spare.
SPARE_CARD_NEEDED = frozenset({"Familiar", "Grim", "Incantation", "Immolate",
                               "Sigil", "Ouija"})
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
    SWAP_JOKER_LEFT = "swap_joker_left"
    SELL_CONSUMABLE = "sell_consumable"
    BUY = "buy"
    BUY_AND_USE = "buy_and_use"
    REROLL_BOSS = "reroll_boss"
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
    DOUBLE = "Double Tag"
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
    HANDY = "Handy Tag"
    GARBAGE = "Garbage Tag"
    SPEED = "Speed Tag"
    TOP_UP = "Top-up Tag"
    ORBITAL = "Orbital Tag"
    VOUCHER = "Voucher Tag"
    D_SIX = "D6 Tag"


TAG_POOL = list(Tag)

# The game's key for each tag, all twenty-four of them. Seven used to be
# missing -- Handy, Garbage, Speed, Top-up, Orbital, Voucher and D6 -- and a
# missing tag was not an inert one: the pool still drew it, the skip still
# happened, and TAG_BY_KEY.get returned None so the reward evaporated. A run
# could skip a blind for a Top-up Tag and get two fewer jokers than the game
# would have given it.
TAG_BY_KEY = {
    "tag_uncommon": Tag.UNCOMMON,
    "tag_rare": Tag.RARE,
    "tag_charm": Tag.CHARM,
    "tag_meteor": Tag.METEOR,
    "tag_buffoon": Tag.BUFFOON,
    "tag_boss": Tag.BOSS,
    "tag_double": Tag.DOUBLE,
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
    "tag_handy": Tag.HANDY,
    "tag_garbage": Tag.GARBAGE,
    "tag_skip": Tag.SPEED,
    "tag_top_up": Tag.TOP_UP,
    "tag_orbital": Tag.ORBITAL,
    "tag_voucher": Tag.VOUCHER,
    "tag_d_six": Tag.D_SIX,
}

# The five that pay out the instant the blind is skipped, rather than waiting
# for a shop or a blind choice. G.GAME.tags is walked for `immediate` inside
# skip_blind itself, after the skip has been counted and the new tag added --
# so a Speed Tag counts the very skip that produced it.
IMMEDIATE_TAGS = (Tag.HANDY, Tag.GARBAGE, Tag.SPEED, Tag.TOP_UP, Tag.ORBITAL)


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
    consumables: list[ConsumableInstance] = field(default_factory=list)
    vouchers: list[Voucher] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    ante_tags: list = field(default_factory=list)   # skip rewards [small, big]
    # Whether the consumable being applied right now came straight out
    # of a booster rather than a slot. See consumables._wheel_of_fortune.
    using_from_pack: bool = False
    # The key of the consumable being applied right now. G.FUNCS.use_card
    # takes the card out of its area but does not remove it until it
    # dissolves, after its effect (button_callbacks.lua:2209, 2258-2260), so
    # its used_jokers entry stands for the whole effect: The Emperor cannot
    # draw another Emperor. See seen_centers.
    using_key: str = ""
    # G.STATES.PLAY_TAROT: true while a consumable's effect runs. use_card
    # switches G.STATE for the whole effect and puts it back only in an event
    # queued behind it (button_callbacks.lua:2178-2185, 2258-2263), so a hand
    # size a Hex or a Judgement raises is not dealt into. See
    # _hand_size_changed.
    playing_tarot: bool = False
    # The setting_blind pass while it runs (see _setting_blind): the events
    # its jokers queue for after it, the jokers Madness and Ceremonial Dagger
    # have marked getting_sliced, and G.GAME.joker_buffer. The event list is
    # None outside the pass, when there is nothing to wait for.
    blind_select_events: list | None = None
    getting_sliced: list = field(default_factory=list)
    joker_buffer: int = 0
    # The jokers that get a money row on the cash-out screen, settled when
    # the round is evaluated -- before the beaten blind lets go of the joker
    # Crimson Heart held. None outside a cash-out. See _beat_blind.
    dollar_rows: list | None = None
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
    # G.GAME.pool_flags: the run's history as the pools read it. One flag in
    # the whole game -- 'gros_michel_extinct', set when Gros Michel dies
    # (card.lua:3037) -- and it swaps Gros Michel out of every later pool and
    # Cavendish in. Every call that rolls a joker has to pass it.
    pool_flags: set = field(default_factory=set)
    rerolls: int = 0
    blinds_skipped: int = 0
    # G.GAME.unused_discards, which the Garbage Tag pays a dollar each for.
    # A run total, banked at the end of every round, not this round's leftovers.
    unused_discards: int = 0
    # G.GAME.orbital_choices[ante][blind]. Rolled once per ante and blind and
    # remembered, so a Double Tag's copy levels the same hand as the original.
    orbital_choices: dict = field(default_factory=dict)
    # G.GAME.round_resets.temp_handsize -- the Juggle Tag's three cards. It
    # lasts one round and is handed back when that round ends, and it stacks:
    # round_start_bonus is applied to every tag held, without breaking.
    temp_hand_size: int = 0
    # G.GAME.current_round.most_played_poker_hand, which is what The Ox reads.
    # A snapshot taken when a boss round ends, not a live count -- it starts
    # at High Card and is only ever rewritten there.
    most_played_hand: HandType = HandType.HIGH_CARD
    # G.GAME.round_scores.hand.amt -- the biggest single hand scored this run.
    # A high score the game only ever raises, and the one number that says how
    # strong a build has become rather than how far it has got.
    best_hand: int = 0
    # G.GAME.current_round.reroll_cost, which survives the shop closing: the
    # game resets it at the start of a round, not when the shop is left, so a
    # shop rerolled twice still reads its climbed price afterwards.
    reroll_cost_carried: int = 5
    # round_resets.blind_states, for the ante in progress: which of this
    # ante's three blinds were skipped rather than beaten. The run info
    # screen shows the difference and a skipped blind pays nothing, so it is
    # not the same as a defeated one.
    skipped_this_ante: set = field(default_factory=set)
    cards_sold: int = 0
    glass_destroyed: int = 0
    lucky_triggers: int = 0
    chips_scored: int = 0
    hands_left: int = 0
    discards_left: int = 0
    hands_played_this_round: set[HandType] = field(default_factory=set)
    # Blind.only_hand: The Mouth's hand for the round, which is the type of
    # the first hand it let through and nothing else. See hand_is_debuffed.
    mouth_only_hand: HandType | None = None


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
    # G.GAME.ecto_minus. Ectoplasm's hand-size cost is not a flat one: it
    # starts at one and rises by one with every Ectoplasm used in the run.
    ecto_minus: int = 1
    # round_resets.blind_choices.Boss: this ante's boss, drawn at its start.
    ante_boss: str = ""
    # The card Cerulean Bell nominates: every hand must include it.
    forced_card: object = None
    # round_resets.boss_rerolled: Director's Cut allows one reroll an ante and
    # this is what remembers that it has been spent. Reset when a boss falls.
    boss_rerolled: bool = False
    # The stake, one to eight. It is not a difficulty label: it changes the
    # chips every ante asks for, the discards a round starts with, whether
    # the Small Blind pays, and what stickers the shop puts on its jokers.
    stake: int = 1
    # Put every sticker on every stake, whatever the stake would allow.
    #
    # Training only. The stickers are gated so that eternal appears at stake
    # four, perishable at seven and rental at eight -- which means the agent
    # only ever meets them in runs it is also losing quickly for unrelated
    # reasons, and gets a handful of antes a run to learn three mechanics it
    # has never seen. Turning them on at stake one puts them in front of a
    # policy that survives long enough to feel what they do.
    #
    # It moves the RNG: poll_stickers only spends its rental draw when rentals
    # are enabled, so a run under this flag is not the run that seed produces
    # in the real game. Evaluation therefore leaves it off.
    all_stickers: bool = False
    # Endless: keep playing past the ante-eight boss instead of ending there.
    # The rest of the game already supports it and always did -- blinds.py has
    # the post-ante-eight chip formula the real game switches to, and
    # boss_data picks a finisher every eighth ante rather than only at eight.
    # This flag is only about whether the run *stops*.
    endless: bool = False
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
            self.full_deck = standard_deck(
                no_faces=config.get("remove_faces", False),
                erratic=self.rng if config.get("randomize_rank_suit") else None)
        self._apply_deck_config(config)
        # The order the game starts a run in: the boss, then the voucher, then
        # the two skip tags. Every one of them draws, so the order is part of
        # the seed.
        # G:start_run's own deck:shuffle(), under the bare pool name. Almost
        # invisible, because pseudoshuffle sorts by id before it shuffles, so
        # the round-start shuffle washes this order out and every dealt hand
        # matches without it.
        #
        # It shows when something deals *before* the first round: a Charm Tag
        # taken off the opening blind select opens an Arcana pack, and that
        # pack deals a hand from the deck as it stands. With no draw pile the
        # simulator dealt nothing at all, and the pack had no cards to target.
        self.draw_pile = list(self.full_deck)
        self.draw_pile.sort(key=lambda card: card.uid)
        self.rng.shuffle(self.draw_pile, "shuffle")
        self._roll_boss()
        self._roll_voucher()
        self._roll_ante_tags()
        self._reset_round_cards()
        self._reroll_todo_hands()
        # The counters, not the cards: start_run puts round_resets on the HUD
        # (game.lua:2381-2382) before anything is dealt. A recording's first
        # snapshot reads hands_left 4 and discards_left 3 with an empty hand;
        # waiting until the round starts reported nought of each.
        self.hands_left, self.discards_left = self._round_allowance(False)
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
            self._move_joker_counters(joker, arriving=False)
            self.log(f"{joker.name} destroyed{f' ({reason})' if reason else ''}")

    def slice_joker(self, victim: JokerInstance, reason: str) -> None:
        """Madness or Ceremonial Dagger taking a joker as the blind is set.

        The game only marks it -- `getting_sliced = true` (card.lua:2512,
        2568) -- and dissolves it in an event, so it keeps its place and its
        slot, doing nothing, until the setting_blind pass is over. See
        _setting_blind. Outside the pass there is nothing to wait for.
        """
        if self.blind_select_events is None:
            self.destroy_joker(victim, reason)
            return
        self.getting_sliced.append((victim, reason))

    def is_getting_sliced(self, joker: JokerInstance) -> bool:
        return any(victim is joker for victim, _ in self.getting_sliced)

    def after_setting_blind(self, event) -> None:
        """Run what a setting_blind effect leaves to an event, once the pass ends.

        Riff-raff's jokers (card.lua:2532-2543): no joker later in the row
        sees them. Outside the pass, at once.
        """
        if self.blind_select_events is None:
            event()
        else:
            self.blind_select_events.append(event)

    def remove_card(self, card: Card, shattered: bool = False) -> None:
        """Take a card out of the run, and tell what feeds on that.

        Every destruction comes through here -- a glass card shattering, a
        Hanged Man, an Immolate, a Familiar clearing space -- so this is the
        one place Canio and Glass Joker need to be told, the same way
        destroy_joker is the one place a joker can leave.

        `shattered` is not "was it a glass card". See note_cards_destroyed.
        """
        gone = False
        for pile in (self.full_deck, self.draw_pile, self.hand, self.discard_pile):
            if card in pile:
                pile.remove(card)
                gone = True
        if gone:
            self.note_cards_destroyed([card], [card] if shattered else [])

    def add_card(self, card: Card) -> None:
        """A card joins the deck.

        Note what this does *not* do: it does not stamp `card.uid`. A card
        gets its id where it is built, because that is where the game gets
        one -- `Card:init` is the only place `G.sort_id` is incremented, and
        it runs at construction:

            G.sort_id = (G.sort_id or 0) + 1
            self.sort_id = G.sort_id

        A booster builds every one of its cards in a single loop the moment
        it opens (card.lua:1740-1780), so slot one is older than slot four
        however the player picks them. Stamping the id here made it an
        acquisition order instead and reversed exactly that pair -- and
        because `pseudoshuffle` sorts by id before it shuffles, a reversed
        pair does not merely mislabel two cards, it deals a different deck.
        Recording 10 stopped on that.
        """
        # CardArea:emplace puts a card at the *front* of a deck, which is its
        # bottom -- drawing takes from the back. A card added mid-round is
        # therefore the last one you will see, not the next.
        self.full_deck.append(card)
        self.draw_pile.insert(0, card)
        self.note_card_created(card)

    def add_card_to_hand(self, card: Card) -> None:
        """A card made straight into the hand -- Cryptid, Certificate, Grim.

        It joins the deck as well, so it comes round again in later rounds,
        and it counts as a card added: playing_card_joker_effects fires for
        every playing card the run builds, wherever it lands.
        """
        self.full_deck.append(card)
        self.hand.append(card)
        self.note_card_created(card)

    def set_enhancement(self, card: Card, enhancement: Enhancement) -> None:
        """Change a card's enhancement, and lose what that costs.

        Card:set_ability rebuilds the whole ability table from the new centre
        and carries exactly two things across: perma_bonus, so a Hiker's chips
        survive, and forced_selection, so Cerulean Bell keeps its grip.
        Everything else is rebuilt -- including played_this_ante, which is
        what The Pillar debuffs.

        So changing an *enhancement* launders a card that has already been
        played this ante, and changing its suit, rank, edition or seal does
        not: those write self.base, self.edition and self.seal and never
        touch ability at all. It is a real difference and an easy one to have
        backwards.
        """
        card.enhancement = enhancement
        card.played_this_ante = False

    def note_cards_destroyed(self, cards: list, shattered: list = ()) -> None:
        """Tell the jokers that feed on cards leaving the deck.

        Canio counts the face cards among `cards`, whatever destroyed them.
        Glass Joker counts `shattered`, which is narrower than "the glass
        cards among them" in a way that is worth spelling out, because it
        decides real money and it is not what the card text suggests.

        The game marks a destroyed Glass Card `shattered` and everything else
        `destroyed`, then hands the jokers the list. But the two are not
        written at the same moment. Scoring and discarding set the flag inline
        and notify straight after, so the joker sees it. The tarots that
        destroy -- Hanged Man, Familiar, Grim, Incantation, Immolate -- queue
        the shatter as an animation event and notify *first*, so the flag is
        still unset when Glass Joker looks, and it is paid nothing.

        That is an accident of animation order rather than a rule, but it is
        the behaviour, and it is why The Hanged Man has a second, separate
        handler of its own (see _hanged_man) while Familiar and the rest have
        none. Verified against the engine: a Familiar eating a glass card
        moves Glass Joker by X0.00 and Canio, if it was a face, by X1.00.
        """
        if not cards:
            return
        for joker, answer in self.calculating_hooks("on_cards_destroyed"):
            answer(joker, list(cards), self)
        if shattered:
            for joker, answer in self.calculating_hooks("on_glass_shattered"):
                answer(joker, list(shattered), self)

    def note_card_created(self, card: Card) -> None:
        """Tell the jokers that count cards added that one has been.

        Three effects built cards straight into the deck and the hand without
        going through here, so Hologram -- X0.25 for every playing card added
        -- undercounted by however many Cryptid copies and Certificate cards
        a run made. The deck size stayed right, which is what made it hard to
        see: Hologram counts *additions*, not cards.
        """
        for joker in self.calculating_jokers():
            if joker.name == "Hologram":
                joker.counter += 0.25

    def hold_consumable(self, spec: ConsumableSpec | ConsumableInstance,
                        edition: Edition = Edition.NONE) -> ConsumableInstance:
        """Wrap a registry entry as a card the run is actually holding.

        Passing an instance back through is fine and returns it unchanged, so
        callers that already have one -- Perkeo copying a held card -- do not
        have to care which they were given.
        """
        if isinstance(spec, ConsumableInstance):
            return spec
        return ConsumableInstance(spec, edition)

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
        showman = any(j.spec.allows_duplicates for j in self.active_jokers)
        # The game tests for a free slot *before* it creates the card, so a
        # full row of consumables costs nothing at all. Drawing and then
        # dropping the card, which is what this did, spends a roll the game
        # never spends and puts every later draw from that pool one place
        # along -- which is how a purple seal handed over Strength where the
        # run was given the Wheel of Fortune.
        room = self.consumable_slots - len(self.consumables)
        # Each card made blanks itself from the pool the next one draws from
        # -- a card marks its centre used the moment it is built. The Emperor
        # makes two Tarots and they cannot be the same Tarot; drawing both
        # against the pool as it stood at the start gave the run two copies
        # of one card.
        made = set(self.seen_centers)
        out = []
        for _ in range(max(0, min(count, room))):
            key = shop_pool.draw_consumable(
                self.rng, card_set, self.ante, played_hands=played,
                seen=made, showman=showman, append=append)
            if not showman:
                made.add(key)
            out.append(cons.REGISTRY[shop_pool.NAME_BY_CONSUMABLE_KEY[key]])
        return out

    def add_consumables(self, specs: list,
                        edition: Edition = Edition.NONE) -> None:
        """Bank registry entries as held cards, as far as the row will take.

        `edition` is for the one thing that makes a copy rather than a card:
        Perkeo's is Negative, and a Negative one does not use a slot.
        """
        for spec in specs:
            if len(self.consumables) < self.consumable_slots:
                self.consumables.append(self.hold_consumable(spec, edition))

    def add_random_joker(self, source: str = "", rarity: Rarity | None = None,
                         legendary: bool = False, append: str = "",
                         room_checked: bool = False) -> None:
        """A joker from the game's own pool, not from a list of every joker.

        `append` is the key_append the thing creating it uses -- "jud" for
        Judgement, "sou" for The Soul, "wra" for Wraith -- and it names the
        stream, so getting it wrong draws the right joker from the wrong
        place. A forced rarity skips the rarity roll entirely, which is how
        Wraith is always rare and never legendary.

        The joker polls an edition too, under the same append:
        create_card ends every joker with
        `poll_edition('edi'..(key_append or '')..ante)`
        (common_events.lua:2149), whatever area it was made for. Nothing here
        polled one, so a Polychrome Red Card out of a Riff-Raff arrived plain,
        and a Judgement, a Soul, a Wraith or a Top-up Tag never made a Foil,
        a Holographic or a Negative. The append is kept for a legendary here:
        get_current_pool drops it from the *pool* key, create_card does not
        drop it from the edition key.
        """
        # `room_checked` is for a creator that counted the room itself and
        # emplaces whatever the row holds by then: Riff-raff, whose count was
        # fixed mid-pass while a Dagger's victim still sat in its slot.
        if not room_checked and len(self.jokers) >= self.joker_slots:
            return
        key = shop_pool.draw_joker(
            self.rng, self.ante, seen_jokers=self.seen_centers,
            rarity=4 if legendary else _RARITY_INDEX.get(rarity),
            append=append,
            owned_enhancements={"m_%s" % c.enhancement.value
                                for c in self.full_deck},
            showman=any(j.spec.allows_duplicates for j in self.active_jokers),
            pool_flags=self.pool_flags)
        spec = JOKER_REGISTRY[shop_pool.NAME_BY_JOKER_KEY[key]]
        edition = _EDITION_BY_NAME[shop_pool.poll_edition(
            self.rng, "edi%s%d" % (append, self.ante),
            edition_rate=self.edition_rate)]
        self.gain_joker(self._made_joker(JokerInstance(spec, edition=edition)))
        self.log(f"{source}: gained {spec.name}")

    def can_use_consumable(self, spec: ConsumableSpec,
                           targets: tuple = ()) -> bool:
        """Card:can_use_consumeable, branch for branch.

        Only the count of selected cards was being checked, so the simulator
        offered a good deal the game refuses -- a Judgement with no room for
        the joker, a Sigil in the shop, an Aura onto a card that is already
        polychrome. For a policy that is not a harmless extra option: it is a
        move that appears legal, gets taken, and does nothing.

        The branch that matters most is the last one. Anything that selects
        cards is gated on `G.STATE == SELECTING_HAND or one of the three pack
        states`, so a targeting Tarot cannot be used in a shop or on the
        cash-out screen at all -- only in a round, or out of an Arcana or
        Spectral pack, which is why those two deal a hand when they open.
        """
        # A hand to work on: in a round, or inside a pack that dealt one.
        hand_dealt = self.phase in (Phase.PLAYING, Phase.PACK)
        plain_jokers = [j for j in self.jokers if j.edition is Edition.NONE]

        if spec.name in FREE_JOKER_NEEDED:
            return len(self.jokers) < self.joker_slots
        if spec.name in FREE_CONSUMABLE_NEEDED:
            # `or self.area == G.consumeables`: using it frees the slot it is
            # sitting in, so holding it is always enough. Only a copy taken
            # straight out of a pack can be blocked.
            # Compared by centre rather than by card, so this answers the
            # same whether it was handed the registry entry or the held copy.
            centre = getattr(spec, "spec", spec)
            room = (len(self.consumables) < self.consumable_slots
                    or any(c.spec is centre for c in self.consumables))
            if spec.name != "The Fool":
                return room
            return (room and bool(self.last_tarot_planet)
                    and self.last_tarot_planet != "c_fool")
        if spec.name in PLAIN_JOKER_NEEDED:
            return bool(plain_jokers)
        if spec.name == "Ankh":
            # Deliberately not a free slot -- that is check_use, and the
            # disagreement between the two is the Ankh bug. See refuses_use.
            return bool(self.jokers) and self.joker_slots > 1
        if spec.name == "Aura":
            return (hand_dealt and len(targets) == 1
                    and targets[0].edition is Edition.NONE)
        if spec.name in SPARE_CARD_NEEDED:
            # They destroy a card at random, and the game will not let the
            # hand go empty that way.
            return hand_dealt and len(self.hand) > 1
        if spec.targets:
            return hand_dealt and spec.accepts(len(targets))
        return True

    def refuses_use(self, spec: ConsumableSpec) -> bool:
        """Card:check_use -- the one card the game refuses at the last moment.

        Ankh is the only entry in it, and it disagrees with the check that
        enables the button: can_use_consumeable asks only for a joker and a
        limit above one, while this asks for a *free slot*. So with a full
        row the button is live, you press it, and the game says No Room.
        """
        return (spec.name == "Ankh"
                and len(self.jokers) >= self.joker_slots)

    def _usable_now(self, spec: ConsumableSpec, targets: tuple = ()) -> bool:
        """Whether pressing use actually uses the card: the button, then check_use.

        G.FUNCS.use_card (button_callbacks.lua:2163-2169) calls check_use
        before anything else and, when it refuses, restores the button and
        returns -- the card stays in its slot or in the pack, and no pack
        choice is spent. So a use from a slot or a pick from a pack that
        check_use refuses is not a move at all: offering it had the policy take
        an Ankh into a full row and the engine do nothing (seeds 3FBLDRVK,
        9977JY5C). Buy-and-use is different, because it has already paid and
        removed the card -- that path keeps the loose gate and loses the card.
        """
        return (self.can_use_consumable(spec, targets)
                and not self.refuses_use(spec))

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
        self.add_money(-self.slot_price(slot), f"bought {slot.label}")
        spec = slot.consumable
        if spec is None:                     # a joker: buy-and-use is a buy
            if slot.joker is not None:
                self.gain_joker(slot.joker)
            return
        if self.refuses_use(spec):
            self.log(f"{spec.name}: No Room -- bought, used by nothing, lost")
            return
        self.use_consumable(spec, [])

    def use_consumable(self, spec: ConsumableSpec | ConsumableInstance,
                       targets: list[Card] | None = None,
                       from_pack: bool = False) -> None:
        """Apply a consumable and remember it if it was a Tarot or a Planet.

        Takes either the registry entry or a held card: everything read here
        is forwarded by ConsumableInstance, so a pack pick and a slot use go
        down the same path -- except for `from_pack`, which some effects
        need because the game does not treat the two alike. See
        `consumables._wheel_of_fortune`.

        The remembering is what The Fool reads, and it was never written --
        the field existed and nothing ever set it, so The Fool copied nothing
        for the whole of a run. The game records it after the effect has run,
        which is why using The Fool leaves The Fool as the last one used and
        the game refuses to let you use it twice in a row.
        """
        if spec.apply is not None:
            # An effect that needs to know reads it off the game; nothing
            # else changes, so the two paths stay one path.
            self.using_from_pack = from_pack
            self.using_key = shop_pool.KEY_BY_CONSUMABLE_NAME.get(spec.name, "")
            self.playing_tarot = True
            try:
                spec.apply(self, list(targets or []))
            finally:
                self.using_from_pack = False
                self.using_key = ""
                self.playing_tarot = False
        self.log(f"Used {spec.name}")
        if spec.kind in (ConsumableKind.TAROT, ConsumableKind.PLANET):
            self.last_tarot_planet = shop_pool.KEY_BY_CONSUMABLE_NAME.get(
                spec.name, "")

        # G.GAME.consumeable_usage_total. Both counters were declared on the
        # run and never written, so Fortune Teller -- "+1 Mult per Tarot used
        # this run" -- added nothing for a whole run, and Constellation grew
        # on nothing.
        if spec.kind is ConsumableKind.TAROT:
            self.tarots_used += 1
        elif spec.kind is ConsumableKind.PLANET:
            self.planets_used += 1
            # G.GAME.consumeable_usage is keyed by centre, and Satellite pays
            # a dollar for each key whose set is Planet (card.lua:1667-1674):
            # one per distinct Planet. It was declared and never written, so
            # Satellite paid nothing for a whole run.
            self.unique_planets.add(spec.name)
            for joker in self.calculating_jokers():
                if joker.name == "Constellation":
                    joker.counter += 0.1

    def add_tag_by_key(self, key: str) -> None:
        """Hand the run a tag the game names by key, if it is one we model.

        A Double Tag copies whatever arrives next -- anything but another
        Double Tag -- and is spent doing it. That is `tag_add`, the moment a
        tag joins the list rather than the moment it fires, so everything has
        to come through here.
        """
        tag = TAG_BY_KEY.get(key)
        if tag is None:
            return

        # add_tag walks the whole tag list firing `tag_add` and never breaks,
        # so *every* Double Tag held copies the incoming one -- not just the
        # first. Each sets triggered before its copy is queued, so the copies
        # do not cascade into each other. Consuming one Double at a time was
        # the difference between one spare and a row of them, and a row is
        # reachable: sell a stack of Diet Colas, or play the Anaglyph Deck,
        # which hands over a Double every time a boss falls.
        doubles = self.tags.count(Tag.DOUBLE) if tag is not Tag.DOUBLE else 0
        for _ in range(doubles):
            self.tags.remove(Tag.DOUBLE)

        self.tags.append(tag)
        self.log("gained %s" % tag.value)
        for _ in range(doubles):
            self.tags.append(tag)
            self.log("Double Tag: and another %s" % tag.value)

    def _fire_immediate_tags(self) -> None:
        """The five that pay the instant a blind is skipped.

        Three of them read a run total rather than anything about the round
        just skipped: Handy counts every hand played this run, Garbage every
        discard left unspent at the end of a round, and Speed every blind
        skipped -- including the one being skipped now, because skip_blind
        increments the counter before it hands the tag over.
        """
        for tag in list(self.tags):
            if tag not in IMMEDIATE_TAGS:
                continue
            self.tags.remove(tag)
            if tag is Tag.HANDY:
                self.add_money(self.hands_played, "Handy Tag")
            elif tag is Tag.GARBAGE:
                self.add_money(self.unused_discards, "Garbage Tag")
            elif tag is Tag.SPEED:
                self.add_money(5 * self.blinds_skipped, "Speed Tag")
            elif tag is Tag.TOP_UP:
                # Two Common jokers, under append "top". The game passes a
                # forced rarity poll of 0 rather than rolling one, so no draw
                # is spent deciding they are Common, and it re-checks the room
                # before each -- a single free slot yields one joker, not two.
                for _ in range(2):
                    if len(self.jokers) >= self.joker_slots:
                        break
                    self.add_random_joker("Top-up Tag", rarity=Rarity.COMMON,
                                          append="top")
            elif tag is Tag.ORBITAL:
                hand = self._orbital_hand()
                self.hand_levels.level_up(hand, 3)
                self.log("Orbital Tag: %s up three levels" % hand.label)

    def _orbital_hand(self) -> HandType:
        """The hand an Orbital Tag names.

        Drawn from the *visible* hands, so nine of them until a secret hand
        has been made, and remembered per ante and blind -- which is what
        lets a Double Tag's copy level the same hand as the original. Same
        `pairs(G.GAME.hands)` pool as To Do List, with the same caveat about
        the engine's own order not being reproducible; see hands.py.
        """
        slot = (self.ante, self.blind_index)
        if slot not in self.orbital_choices:
            self.orbital_choices[slot] = self.rng.random_element(
                self.visible_hands, "orbital")
        return self.orbital_choices[slot]

    def note_card_sold(self) -> None:
        """Tell the jokers that count sales that one has happened.

        Campfire gains X0.25 for every card sold, of any kind, and resets to
        X1 when a Boss Blind is beaten. The counter it reads was sitting at
        its starting value for whole runs.
        """
        self.cards_sold += 1
        for joker in self.calculating_jokers():
            if joker.name == "Campfire":
                joker.counter += 0.25

    def gain_joker(self, joker: JokerInstance) -> None:
        """Put a joker in the row, stamped with when it arrived.

        The game records hands_played_at_create on every card it builds, and
        the jokers that count hands measure from there rather than from the
        start of the run -- Loyalty Card's X4 lands on the sixth hand since it
        was bought, not the sixth of the run. Nothing was stamping it, so
        every joker behaved as though it had been there from the beginning and
        Loyalty Card fired on the wrong hand for the whole game.
        """
        joker.hands_at_create = self.hands_played
        # No age is stamped here: the joker has had one since it was built
        # (JokerInstance.uid). Restamping on arrival made the age a purchase
        # order, so Madness, the Wheel of Fortune, Ectoplasm and Hex drew the
        # wrong joker whenever a shop was bought out of slot order.
        self.jokers.append(joker)
        # Chaos the Clown hands over its free reroll the moment it joins the
        # row -- Card:add_to_deck does it -- so buying one in a shop you are
        # standing in gives you a reroll in that shop. Topping up only when
        # the shop opens misses exactly that, which is when anyone would buy
        # it.
        self._move_joker_counters(joker, arriving=True)

    def _move_joker_counters(self, joker: JokerInstance,
                             arriving: bool) -> None:
        """The run-level counters Card:add_to_deck and remove_from_deck move.

        Both are immediate rather than next-round, which is the whole point: a
        Chaos the Clown bought in a shop hands over its reroll in that shop,
        and a Merry Andy hands over its three discards the moment it is
        bought. The round allowance counts these jokers as well, so leaving
        them out here did not lose the discards -- it delayed them by a round,
        which is worse, because it looks right everywhere except the shop you
        bought it in.

            if self.ability.d_size > 0 then
                G.GAME.round_resets.discards = ... + self.ability.d_size
                ease_discard(self.ability.d_size)
            end

        Recording 8 stopped on exactly that at step 206 of 443: five discards
        recorded against two simulated, one action after a Merry Andy was
        bought.
        """
        # Both halves open on added_to_deck (card.lua:566, 646), and a debuff
        # has already run remove_from_deck(true): selling a joker Crimson Heart
        # holds gives nothing back a second time. set_joker_debuff clears the
        # flag before it puts a joker back.
        if joker.debuffed:
            return
        rerolls = joker.spec.free_rerolls
        if rerolls and self.shop is not None:
            self.shop.free_rerolls = (
                self.shop.free_rerolls + rerolls if arriving
                else max(0, self.shop.free_rerolls - rerolls))
        # Strictly `> 0`, as the game has it: a negative d_size takes nothing
        # away on arrival. Clamped on the way down because ease_discard is
        # `mod = math.max(-G.GAME.current_round.discards_left, mod)`.
        discards = joker.spec.extra_discards
        if discards > 0:
            self.discards_left = (self.discards_left + discards if arriving
                                  else max(0, self.discards_left - discards))
        # change_size for the joker's hand size: h_size, Turtle Bean,
        # Troubadour and Stuntman on the way in (card.lua:587, 606, 624, 628)
        # and the reverse on the way out (card.lua:649, 663, 681, 685).
        # Nothing for a debuffed joker -- its debuff already ran
        # remove_from_deck(true), and both are guarded by added_to_deck --
        # which is also why hand_size reads active_jokers.
        if not joker.debuffed:
            size = (int(joker.counter) if joker.spec.hand_size_from_counter
                    else joker.spec.hand_size)
            self._hand_size_changed(size if arriving else -size)

    def _hand_size_changed(self, delta: int) -> None:
        """Deal into a hand size that has just grown, as change_size does.

        hand_size is derived from the row, so the limit itself has already
        moved; what the game also does is deal. CardArea:change_size
        (cardarea.lua:94-111):

            if delta > 0 and self.config.real_card_limit > 1 and self == G.hand
               and self.cards[1] and (G.STATE == G.STATES.DRAW_TO_HAND
                                      or G.STATE == G.STATES.SELECTING_HAND)
            then for i=1, math.abs(delta) do draw_card(G.deck, G.hand, ...)
                     ... self:sort() ... end end

        |delta| cards off the top of the deck, not a top-up to the limit: a
        hand already over it still gets them. A decrease only lowers the limit
        and discards nothing. U2EBFAQ2 stopped on this at decision 186: the
        policy sold Stuntman while selecting a hand, the game held 10 cards
        and the simulator 8.

        Not during a consumable, whose G.STATE is PLAY_TAROT until after the
        change_size event has run (see playing_tarot): on the engine a Hex
        that destroys a Stuntman and a Judgement that makes a Juggler both
        raise the limit and deal nothing.
        """
        if delta <= 0 or self.playing_tarot:
            return
        if self.phase is not Phase.PLAYING or not self.hand:
            return
        # real_card_limit is unfloored, but after an increase it is above one
        # exactly when the floored hand_size is.
        if self.hand_size <= 1:
            return
        self._draw_cards(delta)

    def set_joker_debuff(self, joker: JokerInstance, debuff: bool) -> None:
        """Card:set_debuff on a joker (card.lua:526-538).

        A perished joker stays debuffed whatever is asked. Otherwise a change
        takes the joker out of the deck or puts it back -- remove_from_deck(true)
        or add_to_deck(true) -- so its hand size, discards and rerolls go and
        come with it, and a hand size given back is dealt into
        (_hand_size_changed). Crimson Heart's pick and the release when the
        blind is disabled or beaten both come through here.
        """
        if joker.perishable and joker.perish_tally <= 0:
            joker.debuffed = True
            return
        if joker.debuffed == debuff:
            return
        if debuff:
            # Out while the flag is still clear: _move_joker_counters leaves
            # a joker that is already debuffed alone.
            self._move_joker_counters(joker, arriving=False)
            joker.debuffed = True
        else:
            joker.debuffed = False
            self._move_joker_counters(joker, arriving=True)

    def add_joker_copy(self, joker: JokerInstance, source: str = "") -> None:
        """A copy of a joker already held, if the row has room for it."""
        if len(self.jokers) >= self.joker_slots:
            return
        self.copy_joker(joker, source)

    def copy_joker(self, joker: JokerInstance,
                   source: str = "") -> JokerInstance:
        """copy_card, add_to_deck and emplace (common_events.lua:2156-2181).

        Everything in the ability table comes across -- counters, stickers,
        and hands_played_at_create too: set_ability stamps the new card's own
        (card.lua:337) and the loop over other.ability writes the original's
        over it, so a copied Loyalty Card keeps the original's cycle.

        Except a Negative. Both callers pass strip_edition for one (Ankh at
        card.lua:1445, Invisible Joker at 2384), which skips set_edition, so
        the copy has no edition at all rather than a free slot of its own.

        It is a new card all the same: Card:init gives it the next sort_id
        (a deepcopy would keep the original's age), and set_ability runs
        before the ability table is copied over, so a To Do List copy spends
        its creation draw and then keeps the original's hand.
        """
        from .cards import next_sort_id

        clone = copy.deepcopy(joker)
        clone.uid = next_sort_id()
        self._made_joker(clone)
        clone.named_hand = joker.named_hand
        if joker.edition is Edition.NEGATIVE:
            clone.edition = Edition.NONE
        self.gain_joker(clone)
        clone.hands_at_create = joker.hands_at_create
        self.log(f"{source}: copied {joker.name}")
        return clone

    # ------------------------------------------------------------------
    # derived state
    # ------------------------------------------------------------------

    @property
    def boss(self) -> BossEffect | None:
        if self.blind is None or self.blind.kind is not BlindKind.BOSS:
            return None
        if self.blind.disabled:
            return None
        # Chicot disables the boss from setting_blind (card.lua:2492) and
        # add_to_deck (card.lua:596), neither of which a debuffed one runs.
        if any(j.name == "Chicot" for j in self.active_jokers):
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
            # change_size(1) deals its card (blind.lua:387), and
            # draw_from_deck_to_hand(1) another (blind.lua:389). Both are the
            # delta rather than a top-up: on the engine eight cards held under
            # a limit of seven became ten, where topping up gave nine.
            self._hand_size_changed(-boss.hand_size_delta)
            self._draw_cards(-boss.hand_size_delta)

        # And every joker is asked debuff_card again (blind.lua:410-412),
        # which a disabled Crimson Heart no longer answers (blind.lua:647-651):
        # the joker it held comes back, and deals its card if it is a Juggler.
        for joker in list(self.jokers):
            self.set_joker_debuff(joker, False)

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

    def room_for_joker(self, joker) -> bool:
        """Whether this particular joker can be taken, full row or not.

        `joker_slots` counts the Negatives already held, because add_to_deck
        raises the limit as one arrives. The one being offered has not
        arrived, so it has to be asked about separately -- a Negative needs no
        slot, and a full row does not stop it.

        The game says this in two places and says the same thing in both.
        button_callbacks.lua:2112, can_select_card, which decides whether a
        pack card is given a use_card button at all:

            set ~= 'Joker' or (edition and edition.negative)
                           or #G.jokers.cards < card_limit

        and the shop's can_buy at 2396, which adds one to the limit when the
        card is Negative. Without this the simulator refused a Negative out of
        a Buffoon pack and refused to buy one from the shop, both of which the
        game allows -- and both silently, as a mask that was simply narrower
        than the real one.

        Creation is deliberately not routed through here. A joker made by
        Riff-raff or a Judgement is checked for room *before* it exists
        (card.lua:2529, 3967), so its edition cannot be consulted and a full
        row stops it whatever it would have rolled.
        """
        return (len(self.jokers) < self.joker_slots
                or joker.edition is Edition.NEGATIVE)

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

    def calculating_jokers(self):
        """The row as calculate_joker walks it: a debuffed joker is skipped.

        Card:calculate_joker opens `if self.debuff then return nil end`
        (card.lua:2291-2292), so a debuffed joker answers no context at all --
        not a discard, not the end of the round, not a reroll, a sale, a pack
        opened or skipped, a card added or destroyed, a consumable used. The
        joker hooks outside the scoring of a hand go through calculating_hooks,
        which walks the row the same way and resolves copies too; what is left
        here are the loops keyed on a joker's own name -- Hologram,
        Constellation, Campfire, To Do List -- whose branches all say `not
        context.blueprint`. Scoring goes through scoring.calculating_specs.
        RRT5KY7W stopped on the discard at decision 188: a Ramen Crimson Heart
        held went on losing X0.01 a card, X1.8 here against the game's X1.85.

        Over a copy of the row, as these loops always were, with the debuff
        read as each joker is reached.
        """
        for joker in list(self.jokers):
            if not joker.debuffed:
                yield joker

    # Which copies answer a context outside the scoring of a hand. A Blueprint
    # or a Brainstorm hands every context to the joker it copies
    # (card.lua:2304-2333), and the copy runs unless the copied joker's branch
    # says `not context.blueprint`. These are the jokers whose branch does, by
    # hook; None shuts the whole context to copies.
    _NOT_COPIED = {
        # discard: Ramen 2757, Yorick 2788, Castle 2816, Hit the Road 2837,
        # Green Joker 2846. Mail-In Rebate 2825 and Faceless Joker 2858 copy.
        "discarded": frozenset(
            {"Ramen", "Yorick", "Castle", "Hit the Road", "Green Joker"}),
        # Trading Card's discard branch, 2802. Burnt Joker's pre_discard, 2749,
        # copies.
        "on_first_discard": frozenset({"Trading Card"}),
        # Sixth Sense lives under `context.destroying_card and not
        # context.blueprint` (2603); DNA's `before` branch, 3501, copies.
        "before_hand": frozenset({"Sixth Sense"}),
        # joker_main: Vagabond 3743, Superposition 3762, Seance 3787.
        "after_hand": frozenset(),
        # first_hand_drawn: Certificate 2463.
        "on_round_start": frozenset(),
        # end_of_round's joker branch is `elseif not context.blueprint` (2888),
        # from Campfire through Mr. Bones.
        "round_end": None,
        "on_reroll": frozenset({"Flash Card"}),        # reroll_shop 2404
        "on_pack_skip": frozenset({"Red Card"}),       # skipping_booster 2442
        "on_pack_open": frozenset(),                   # Hallucination 2336
        "on_shop_end": frozenset(),                    # Perkeo 2413
        # remove_playing_cards and cards_destroyed: Caino 2623, 2673; Glass
        # Joker 2647, 2687, and on The Hanged Man's using_consumeable, 2709.
        "on_cards_destroyed": frozenset({"Canio"}),
        "on_glass_shattered": frozenset({"Glass Joker"}),
        # selling_self: Luchador 2355 and Diet Cola 2361 copy; Invisible Joker
        # 2371 does not.
        "on_sell": frozenset({"Invisible Joker"}),
    }

    def calculating_hooks(self, hook: str):
        """(joker, hook) for each joker that answers `hook`, copies included.

        calculating_jokers ran each joker's own hook, so outside scoring a
        Blueprint or a Brainstorm answered nothing at all: a Blueprint on a
        Mail-In Rebate paid once, on a Perkeo copied nothing, on a
        Hallucination rolled nothing. The game calls the copied joker's own
        calculate_joker (card.lua:2313, 2327), so this yields the *copied*
        joker, whose state the hook then reads -- which is why every hook that
        grows its joker's counter is one the game shuts to copies, and why a
        copy is dropped here rather than run on the copier. Resolved over the
        whole row by scoring.effective_specs, so a copier beside a debuffed
        joker copies nothing; the copier's own debuff is read as it is reached.
        """
        from .scoring import effective_specs

        row = list(self.jokers)
        shut = self._NOT_COPIED[hook]
        for owner, (spec, source) in zip(row, effective_specs(row)):
            if owner.debuffed:
                continue
            answer = getattr(spec, hook)
            if answer is None:
                continue
            if source is not owner and (shut is None or spec.name in shut):
                continue
            yield source, answer

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
        # The consumable in use is in no area but still exists until its
        # effect has run -- its used_jokers entry is cleared by Card:remove
        # (card.lua:4741-4749), which comes after. Without this The Emperor
        # drew from a pool with itself back in it.
        if self.using_key:
            keys.add(self.using_key)
        keys.discard(None)
        return keys

    @property
    def bankrupt_at(self) -> int:
        """How far into debt the run may go. Credit Card lowers the floor."""
        return -sum(j.spec.debt_limit for j in self.active_jokers)

    @property
    def spendable(self) -> int:
        """What the run may lay out, which is not what it holds.

        Every affordability test in the game is `cost > dollars -
        bankrupt_at`, and Credit Card exists only to move bankrupt_at. Testing
        against money alone -- which is what every check here did -- made
        Credit Card an entirely inert purchase, and it is a joker whose whole
        text is the twenty dollars of credit. They stack, too: measured on the
        engine at $0, one allows a $20 buy and refuses $21, two allow $40 and
        refuse $41.
        """
        return self.money - self.bankrupt_at

    def affords(self, cost: int) -> bool:
        """The game's test, including its free-item escape.

        `(cost > dollars - bankrupt_at) and (cost > 0)` -- so something free
        is always takeable, even by a run already past its floor.
        """
        return cost <= 0 or cost <= self.spendable

    @property
    def can_reroll_boss(self) -> bool:
        """Whether the Director's Cut / Retcon button is live.

        Without either voucher there is no button at all. Director's Cut
        allows one reroll an ante -- boss_rerolled remembers it, and
        reset_blinds clears it when a boss falls. Retcon allows any number.
        Either way you must be able to afford the ten dollars, measured
        against the debt floor rather than against zero.
        """
        owned = {v.key for v in self.vouchers}
        if "v_retcon" in owned:
            pass
        elif "v_directors_cut" in owned and not self.boss_rerolled:
            pass
        else:
            return False
        return (self.money - self.bankrupt_at) - BOSS_REROLL_COST >= 0

    @property
    def sticker_rules(self) -> dict:
        """Which stickers the stake lets the shop put on a joker."""
        if self.all_stickers:
            return {"eternals": True, "perishables": True, "rentals": True}
        return {"eternals": self.stake >= 4, "perishables": self.stake >= 7,
                "rentals": self.stake >= 8}

    def _apply_stickers(self, joker: JokerInstance,
                        in_pack: bool = False) -> None:
        """Poll a shop joker's stickers onto it.

        The first poll happens whether or not any sticker is enabled, so it
        is made on every stake -- see shop_pool.poll_stickers. What a rental
        then costs is `slot_price`'s business: set_cost puts it at a dollar
        after the discount, however expensive the joker is, which is six
        dollars a recording said the run still had.
        """
        stickers = shop_pool.poll_stickers(self.rng, self.ante, in_pack,
                                           **self.sticker_rules)
        # The centre gets a veto, and it is not a preference: set_eternal and
        # set_perishable simply drop the sticker when the joker refuses it
        # (card.lua:506, 513). Ride the Bus is `perishable_compat = false`,
        # and handing it one anyway debuffed it five rounds into a run the
        # game had left alone -- which is how the live differential found
        # that these flags were not modelled at all. Each also refuses the
        # other's sticker, hence the ordering below.
        eternal_ok, perishable_ok = shop_pool.takes_sticker(joker.name)
        joker.eternal = stickers["eternal"] and eternal_ok
        joker.perishable = (stickers["perishable"] and perishable_ok
                            and not joker.eternal)
        joker.rental = stickers["rental"]
        if joker.perishable:
            joker.perish_tally = PERISHABLE_ROUNDS

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
        """How many consumables the row holds.

        A Negative one does not take a slot: add_to_deck raises the limit for
        it and remove_from_deck lowers it again, the same bookkeeping a
        negative joker gets. Perkeo's whole point is that its copy is free,
        and without this it was taking a slot like any other card.
        """
        return (BASE_CONSUMABLE_SLOTS + self.extra_consumable_slots
                + sum(v.consumable_slots for v in self.vouchers)
                + sum(1 for c in self.consumables
                      if c.edition is Edition.NEGATIVE))

    @property
    def hand_size(self) -> int:
        size = self.base_hand_size
        size += sum(v.hand_size for v in self.vouchers)
        # Turtle Bean's contribution is its counter, which shrinks by one
        # every round, rather than a number fixed on the spec -- summing the
        # spec gave nothing at all, so a run holding one dealt five cards
        # fewer than the game did.
        size += sum(int(j.counter) if j.spec.hand_size_from_counter
                    else j.spec.hand_size for j in self.active_jokers)
        size += self.deck_config.get("hand_size", 0)
        size += self.temp_hand_size
        if self.boss is not None:
            size += self.boss.hand_size_delta
        # CardArea:update floors this at *zero*, not one -- math.max(0,
        # real_card_limit). Flooring it at one made the hand-size loss
        # unreachable, which matters because reaching zero is a real way to
        # end a run: Troubadour and Stuntman are -2 each, Merry Andy -1, a
        # decayed Turtle Bean another, and The Manacle one more on top.
        return max(0, size)

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
        """What a voucher or a booster on the shelf costs.

        The same `Card:set_cost` every other price goes through: a voucher and
        a pack are Cards like any other. This rounded instead, which is not
        the same thing -- a $10 voucher under Clearance Sale is
        `floor((10 + 0.5) * 0.75)` = $7 in the game and came out $8 here, and
        a live run stopped on the dollar.
        """
        return self.card_cost(base)

    @property
    def discount_percent(self) -> int:
        """G.GAME.discount_percent -- Clearance Sale and Liquidation."""
        return max([v.discount_percent for v in self.vouchers] or [0])

    def card_cost(self, base: int, edition: Edition = Edition.NONE) -> int:
        """What a card costs, exactly as Card:set_cost works it out.

        The half is the game's, not a rounding choice here:

            cost = max(1, floor((base + extra + 0.5) * (100 - discount)/100))

        so a four dollar joker under Liquidation costs two rather than the
        two-and-a-bit that rounding would give.
        """
        extra = EDITION_VALUE.get(edition, 0)
        scaled = (base + extra + 0.5) * (100 - self.discount_percent) / 100.0
        return max(1, int(scaled))

    def sell_value(self, joker: JokerInstance) -> int:
        """Half what the card costs -- and the cost includes the discount.

        A voucher that makes the shop cheaper makes selling worth less too,
        which is easy to miss because it reads like a pure gain. Reading the
        joker's list price instead paid a dollar too much for every joker a
        run with Liquidation sold, and Temperance pays out the sell value of
        every joker held, so it compounds.
        """
        cost = 1 if joker.rental else self.card_cost(joker.spec.cost,
                                                     joker.edition)
        return max(1, cost // 2) + int(joker.extra_sell_value)

    def consumable_sell_value(self, held: ConsumableSpec | ConsumableInstance
                              ) -> int:
        """What selling a held consumable pays -- Card:set_cost, as for a joker.

            self.cost = max(1, floor((base_cost + extra_cost + 0.5)
                                     * (100 - discount_percent) / 100))
            -- a Planet while Astronomer is held (find_joker, not debuffed):
            self.cost = 0                                     (card.lua:380)
            self.sell_cost = max(1, floor(self.cost/2)) + ability.extra_value

        extra_cost carries the edition (card.lua:372-373), so Perkeo's
        Negative copy of a Tarot sells for $4, and extra_value is what Gift
        Card adds every round (3000-3005). set_cost is rerun on every card
        when the discount or Astronomer changes (1917-1923, 619, 676), so this
        is worked out now rather than kept. It sold every consumable for
        `max(1, cost // 2)`: no edition, no discount, and no Gift Card money.
        """
        held = self.hold_consumable(held)
        if (held.spec.kind is ConsumableKind.PLANET
                and any(j.spec.free_planets for j in self.active_jokers)):
            cost = 0
        else:
            cost = self.card_cost(held.spec.cost, held.edition)
        return max(1, cost // 2) + int(held.extra_sell_value)

    @property
    def is_over(self) -> bool:
        return self.phase in (Phase.GAME_OVER, Phase.WON)

    # ------------------------------------------------------------------
    # blind flow
    # ------------------------------------------------------------------

    @property
    def visible_hands(self) -> list[HandType]:
        """The hands the run knows about.

        Five of a Kind, Flush House and Flush Five start `visible = false` and
        are switched on in evaluate_play the first time one is made. Anything
        choosing a poker hand at random draws from the visible ones only, so
        the difference is not cosmetic: it is a nine-entry pool rather than a
        twelve-entry one, which changes both the hand picked and where the
        stream lands afterwards.

        Returned in HANDLIST order -- see the note there on why the engine's
        own order for this is not reproducible.
        """
        return [h for h in HANDLIST
                if h not in SECRET_HANDS or self.hand_levels.plays[h] > 0]

    def _reroll_todo_hands(self) -> None:
        """To Do List names a poker hand, and picks a new one every round.

        Drawn from the visible hands it is *not* already on, so it never
        repeats itself two rounds running. The hand belongs to the joker --
        the game keeps it in ability.to_do_poker_hand -- so two To Do Lists
        name two different hands and roll separately. Holding it on the run
        instead made a second copy overwrite the first, and the field it read
        was declared and never written, so the joker paid out for whatever
        HandType happened to be None: nothing.
        """
        visible = self.visible_hands
        # An end_of_round branch (card.lua:2975): a debuffed To Do List
        # keeps its hand and takes no 'to_do' draw.
        for joker in self.calculating_jokers():
            if not joker.spec.rerolls_a_hand:
                continue
            pool = [h for h in visible if h is not joker.named_hand]
            joker.named_hand = self.rng.random_element(pool, "to_do")

    def _made_joker(self, joker: JokerInstance) -> JokerInstance:
        """A joker has just been built: what Card:set_ability does with it.

        For a To Do List that is rolling its hand (card.lua:311-322) -- from
        every visible hand, on the same 'to_do' stream as the round-end roll,
        and for every card built whether or not anyone buys it: a shop builds
        its whole shelf, a Buffoon pack its whole spread. Left to the
        round-end roll, a To Do List bought mid-ante named nothing and paid
        nothing for its first round, and each one built without its draw put
        every later roll in the run a draw out of step with the game's.
        """
        if joker.spec.rerolls_a_hand:
            joker.named_hand = self.rng.random_element(self.visible_hands,
                                                       "to_do")
        return joker

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
                self.consumables.append(
                    self.hold_consumable(cons.REGISTRY[name]))

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
                    card.set_suit(Suit.SPADES)
                elif card.suit is Suit.DIAMONDS:
                    card.set_suit(Suit.HEARTS)

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
        # No counters here. The game writes current_round.hands_left and
        # discards_left only at start_run (game.lua:2381), cash_out
        # (button_callbacks.lua:2929) and new_round (state_events.lua:296),
        # and moves them otherwise by ease_hands_played / ease_discard.
        # Leaving the shop and skipping a blind do neither, so the screen
        # shows what cash-out left. Recomputing the allowance here applied a
        # Troubadour bought in the shop a blind early -- 3 hands against the
        # game's 4 (card.lua:625 moves round_resets.hands alone).
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
        # set_blind: `if self.name == 'The Mouth' and not reset then
        # self.only_hand = false end` (blind.lua:173).
        self.mouth_only_hand = None
        # The counters first, then the jokers that react to the blind being
        # taken -- the game's order, and it matters: Burglar's whole drawback
        # is ease_discard(-current_round.discards_left), which needs a number
        # to take away. Setting the allowance afterwards handed the discards
        # straight back.
        self.hands_left, self.discards_left = self._round_allowance()
        # round_resets.reroll_cost, restored as the round begins.
        self.reroll_cost_carried = max(
            0, 5 - sum(v.reroll_discount for v in self.vouchers))

        # round_start_bonus, applied to every tag held rather than the first:
        # three Juggle Tags are nine cards, not three. The tag was mapped and
        # then read by nothing, so it had been worth zero.
        while Tag.JUGGLE in self.tags:
            self.tags.remove(Tag.JUGGLE)
            self.temp_hand_size += 3
            self.log("Juggle Tag: +3 hand size for this round")

        # set_blind leaves the blind prepped (blind.lua:94), which is what lets
        # Crimson Heart take a joker on the opening deal.
        self.blind.prepped = True

        # Selecting the blind is its own moment, before any card is dealt:
        # Marble Joker's Stone card is in the deck for the first draw, and
        # Riff-Raff's Jokers are there for the first hand.
        self._setting_blind()

        boss = self.boss
        if boss is not None and boss.shuffles_jokers and len(self.jokers) > 1:
            # Amber Acorn shuffles the joker row under its own pool name --
            # and joker order decides the order effects resolve in, so this
            # is a real change rather than a cosmetic one.
            #
            # Three times, not two, and each one sorted by card id first
            # (blind.lua:195-201): every event calls G.jokers:shuffle('aajk'),
            # CardArea:shuffle is pseudoshuffle, and pseudoshuffle sorts the
            # list by sort_id before it shuffles. So the row the player
            # dragged into is washed out by the first sort, and what decides
            # the order is the third draw from the stream. Seed QWERTYUI on
            # the headless engine reached an Amber Acorn at ante eight and the
            # two rows came out holding the same six jokers in different
            # orders; no recording had ever reached one.
            for _ in range(3):
                self.jokers.sort(key=lambda joker: joker.uid)
                self.rng.shuffle(self.jokers, "aajk")

        self.draw_pile = list(self.full_deck)
        # The game shuffles with pseudoseed("nr" .. ante) at the start of a
        # round, and draws from the end of the result. The pool is named by
        # the ante, not the round, so the two blinds of an ante draw from the
        # same stream at different points in it.
        #
        # Sorted by card id first, because pseudoshuffle does it itself:
        #
        #     if list[1] and list[1].sort_id then
        #       table.sort(list, function (a, b) ... end)
        #     end
        #
        # so the shuffle's input is never the order the deck happens to be
        # in -- it is always id order, and the ids decide the deal. A deck
        # holding a card whose id is out of step with when it was appended
        # therefore deals differently here than in the game, which is what
        # recording 10 stopped on.
        self.draw_pile.sort(key=lambda card: card.uid)
        self.rng.shuffle(self.draw_pile, f"nr{self.ante}")
        self.hand = []
        self.discard_pile = []
        self._apply_debuffs()
        self._draw_to_hand_size()
        if self.phase is Phase.GAME_OVER:
            # A run whose hand size has reached zero dies on the deal itself.
            # The game returns out of update_draw_to_hand the moment
            # draw_from_deck_to_hand reports it -- `if
            # G.FUNCS.draw_from_deck_to_hand(nil) then return true end` -- so
            # the first_hand_drawn jokers never fire and the round never
            # properly begins.
            return

        # After the deal, not before. The game fires these on
        # `first_hand_drawn`, so Certificate's card lands on top of a hand
        # that is already full and the round starts one card over the limit.
        # Running them first put the card in a hand that was then thrown away
        # and dealt again. A Blueprint on a Certificate makes a second card.
        for joker, answer in self.calculating_hooks("on_round_start"):
            answer(joker, self)

        self.phase = Phase.PLAYING
        self.log(f"--- Ante {self.ante} {self.blind.name}: need {self.blind.target} ---")
        # G.STATE = SELECTING_HAND, and then drawn_to_hand (game.lua:3237-3238).
        self._drawn_to_hand()

    # Madness, Ceremonial Dagger and Chicot open their setting_blind branch
    # with `not context.blueprint` (card.lua:2492, 2503, 2561). Burglar,
    # Riff-raff, Cartomancer and Marble Joker are copied.
    _NOT_COPIED_ON_BLIND_SELECT = frozenset(
        {"Madness", "Ceremonial Dagger", "Chicot"})

    def _setting_blind(self) -> None:
        """calculate_joker({setting_blind}) down the row (state_events.lua:335-337).

        Nothing leaves the row or joins it while the pass runs. Madness and
        Ceremonial Dagger only mark their victim getting_sliced (card.lua:2512,
        2568) and dissolve it in an event, and Riff-raff makes its jokers in
        one (card.lua:2532-2543). So a joker getting sliced keeps its place
        and its slot for the rest of the pass and does nothing -- the whole
        branch is `context.setting_blind and not self.getting_sliced`
        (card.lua:2491) -- a Dagger will not eat a neighbour already taken,
        and nothing sees Riff-raff's new jokers. When the pass is done the
        queued events run, and then the sliced jokers go: the dissolve's
        remove() is queued from inside the slicing event (card.lua:2170-2175),
        behind everything the pass queued.

        Removing the victim on the spot and walking a copy of the row let a
        Burglar that Madness ate still give its hands, a Riff-raff it ate still
        fill the slot, and a Dagger behind it eat the joker past the one taken.

        Through effective_specs, because a Blueprint or a Brainstorm runs the
        copied joker's calculate_joker (card.lua:2304-2330): skipped when that
        joker is getting sliced, and by the Burglar-style guards
        `(context.blueprint_card or self).getting_sliced` when the copier is.
        No copy ran at all before, so a Blueprint on a Burglar gave three
        hands where the game gives six. And a debuffed joker runs nothing
        (`not self.debuff`, card.lua:2303), copier or copied.
        """
        from .scoring import effective_specs

        row = list(self.jokers)
        self.blind_select_events, self.getting_sliced = [], []
        self.joker_buffer = 0
        for joker, (spec, source) in zip(row, effective_specs(row)):
            hook = spec.on_blind_select
            if hook is None or joker.debuffed or source.debuffed:
                continue
            if (source is not joker
                    and spec.name in self._NOT_COPIED_ON_BLIND_SELECT):
                continue
            if self.is_getting_sliced(joker) or self.is_getting_sliced(source):
                continue
            hook(source, self)
        events, self.blind_select_events = self.blind_select_events, None
        for event in events:
            event()
        sliced, self.getting_sliced = self.getting_sliced, []
        for victim, reason in sliced:
            self.destroy_joker(victim, reason)
        self.joker_buffer = 0

    def _drawn_to_hand(self) -> None:
        """Blind:drawn_to_hand, after every deal into the round (game.lua:3238).

        Crimson Heart's half; Cerulean Bell's is in _draw_to_hand_size. The
        blind is prepped by set_blind (blind.lua:94) and again by press_play
        whenever a hand is played with a joker held (blind.lua:488-493), and
        drawn_to_hand takes the prep away whatever happens (blind.lua:602).
        So the opening deal takes a joker, the draw after each played hand
        moves it on, a discard's draw moves nothing, and the hand that wins
        the round draws nothing at all. This used to pick in _play, just
        before scoring: the same draws, each one a deal late. N1OA90W1 stopped
        on it at decision 188, Stencil debuffed in the game the moment the
        blind was selected and nothing here.

        The pick (blind.lua:588-600): the jokers not already debuffed are the
        candidates -- all of them when there is only one -- every joker is let
        go, and one candidate is taken with pseudorandom_element, which sorts
        by sort_id first. By creation order, then, not by seat: recording 12
        had its jokers dragged about, and picking by seat debuffed a Turtle
        Bean four places from the Baron the game took, 42441 against 14147.
        """
        blind = self.blind
        if blind is None or self.phase is not Phase.PLAYING:
            return
        boss = self.boss
        if (boss is not None and boss.debuff_a_joker and blind.prepped
                and self.jokers):
            eligible = [j for j in self.jokers
                        if not j.debuffed or len(self.jokers) < 2]
            for joker in list(self.jokers):
                self.set_joker_debuff(joker, False)
            if eligible:
                chosen = self.rng.random_element(
                    sorted(eligible, key=lambda j: j.uid), "crimson_heart")
                self.set_joker_debuff(chosen, True)
        blind.prepped = False

    def _nominate_forced_card(self) -> None:
        """Cerulean Bell picks a card the player must always include.

        Chosen when the hand is dealt, and only when the hand does not
        already hold the one it chose -- so it survives a discard that leaves
        it in place and is replaced when it goes.
        """
        boss = self.boss
        if boss is None or not boss.forces_a_card or not self.hand:
            self.forced_card = None if boss is None else self.forced_card
            return
        if self.forced_card in self.hand:
            return
        self.forced_card = self.rng.random_element(
            sorted(self.hand, key=lambda c: c.uid), "cerulean_bell")

    def _apply_debuffs(self) -> None:
        boss = self.boss
        for card in self.full_deck:
            card.debuffed = False
        if boss is None:
            return
        if boss.debuff_until_sale:
            # Verdant Leaf debuffs every card -- not the jokers -- until a
            # joker is sold, which disables the blind.
            for card in self.full_deck:
                card.debuffed = True
            return
        for card in self.full_deck:
            # Blind:debuff_card asks `card:is_suit(suit, true)`
            # (blind.lua:626), and that is not the card's printed suit: a Wild
            # Card is every suit and is debuffed by any of the four, a Stone
            # Card has no suit and is debuffed by none, and a Smeared Joker
            # pairs hearts with diamonds and spades with clubs (card.lua:4076).
            # Reading `card.suit` let a wild Five score under The Goad for
            # five chips and a Greedy Joker's three mult -- 2291 against the
            # game's 1924, on a hand that decided the blind.
            if (boss.debuff_suit is not None
                    and suit_matches_for(card, boss.debuff_suit, self)):
                card.debuffed = True
            # `card:is_face(true)` in the same function (blind.lua:630): not
            # the printed rank, so a Stone King is spared, and Pareidolia
            # makes every card a face card (card.lua:967) -- so The Plant
            # debuffs the whole deck, Stone included, while one is held.
            if boss.debuff_face and is_face_for(card, self, from_boss=True):
                card.debuffed = True
            if boss.debuff_previously_played and card.played_this_ante:
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

    def _reroll_boss_blind(self) -> None:
        """Draw a new boss and put it in force if the boss blind is on deck.

        Rolling alone is not enough. `blind` is built when the blind comes up
        and holds its boss, so a re-roll that only moves ante_boss leaves the
        run facing the old one -- which is what a Boss Tag did after the tag
        firing moved to *after* _next_blind, where the blind already exists.
        The paid re-roll had always done both; now they share this.
        """
        self._roll_boss()
        if self.blind is not None and self.blind.kind is BlindKind.BOSS:
            self.blind = make_blind(
                BlindKind.BOSS, self.ante, self._pick_boss(),
                ante_scaling=self.deck_config.get("ante_scaling", 1),
                scaling=self.blind_scaling)

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
        value = (Card.suit_sort_value.fget if self.hand_sort == "suit"
                 else Card.sort_value.fget)
        # The id breaks ties, ascending, so two identical cards have one
        # order. The game compares get_nominal alone and Lua's table.sort is
        # not stable, so its answer for a pair of Jacks of Spades is whatever
        # the quicksort happened to do -- and that decides which of them a
        # play removes, which reorders what The Hook then draws from. The
        # engine is patched to break the tie the same way; see
        # headless_patch.lua.
        self.hand.sort(key=lambda c: (-value(c), c.uid))

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
        # Checked before anything is dealt, at the top of the game's own
        # draw_from_deck_to_hand, and it is the one loss that does not go
        # through the end of the round: no target check, no Mr. Bones, no
        # cash-out. Verified on the engine -- with the blind's target already
        # beaten and with Mr. Bones held, both still ended the run.
        #
        # An Arcana or Spectral pack suspends it. Those two deal you a hand to
        # use their cards on, and one of those cards may well be how you fix
        # the problem, so the game refuses to call it while either is open.
        if self.hand_size <= 0 and not self.hand and not self._in_hand_pack():
            self.phase = Phase.GAME_OVER
            self.log("No hand size left")
            return

        boss = self.boss
        if (boss is not None and boss.always_draw_three
                and (self.hands_played_this_round or self.discards_used)):
            # The Serpent: after the first play or discard of the round the
            # hand is topped up with exactly three cards, whatever room there
            # was -- so the hand grows past its limit and keeps growing.
            self._draw_cards(min(3, len(self.draw_pile)))
            self._nominate_forced_card()
            return
        while len(self.hand) < self.hand_size and self.draw_pile:
            self.hand.append(self.draw_pile.pop())
        self._sort_hand()
        self._nominate_forced_card()
        if not self.hand and not self.draw_pile and self.phase is Phase.PLAYING:
            # Hand and deck both empty ends the round where it stands --
            # update_selecting_hand calls end_round the moment nothing is left
            # anywhere. That is not itself a loss: with the target already met
            # the engine goes to the cash-out screen as usual. It cannot be
            # met here, though, because a play that reaches the target ends
            # the round before this runs, so by the time the cards are gone
            # the run is short and the round is lost.
            #
            # Mr. Bones does not help, and the reason is worth writing down.
            # He does fire -- the engine dissolves him -- but the save returns
            # the run to SELECTING_HAND with the hand and deck still empty, so
            # end_round fires again immediately and this time there is nothing
            # to spend. He is consumed and the run ends anyway.
            self.phase = Phase.GAME_OVER
            self.log("Ran out of cards")

    # ------------------------------------------------------------------
    # playing
    # ------------------------------------------------------------------

    # The game asks for these with find_joker(name), which leaves out a
    # debuffed joker (misc_functions.lua:903-907): Four Fingers
    # (misc_functions.lua:524, 550), Shortcut (567), Splash
    # (state_events.lua:583), Pareidolia (card.lua:967), Smeared Joker
    # (card.lua:4072, 4084). Oops! All 6s doubles the probabilities in
    # add_to_deck and halves them in remove_from_deck (card.lua:608, 665),
    # which set_debuff runs (card.lua:526-538).
    def _four_fingers(self) -> bool:
        return any(j.name == "Four Fingers" for j in self.active_jokers)

    def _splash(self) -> bool:
        return any(j.name == "Splash" for j in self.active_jokers)

    def has_pareidolia(self) -> bool:
        """Every card counts as a face card."""
        return any(j.name == "Pareidolia" for j in self.active_jokers)

    def has_smeared(self) -> bool:
        """Hearts count as Diamonds and Spades as Clubs, both ways."""
        return any(j.name == "Smeared Joker" for j in self.active_jokers)

    def probability_scale(self) -> int:
        """Oops! All 6s doubles every listed probability, and stacks."""
        return 2 ** sum(1 for j in self.active_jokers
                        if j.name == "Oops! All 6s")

    def _shortcut(self) -> bool:
        return any(j.name == "Shortcut" for j in self.active_jokers)

    def evaluate_selection(self, cards: list[Card]):
        return evaluate(cards, splash=self._splash(),
                        smeared=self.has_smeared(),
                        four_fingers=self._four_fingers(),
                        shortcut=self._shortcut())

    def preview_score(self, indices: tuple[int, ...]) -> int:
        """Score a candidate play without advancing the run. See preview_play."""
        return self.preview_play(indices)[0]

    def preview_play(self, indices: tuple[int, ...]) -> tuple[int, list]:
        """Score a candidate play without advancing the run, and hand back
        the joker row as scoring left it.

        Joker counters and card enhancements that scoring would mutate are
        snapshotted and restored, and a throwaway RNG stands in so previewing
        does not consume the run's random stream.

        The row that comes back is the copies scoring ran on -- a Square
        Joker four chips up after a four-card hand, a Runner fifteen up after
        a Straight -- while the run's own row is put back untouched. Which is
        what farming a scaling joker needs to know and what a score alone
        cannot say: the hand is worth what it scores, the play is worth that
        plus every later hand the grown joker adds to.
        """
        played = [self.hand[i] for i in indices]
        held = [c for i, c in enumerate(self.hand) if i not in indices]
        real_jokers, real_rng = self.jokers, self.rng
        # Everything a scoring hook can write to that is not the joker it
        # runs on. The jokers are copied; these are put back. Space Joker
        # levels the played hand one time in four, 8 Ball makes a Tarot for
        # a scored eight, Hiker adds chips to every scored card for good,
        # Vampire and Midas Touch change enhancements -- and a caller
        # previewing every subset of a hand runs each of those two hundred
        # times. Levels were not restored, so a policy that previewed took
        # High Card to level 140 and won a run on hands the game never dealt.
        cards = [(c, c.enhancement, c.extra_chips, c.seal, c.edition)
                 for c in played + held]
        levels = dict(self.hand_levels.levels)
        plays = dict(self.hand_levels.plays)
        consumables = list(self.consumables)
        money = self.money
        # score_hand sets the blind's triggered flag for The Flint and for a
        # debuffed scoring card; a preview must not leave it set.
        triggered = self.blind.triggered if self.blind is not None else False
        self.jokers = [copy.copy(j) for j in real_jokers]
        # A throwaway generator, named off the run's own seed. This used to
        # xor the seed with a constant, which worked only while seeds were
        # integers -- every preview against a real run's seed raised
        # TypeError.
        self.rng = RunRng("%s_preview" % self.seed)
        try:
            result = self.evaluate_selection(played)
            # A hand the boss zeroes -- The Psychic, The Eye, The Mouth --
            # scores nothing and runs no scoring joker: evaluate_play skips
            # the whole block (state_events.lua:614, 997-999). The policy
            # values plays through this, so it has to say 0 too. Asked in
            # check mode, as cardarea.lua:168 asks it, so nothing is set.
            if self.hand_is_debuffed(result.hand, played):
                # But the `after` pass is outside that block and asked of
                # every hand (state_events.lua:1068-1075), so the copies come
                # back as _play leaves the row: Ice Cream melted, Seltzer
                # counted down. Returned untouched, a refused hand looked
                # free to a policy pricing the row a play leaves.
                self._refused_hand_after_pass(result, played, held)
                return 0, self.jokers
            # Counted before it scores, as the play counts it (see _play) and
            # as the game does: evaluate_play increments `played` at
            # state_events.lua:574, before the jokers' context.before pass
            # (card.lua:3411) where Obelisk decides whether to reset
            # (card.lua:3543). Without it the preview saw the count one play
            # late and called a reset a gain -- seven of twenty-six plays on
            # PLOQ83ZX, two of them "clears the blind" that did not. `finally`
            # puts the count back.
            self.hand_levels.plays[result.hand] += 1
            score = score_hand(self, result, played, held).score
            # The copies, read before `finally` puts the real row back.
            return score, self.jokers
        finally:
            for card, enhancement, extra, seal, edition in cards:
                card.enhancement = enhancement
                card.extra_chips = extra
                card.seal = seal
                card.edition = edition
            self.hand_levels.levels.clear()
            self.hand_levels.levels.update(levels)
            self.hand_levels.plays.clear()
            self.hand_levels.plays.update(plays)
            self.consumables[:] = consumables
            self.money = money
            if self.blind is not None:
                self.blind.triggered = triggered
            self.jokers, self.rng = real_jokers, real_rng

    def _refused_hand_after_pass(self, result, played: list[Card],
                                 held: list[Card]) -> None:
        """context.after for a hand the boss refused, over self.jokers.

        score_hand runs the pass for a scored hand; a refused one never gets
        there, and evaluate_play asks it anyway (state_events.lua:1068-1075).
        One place, so _play and preview_play cannot drift apart on it.
        """
        from .effects import ScoreContext
        from .scoring import after_hand_pass, calculating_specs

        after_hand_pass(calculating_specs(self.jokers), ScoreContext(
            hand=result.hand, scoring=result.scoring,
            played=tuple(played), held=tuple(held), game=self,
            contains=result.contains))

    def _play(self, indices: tuple[int, ...]) -> None:
        # Which cards the boss debuffs is decided again every time, not once
        # when the round began. The game re-evaluates it in Card:update, so a
        # card the player has just turned into a Diamond is debuffed by The
        # Window on the very next hand -- and a card turned *out* of Diamonds
        # is not. Applying it only at the start of the round let a converted
        # card score through a blind that should have silenced it.
        self._apply_debuffs()
        boss = self.boss
        if boss is not None and boss.debuff_a_joker and self.jokers:
            # Crimson Heart's press_play only preps the blind
            # (blind.lua:488-493). The joker it takes is picked on the draw
            # that follows the hand, in _drawn_to_hand, so this hand scores
            # against the one the last deal took.
            self.blind.prepped = True

        played = [self.hand[i] for i in indices]
        result = self.evaluate_selection(played)

        # Played this ante from the moment they are played, before anything
        # scores: play_cards_from_highlighted sets the flag as it moves each
        # card to the play area (state_events.lua:478-483), and evaluate_play
        # comes after. So a Vampire or a Midas Mask changing the enhancement
        # under context.before rebuilds the ability table and wipes the flag
        # again (card.lua:223-366, 3443-3480), and a DNA copy made there takes
        # it across (common_events.lua:2161-2167). Setting it after scoring
        # left the card a Vampire ate marked, and The Pillar debuffed it:
        # NQ86453Q at decision 78, 7823 here against the game's 8069.
        for card in played:
            card.played_this_ante = True

        # The Hook takes its two cards *before* the hand scores. It lives in
        # `Blind:press_play`, and the game runs that between moving the
        # played cards out of the hand and scoring anything:
        #
        #     draw_card(G.hand, G.play, ..., G.hand.highlighted[i])   -- x N
        #     if G.GAME.blind:press_play() then ...
        #
        # so the two cards it discards are already gone by the time the held
        # pass runs. Doing it after scoring -- which is what this did -- left
        # them in hand for one more hand each round, and a Steel card among
        # them paid an x1.5 the game never paid. Recording 11 stops on
        # exactly that at step 227: 75 chips x 193.5 here against the game's
        # 75 x 186, which is one Steel card, and it wins a 40000 blind on
        # 40409 that the game failed at 39847.
        #
        # This is the third boss to move for this reason. The Arm's level and
        # The Tooth's dollar are both below, with the same note.
        #
        # By creation order, not by what is on screen: the game picks with
        # pseudorandom_element, which sorts the table by sort_id before
        # indexing it, so a hand the player has dragged around loses the same
        # two either way. The pool is the hand *without* the played cards,
        # which are in G.play by now -- they are still in `self.hand` here
        # and are taken out below, so they are filtered rather than removed.
        hook = self.boss
        if hook is not None and hook.discard_random_on_play:
            pool = sorted((c for c in self.hand
                           if not any(c is p for p in played)),
                          key=lambda card: card.uid)
            taken = self.rng.sample("hook", pool,
                                     min(hook.discard_random_on_play,
                                         len(pool)))
            if taken:
                self.discard_cards(list(taken), hook=True)

        # DNA and Sixth Sense act on the played cards before they score, and
        # only on the round's first hand.
        #
        # Through effective_specs, so a Blueprint or a Brainstorm copying a
        # DNA makes a second copy of the card. The game guards its other DNA
        # branch with `not context.blueprint` and this one with nothing, so a
        # copied DNA does fire:
        #
        #     if self.ability.name == 'DNA'
        #        and G.GAME.current_round.hands_played == 0 then
        #         if #context.full_hand == 1 then
        #             ... table.insert(G.playing_cards, _card)
        #
        # Recording 8 shows it plainly: playing one card into a DNA with a
        # Brainstorm on its left grew the deck by two, and by one here. The
        # copies are permanent, so the decks drifted further apart with every
        # such hand, and the extra cards were steel -- which is how a scoring
        # divergence turned out to be a deck-size one.
        #
        # Sixth Sense is the other way round: its branch is
        # `context.destroying_card and not context.blueprint` (card.lua:2603),
        # so a copy destroys nothing and makes no second Spectral.
        #
        # Neither runs on a hand the boss refuses. DNA is `context.before`
        # (card.lua:3501) and Sixth Sense `context.destroying_card`
        # (card.lua:2604), and evaluate_play asks both inside
        # `if not G.GAME.blind:debuff_hand(...)` (state_events.lua:614, 630,
        # 957) -- so one card into The Psychic is refused and copied nothing.
        # The question is the same here as below: nothing between the two
        # (The Hook, The Arm, The Tooth, The Ox) changes what debuff_hand
        # reads.
        debuffed = self.hand_is_debuffed(result.hand, played)
        if not debuffed and self.hands_played_this_round == set():
            for joker, answer in self.calculating_hooks("before_hand"):
                answer(joker, played, self)

        # And the held cards are read *after* them, because DNA puts its copy
        # in hand and the game scores that copy as a held card like any other.
        # Reading the hand first left the copy out of the held pass entirely.
        #
        # It is worth what the copy is worth, which can be a great deal: in
        # recording 8 the copied card was a steel King with a red seal, so it
        # brought three more x1.5 -- itself, its red seal, and the Mime
        # retriggering it -- and the hand scored 537670 here against the
        # game's 568510.
        #
        # By identity rather than by position: the copy joins the hand while
        # this is being worked out, and an index into the old hand no longer
        # means what it meant.
        held = [c for c in self.hand if not any(c is p for p in played)]
        self.hands_left -= 1
        self.hand_levels.plays[result.hand] += 1

        self.last_hand = result.hand.label

        # The Arm takes the level off *before* the hand scores. The game calls
        # debuff_hand and only then reads G.GAME.hands[text].mult, so the hand
        # is already a level down by the time it is worth anything. Doing it
        # after scoring, which is what this did, gave the round one free hand
        # at the old level -- worth about a third here.
        boss = self.boss
        arm_triggered = ox_triggered = False
        if boss is not None and boss.level_down_played_hand:
            arm_triggered = self.hand_levels.levels[result.hand] > 1
            self.hand_levels.levels[result.hand] = max(
                1, self.hand_levels.levels[result.hand] - 1)

        # And the two bosses that move money do it here as well, for the same
        # reason. Both live in Blind:press_play, which the game runs before
        # evaluate_play, so the money is already gone by the time a joker
        # reads it -- and Bootstraps reads it, at two mult for every five
        # dollars held.
        #
        # The Tooth takes a dollar per card played. Recording 8 stopped on
        # exactly that at step 393: three cards into a Tooth left $9002, so
        # the game scored 2 * floor(9002/5) = 3600 mult of Bootstraps where
        # this scored 3602 off the $9005 it still thought it had. 435 chips on
        # a hand worth 808411, from three dollars charged in the wrong order.
        if boss is not None:
            if boss.money_per_card_played:
                self.add_money(boss.money_per_card_played * len(played),
                               boss.name)
            if (boss.zero_money_on_most_played
                    and self._is_most_played(result.hand)):
                ox_triggered = True
                self.money = 0
                self.log(f"{boss.name}: money set to $0")

        # A boss can zero the hand outright. The game skips the whole scoring
        # block when debuff_hand answers yes -- `mult = mod_mult(0);
        # hand_chips = mod_chips(0)` -- so no joker and no card triggers at
        # all, and the hand is simply spent. (`debuffed` is asked above,
        # before DNA and Sixth Sense.)
        # The Mouth's write, made only by the real call and only when the
        # hand got through: a zeroed hand returns before
        # `if not check then self.only_hand = handname end`.
        if (not debuffed and boss is not None and boss.lock_first_hand_type
                and self.mouth_only_hand is None):
            self.mouth_only_hand = result.hand

        # G.GAME.blind.triggered, which is all Matador reads. Every play clears
        # it first (state_events.lua:455). The Hook, The Tooth and Crimson
        # Heart set it in press_play (blind.lua:464-507), but debuff_hand opens
        # with `if self.debuff then self.triggered = false` (blind.lua:521-522)
        # and set_blind makes debuff `{}` at the least (blind.lua:85) -- true
        # in Lua -- so that is wiped before any joker looks. debuff_hand then
        # sets it for a refused hand (blind.lua:523-547), The Arm on a hand
        # above level 1 and The Ox on the most played hand (549-566). A
        # disabled blind skips its own hooks, which `boss` being None stands
        # for here. The two that happen while the hand scores -- The Flint and
        # a debuffed scoring card -- are set in score_hand.
        if self.blind is not None:
            self.blind.triggered = bool(boss is not None and (
                debuffed or arm_triggered or ox_triggered))

        if debuffed:
            self.log("%s debuffed by %s: scores nothing"
                     % (result.hand.label, boss.name if boss else "the blind"))
            ctx = None
            # Nothing scores, but every joker is still asked, under
            # context.debuffed_hand (state_events.lua:1015-1027) -- through a
            # Blueprint too, which passes any context on (card.lua:2305-2317).
            from .scoring import calculating_specs

            for _owner, spec, source in calculating_specs(self.jokers):
                if spec.on_debuffed_hand is not None:
                    spec.on_debuffed_hand(source, self)
            # And then `after`, which sits outside the `if` and so is asked
            # of every hand played (state_events.lua:1068-1075). Ice Cream
            # and Seltzer answer it (card.lua:3571, 3601), so a hand The Mouth
            # zeroes still melts one and counts down the other. Running it
            # only inside score_hand left both untouched: seed VJPW2C6Z,
            # Anaglyph Deck, stake 8, decision 46 took the game's Ice Cream
            # from 40 to 35 on a refused Three of a Kind and kept this on 40.
            self._refused_hand_after_pass(result, played, held)
        else:
            ctx = score_hand(self, result, played, held)

        # The run's hand count goes up once the hand has scored, not before:
        # the game raises it alongside draw_from_play_to_discard, after the
        # scoring is done. Loyalty Card works out where it is in its cycle
        # from that number, so counting the hand first put it one hand along
        # and its X4 landed on the wrong hand for the whole run.
        self.hands_played += 1

        # Jokers that make a card off the back of a hand -- Superposition,
        # Séance, Vagabond -- run once the hand has resolved, so they can ask
        # what it turned out to be. Their joker_main branches have no `not
        # context.blueprint` (card.lua:3743, 3762, 3787), so a copy makes a
        # card of its own.
        for joker, answer in self.calculating_hooks("after_hand"):
            if ctx is not None:
                answer(joker, ctx)
        gained = ctx.score if ctx is not None else 0
        self.chips_scored += gained
        # check_and_set_high_score only ever raises this.
        self.best_hand = max(self.best_hand, int(gained))
        if ctx is not None:
            self.log(f"{result.hand.label} scored {gained} "
                     f"({ctx.chips:g} x {ctx.mult:g}) -> {self.chips_scored}")
            if ctx.money_gained:
                self.add_money(ctx.money_gained, "cards")

        self.hands_played_this_round.add(result.hand)

        # The glass roll is in the destroying pass, inside the block a refused
        # hand skips (state_events.lua:614, 950-996), so a Glass card played
        # into The Psychic neither breaks nor moves the 'glass' stream.
        shattered = [] if debuffed else shattered_glass(self, result.scoring)
        for card in shattered:
            # Scoring sets the flag inline, before the jokers are told, so
            # this is one of the two places Glass Joker is paid.
            self.remove_card(card, shattered=True)
            self.log(f"{card} shattered")

        for card in played:
            if card in self.hand:
                self.hand.remove(card)
                self.discard_pile.append(card)

        if self.chips_scored >= self.blind.target:
            self._beat_blind()
        elif self.hands_left <= 0:
            self._lose_round()
        else:
            self._draw_to_hand_size()
            self._drawn_to_hand()

    def _snapshot_most_played(self) -> None:
        """Fix the hand The Ox will punish, as a boss round closes.

        The game walks G.GAME.hands and keeps `v.played > _played or
        (v.played == _played and _order > v.order)`. `_order` is initialised
        to 100 and never assigned inside the loop, so the tie-break compares
        100 against every hand's order and is always true: any hand equalling
        the best replaces it, and the winner is simply the last one `pairs`
        happens to yield.

        That order is not reproducible across processes -- the same LuaJIT
        string hashing that moves To Do List's pool -- so a tie is a coin
        flip in the real game too. Measured, the order runs strongest-first,
        which leaves the *weakest* of the tied hands standing: with Pair and
        Flush both on 25 plays the engine picked Pair. Walking HANDLIST
        backwards reproduces that, and is a stated rule rather than a guess.
        """
        plays = self.hand_levels.plays
        self.most_played_hand = max(reversed(HANDLIST),
                                    key=lambda h: plays.get(h, 0))

    def _is_most_played(self, hand: HandType) -> bool:
        """The Ox compares against the snapshot, not against a live count.

        Reading the counts live made every hand tied for the lead count as
        the most played, so a run with two hands on three plays each had its
        money zeroed by either of them.
        """
        return hand is self.most_played_hand

    def _discard(self, indices: tuple[int, ...]) -> None:
        cards = [self.hand[i] for i in indices]
        self.discards_left -= 1
        self.discard_cards(cards)
        self.discards_used += 1
        self._draw_to_hand_size()
        self._drawn_to_hand()

    def discard_cards(self, cards: list[Card], hook: bool = False) -> None:
        """The discard itself: seals, joker hooks, and the pile.

        Split out because a discard the player did not ask for goes through
        all of it. The game has one function and a flag --
        `G.FUNCS.discard_cards_from_highlighted(e, hook)` -- and everything
        the flag turns off is at the end of it:

            if not hook then
                if G.GAME.modifiers.discard_cost then ... end
                ease_discard(-1)
                G.GAME.current_round.discards_used = ... + 1
                G.STATE = G.STATES.DRAW_TO_HAND

        so The Hook, which calls it with `hook` true, still fires the seals
        and the jokers and still fills the discard pile -- it just costs no
        discard and draws nothing back. Everything above that line is here
        and everything below it is in `_discard`.

        `discards_used` is read rather than passed, exactly as the game
        reads it, and a Hook discard never increments it -- so a Hook
        discard on an untouched round is still the round's first for Trading
        Card, whose check sits in the `discard` context. But the flag is also
        passed into the `pre_discard` context (state_events.lua:395), and
        Burnt Joker reads it:

            if self.ability.name == 'Burnt Joker' and
               G.GAME.current_round.discards_used <= 0 and not context.hook

        (card.lua:2749), so the cards The Hook takes level nothing.
        """
        first = self.discards_used == 0
        # Copies included: a Blueprint on a Mail-In Rebate pays twice, and on
        # a Burnt Joker levels the hand twice (see _NOT_COPIED).
        for joker, answer in self.calculating_hooks("discarded"):
            answer(joker, cards, self)
        if first:
            for joker, answer in self.calculating_hooks("on_first_discard"):
                # The copied joker, so a copy of Burnt Joker skips too.
                if hook and joker.name == "Burnt Joker":
                    continue
                answer(joker, cards, self)
        for card in cards:
            # Card:calculate_seal opens `if self.debuff then return nil end`
            # (card.lua:2242-2243), ahead of the Purple Seal's discard branch
            # (2253): a debuffed Purple Seal makes nothing. W3D6TLM1 threw one
            # under Verdant Leaf and the shadow drew The Tower the game never
            # made.
            if card.seal is Seal.PURPLE and not card.debuffed:
                self.add_consumables(
                    self.random_consumables(ConsumableKind.TAROT, 1, "8ba"))
            # A joker may have eaten the card on its way out -- Trading Card
            # destroys a lone first discard -- and a destroyed card is not
            # discarded. The game splits exactly here: the branch that removes
            # it never reaches the draw_card into G.discard, so the card is
            # gone rather than in the pile, and it will not come round again
            # when the deck comes back. Moving it anyway raised ValueError on
            # a hand a Trading Card had already emptied.
            if card not in self.hand:
                continue
            self.hand.remove(card)
            self.discard_pile.append(card)

    def _in_hand_pack(self) -> bool:
        """Is an Arcana or Spectral pack open? Those two deal a hand."""
        return (self.phase is Phase.PACK and self.pack is not None
                and self.pack.kind in (PackKind.ARCANA, PackKind.SPECTRAL))

    def _lose_round(self) -> None:
        """The round ended short of the target.

        Mr. Bones is the only way back, and the flag saying so had never been
        read by anything -- the run simply ended. He needs a quarter of the
        target, and what he buys is specific: the engine dissolves him, marks
        the blind defeated so the run moves on to the next one rather than
        replaying it, and stops at the cash-out screen -- but pays no blind
        reward, because the blind was not beaten. Measured: from $10, a saved
        small blind cashes out at $12 and a beaten one at $15, the $3 gap
        being the reward.
        """
        assert self.blind is not None
        if self.blind.target > 0:
            for joker in list(self.jokers):
                if (joker.spec.prevents_death and not joker.debuffed
                        and self.chips_scored / self.blind.target >= 0.25):
                    self.log(f"{joker.name} saved the run")
                    # He goes after the round is closed, not before: the game
                    # only queues start_dissolve (card.lua:3049-3057), so he
                    # is still in the end_round pass and a rental Mr. Bones
                    # pays that round's rent (state_events.lua:103-108).
                    self._beat_blind(reward=False)
                    self.destroy_joker(joker)
                    return
        self.phase = Phase.GAME_OVER
        self.log(f"Lost on ante {self.ante} {self.blind.name}")

    def _beat_blind(self, reward: bool = True) -> None:
        """Close the round and stop on the cash-out screen.

        `reward` is False when Mr. Bones brought the run here rather than the
        score -- see _lose_round.

        The payout is worked out here but not paid: the game shows it and
        waits, and paying early makes the two engines disagree about money for
        the whole of that window. The deck comes back now, though -- the
        engine has all fifty-two cards again the moment the round ends.
        """
        assert self.blind is not None
        # A gold card pays the moment the round ends -- ease_dollars, right
        # there in the hand loop -- rather than as a row on the cash-out
        # screen. Folding it into the payout left the run three dollars short
        # for the length of that screen, per gold card held.
        gold = sum(3 * held_triggers(self, c) for c in self.hand
                   if c.enhancement is Enhancement.GOLD)
        if gold:
            self.add_money(gold, "gold cards")

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
                    for _ in range(held_triggers(self, card)):
                        self.add_consumables(
                            [cons.REGISTRY[PLANET_FOR_HAND[hand]]])
        # The Green Deck pays per unspent hand *and* per unspent discard, and
        # pays more per hand than the usual dollar -- money_per_hand and
        # money_per_discard. It also earns no interest at all, which is the
        # trade. All three were in the deck data and none was applied.
        config = self.deck_config
        per_hand = config.get("extra_hand_bonus", 1)
        per_discard = config.get("extra_discard_bonus", 0)
        # G.GAME.unused_discards accumulates over the whole run, whatever the
        # deck pays per discard -- it is the Garbage Tag's meter, not a payout.
        self.unused_discards += max(0, self.discards_left)
        # G.GAME.interest_amount: one by default, raised by To the Moon in
        # add_to_deck and lowered in remove_from_deck. It multiplies the
        # number of five-dollar blocks *after* the cap has bitten, so the cap
        # does not limit the bonus -- measured on the engine at $100 against
        # the $25 cap, where one To the Moon pays $10 and two pay $15 against
        # a base of $5.
        #
        # Debuffing a joker runs remove_from_deck(true), so the counter comes
        # off with it: active_jokers, not jokers. That is true of every one of
        # these run-level counters -- see test_declared_flags.
        # Everything but the interest. Interest is worked out further down,
        # after the rent, because that is where the game works it out: the
        # end-of-round joker pass and calculate_rental run first
        # (state_events.lua:99-109) and update_round_eval builds the payout
        # rows afterwards, reading G.GAME.dollars as it then stands. Two
        # rentals took six dollars off a five dollar balance, so the game paid
        # no interest and this paid a dollar of it.
        self.pending_payout = ((self.blind.reward if reward else 0)
                               + max(0, self.hands_left) * per_hand
                               + max(0, self.discards_left) * per_discard)

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

        if self.blind.kind is BlindKind.BOSS:
            # The Ox's target for the ante ahead is fixed here, as the boss
            # round closes, and nowhere else.
            self._snapshot_most_played()

        # An Investment Tag pays as a row on the cash-out screen rather than
        # the instant the boss falls, and every one held pays: the game's eval
        # loop walks the whole tag list and adds a row for each.
        # The Anaglyph Deck hands over a Double Tag every time a boss falls,
        # from Back:trigger_effect on the same 'eval' the Investment Tag reads.
        # That is where an arbitrarily long tag stack comes from without a
        # single Diet Cola: one Double a boss, every one of them copying
        # whatever tag arrives next.
        if (self.blind.kind is BlindKind.BOSS
                and self.deck_config.get("double_tag_after_boss")):
            self.add_tag_by_key("tag_double")

        if self.blind.kind is BlindKind.BOSS:
            while Tag.INVESTMENT in self.tags:
                self.tags.remove(Tag.INVESTMENT)
                self.pending_payout += 25
                self.log("Investment Tag: +$25 on the cash-out")

        # The Juggle Tag's cards go back as the round closes -- a loan for one
        # round, not a permanent gain.
        self.temp_hand_size = 0

        self.beaten_blind = self.blind
        # Beating a boss puts Campfire back to X1.
        if self.blind.kind is BlindKind.BOSS:
            for joker in self.calculating_jokers():
                if joker.name == "Campfire":
                    joker.counter = 1.0

        self.beaten_was_boss = self.blind.kind is BlindKind.BOSS
        self.blind = None
        self.phase = Phase.ROUND_EVAL

        # The ante turns over the moment the boss round closes -- the engine
        # already reads the next ante on the cash-out screen, before a penny
        # has been paid.
        if self.beaten_was_boss:
            self.blind_index = 0
            self.ante += 1
            # reset_blinds puts all three back to Upcoming for the new ante.
            self.skipped_this_ante.clear()
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
        self._reroll_todo_hands()

        # calculate_joker({end_of_round}) -- decay, growth and destruction,
        # all of it the instant the round closes and before the cash-out
        # screen appears. A Popcorn that has run out is gone by the time the
        # player sees the score. No copies: the branch is `elseif not
        # context.blueprint` (card.lua:2888).
        #
        # The row as it stood when that pass began is kept for the rent. The
        # game walks G.jokers.cards once and charges each joker's rent right
        # after its own end_of_round answer (state_events.lua:99-110), and a
        # joker that leaves on that answer -- Gros Michel going extinct,
        # Popcorn or Turtle Bean eaten -- only queues its removal
        # (card.lua:3021-3036, 2947-2962, 2905-2920). So it is still in the
        # row when calculate_rental runs on it, and a rental pays its last $3.
        in_row = list(self.jokers)
        for joker, answer in self.calculating_hooks("round_end"):
            answer(joker, self)

        # The stake's stickers are paid for when the round ends, not when the
        # money is taken: calculate_rental and calculate_perishable run in
        # evaluate_round, so the rent is already gone by the time the
        # cash-out screen appears. A rental takes three dollars a round and a
        # perishable counts one round closer to being switched off.
        #
        # Over the row the pass began with, not what is left of it: two
        # stake-8 runs, DS2IGPRB and N97LC9AB, stopped $4 and $3 rich on a
        # rental Gros Michel that went extinct without paying.
        for joker in in_row:
            if joker.rental:
                self.add_money(-RENTAL_RATE, f"{joker.name} rental")
            if joker.perishable and joker.perish_tally > 0:
                joker.perish_tally -= 1
                if joker.perish_tally == 0:
                    joker.debuffed = True
                    self.log(f"{joker.name} perished")

        # And now the interest, on what is left after the rent. The multiplier
        # applies after the cap has bitten, so the cap does not limit the
        # bonus -- measured on the engine at $100 against the $25 cap, where
        # one To the Moon pays $10 and two pay $15 against a base of $5.
        #
        # Debuffing a joker runs remove_from_deck(true), so the counter comes
        # off with it: active_jokers, not jokers -- and a perishable that just
        # expired above is already out of that list, as it is in the game.
        per_block = 1 + sum(j.spec.interest_bonus for j in self.active_jokers)
        if not config.get("no_interest"):
            self.pending_payout += per_block * min(self.interest_cap,
                                                   max(0, self.money) // 5)

        # Which jokers get a money row is settled now, while Crimson Heart
        # still holds its joker: evaluate_round works out every row at once
        # (state_events.lua:1175-1178), and defeat() -- and the release
        # below -- only runs from an event it queued first (1148-1155,
        # blind.lua:330-337). A joker the boss held pays no row that round.
        self.dollar_rows = [j for j in self.active_jokers
                            if j.spec.round_money is not None]

        # Blind:defeat leaves the empty blind behind: set_blind(nil)
        # (blind.lua:336) asks debuff_card of every joker (blind.lua:211-213)
        # and an empty blind holds none (blind.lua:651), so a joker Crimson
        # Heart held is back by the cash-out screen. It is an event, and
        # evaluate_round has already reckoned the interest by then
        # (state_events.lua:1152 against the rows after it) -- so after the
        # interest here as well.
        for joker in list(self.jokers):
            self.set_joker_debuff(joker, False)

        # And now the run stands on the cash-out screen and waits.
        #
        # This used to cash out by itself, on the grounds that the screen
        # holds nothing a policy decides. Two of the eight recordings say
        # otherwise, and not in the way that was assumed: what a player does
        # there is *use a Planet card*, having just seen which hand they
        # played. Three times in one run. So the screen is a real place with
        # real choices on it, and skipping it left the simulator a whole
        # screen ahead -- fresh counters and no chips against a scoreboard
        # still showing two hundred thousand.

    def _cash_out(self) -> None:
        """Take the payout and move on, as pressing Cash Out does."""
        assert self.beaten_blind is not None

        # Pressing Cash Out shuffles the deck, under its own pool name. That
        # is not a detail: the next thing to draw from this deck is the hand
        # an Arcana or Spectral pack deals in the shop, so without this the
        # pack deals off the order the round happened to leave -- which is
        # why not one pack hand in any recording matched. Sorted by card id
        # first, as pseudoshuffle does.
        self.draw_pile.sort(key=lambda card: card.uid)
        self.rng.shuffle(self.draw_pile, "cashout%d" % self.ante)

        self.add_money(self.pending_payout, f"{self.beaten_blind.name} payout")
        self.pending_payout = 0

        # A beaten boss ends the ante, and the next one's two skip tags are
        # rolled here, on the cash-out screen -- after the voucher, which went
        # a moment earlier when the boss fell.
        if self.beaten_blind.kind is BlindKind.BOSS:
            self._roll_ante_tags()
            # reset_blinds runs after the tags: it draws the next boss and
            # gives Director's Cut its reroll back.
            self.boss_rerolled = False
            self._roll_boss()

        # The money rows on the cash-out screen -- calculate_dollar_bonus --
        # are paid when the button is pressed. What happens the moment the
        # round *closes* is the other hook, and that runs in _beat_blind.
        #
        # A debuffed joker has no row: calculate_dollar_bonus opens with
        # `if self.debuff then return end` (card.lua:1656). A perishable that
        # ran out this round was debuffed in end_round (state_events.lua:109)
        # before evaluate_round built the rows (1176), so its last round pays
        # nothing -- a perished Golden Joker went on paying four dollars.
        rows = (self.dollar_rows if self.dollar_rows is not None else
                [j for j in self.active_jokers
                 if j.spec.round_money is not None])
        self.dollar_rows = None
        for joker in rows:
            joker.spec.round_money(joker, self)

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
            for card in self.full_deck:
                card.played_this_ante = False
            # The ante has already moved on -- _beat_blind raises it the
            # moment a boss falls -- so the run is won once it reads *past*
            # WIN_ANTE, not at it. The game's own test is
            # `round_resets.ante == win_ante and blind:get_type() == 'Boss'`,
            # checked before the ante turns over, which is the same instant.
            #
            # Reading `>=` here declared victory an ante early: beating the
            # ante-seven boss took the ante to eight and ended the run. It
            # cost the whole of ante eight, which is the hardest ante there
            # is, and it was invisible until the simulator was driven as a
            # training environment and started reporting wins the engine
            # never gave for the same policy.
            if self.ante > WIN_ANTE and not self.endless:
                self.phase = Phase.WON
                self.log(f"Run won after ante {WIN_ANTE}")
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
        jimbot_sim.shop_pool, which is checked against the engine draw for draw
        -- the distribution a policy trains against is as much a part of
        fidelity as the scoring, and it is the half that fails silently.
        """
        played = [h.label for h, n in self.hand_levels.plays.items() if n > 0]
        owned = {c.enhancement.value for c in self.full_deck}
        kind, key = shop_pool.draw_shop_card(
            self.rng, self.ante, rates=self._shop_rates(),
            seen_jokers=self.seen_centers, played_hands=played,
            # What Showman is *for*: get_current_pool skips a card the run
            # already has unless one is held (common_events.lua:1987). The
            # pack path passed this and the shop path did not, so a Showman
            # widened a pack's pool and left the shop's alone -- which is
            # most of the joker's value, since the shop is where a run buys
            # the second Blueprint it is bought to allow.
            showman=any(j.spec.allows_duplicates for j in self.active_jokers),
            owned_enhancements={"m_%s" % e for e in owned},
            pool_flags=self.pool_flags)

        # Illusion's first roll costs a draw on *every* slot, whatever the
        # slot turns out to be. The game decides Enhanced-or-Base inside the
        # table of candidate types, and Lua builds that table in full before
        # picking from it -- so the draw is made and then thrown away for a
        # Joker or a Tarot. Rolling it only for playing cards, which is what
        # this did, under-draws by one per non-card slot and walks the
        # illusion pool out of step for the rest of the run.
        #
        # Counted on the engine over twelve slots: no Illusion, no draws at
        # all; with Illusion, thirteen draws for twelve slots, the extra one
        # belonging to the single playing card among them.
        illusion = any(v.key == "v_illusion" for v in self.vouchers)
        enhanced = illusion and self.rng.pseudorandom("illusion") > 0.6

        if kind == "Joker":
            spec = JOKER_REGISTRY[shop_pool.NAME_BY_JOKER_KEY[key]]
            # "edi" + the append + the ante. The ante was missing, so every
            # shop in the run polled the same pool and got the wrong answer:
            # a Holographic Loyalty Card came out plain, and holographic is
            # ten mult.
            edition = _EDITION_BY_NAME[shop_pool.poll_edition(
                self.rng, "edi%s%d" % (shop_pool.SHOP_APPEND, self.ante),
                edition_rate=self.edition_rate)]
            joker = self._made_joker(JokerInstance(spec, edition=edition))
            self._apply_stickers(joker)
            return ShopSlot("joker", spec.cost, joker=joker)
        if kind in ("Tarot", "Planet", "Spectral"):
            spec = cons.REGISTRY[shop_pool.NAME_BY_CONSUMABLE_KEY[key]]
            return ShopSlot("consumable", spec.cost, consumable=spec)
        # A playing card, which only appears once Magic Trick or Illusion has
        # raised the playing card rate. This used to hand back an arbitrary
        # Tarot as a placeholder, so a shop that offered a card offered the
        # wrong thing entirely and the purchase went to the wrong slot.
        #
        # Illusion is the reason the type is decided by a roll: with it, a
        # shop card is Enhanced rather than Base six times in ten, and may
        # carry an edition and a seal besides. Without it the roll is not
        # made at all -- the game short-circuits on used_vouchers.
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
        return ShopSlot("card", 1, card=card)

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
            slot.couponed = True
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


    def _open_shop(self) -> None:
        shop = Shop()
        shop.free_rerolls = sum(j.spec.free_rerolls
                                for j in self.active_jokers)
        # Set before it is filled, not after. `seen_centers` blanks a pool
        # with what exists, and a card that has just been built exists:
        # Card:set_ability writes `G.GAME.used_jokers[k] = true` the moment it
        # is created (card.lua:352), so the first card of a shop blanks its
        # own key for the second draw. Filling a shop the run could not yet
        # see offered the same card twice -- `c_star, c_star` on a shelf where
        # the game had `c_star, c_heirophant`. A *rerolled* shop never had the
        # bug, since that path fills `self.shop` in place, which is what made
        # it look like a draw-order problem rather than this.
        self.shop = shop
        self._fill_shop(shop)
        shop.packs = [self._roll_pack() for _ in range(2)]
        # The ante's voucher, unless it has already been taken. All three
        # shops of an ante show the same one, so it is rolled per ante and not
        # per shop -- but once redeemed it is gone, and the slot stays empty
        # until the next ante rolls another.
        #
        # Restocking it regardless meant a run could buy the same voucher in
        # every shop of an ante and have its effect applied each time: three
        # Seed Moneys, an interest cap raised three times. The recordings
        # never caught it because they do not compare the shop, and a policy
        # would have found it immediately and learned to farm it.
        redeemed = {v.key for v in self.vouchers}
        shop.vouchers = ([shop_mod.VOUCHER_BY_KEY[self.round_voucher]]
                         if self.round_voucher
                         and self.round_voucher not in redeemed else [])

        # Every Voucher Tag held adds one more, drawn under 'Voucher_fromtag'
        # rather than the ante's pool name. Each draw withholds the vouchers
        # already in the row -- the game reads G.shop_vouchers.cards for that,
        # so the exclusion grows as the row does and none can appear twice.
        #
        # There is no cap on how many tags are waiting. Diet Cola sells into a
        # free Double Tag, every Double Tag copies the next tag that is not
        # another Double, and add_tag walks the whole list without breaking --
        # so selling a stack of Colas and then taking one Voucher Tag fills
        # the row with as many vouchers as there were Colas, plus one.
        while Tag.VOUCHER in self.tags:
            self.tags.remove(Tag.VOUCHER)
            key = shop_pool.draw_voucher(
                self.rng, self.ante,
                redeemed=[v.key for v in self.vouchers],
                on_offer=[v.key for v in shop.vouchers], from_tag=True)
            shop.vouchers.append(shop_mod.VOUCHER_BY_KEY[key])
            self.log("Voucher Tag: %s as well" % shop.vouchers[-1].name)

        # The D6 Tag makes this shop's rerolls free from the first one --
        # temp_reroll_cost = 0 -- and fires once, on the shop opening.
        if Tag.D_SIX in self.tags:
            self.tags.remove(Tag.D_SIX)
            shop.free_reroll_cost = True
            self.log("D6 Tag: rerolls start at nothing")
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
                self._reroll_boss_blind()
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
            # The same pool the shop rolls from, gates and all: a Rare Tag
            # offered Lucky Cat to a deck with no Lucky card, and neither tag
            # knew Gros Michel had gone extinct.
            key = shop_pool.draw_joker(
                self.rng, self.ante, seen_jokers=self.seen_centers,
                rarity=rarity, append=append,
                owned_enhancements={"m_%s" % c.enhancement.value
                                    for c in self.full_deck},
                showman=any(j.spec.allows_duplicates
                            for j in self.active_jokers),
                pool_flags=self.pool_flags)
            spec = JOKER_REGISTRY[shop_pool.NAME_BY_JOKER_KEY[key]]
            # At the run's edition rate, like every create_card joker
            # (common_events.lua:2071-2076, 2149).
            edition = _EDITION_BY_NAME[shop_pool.poll_edition(
                self.rng, "edi%s%d" % (append, self.ante),
                edition_rate=self.edition_rate)]
            # The tag's joker is free, which the game says by couponing it
            # rather than by pricing it at nothing.
            return ShopSlot("joker", spec.cost, couponed=True,
                            joker=self._made_joker(
                                JokerInstance(spec, edition=edition)))
        return None

    def _apply_shop_tags(self) -> None:
        # What is left for the shop itself: the D6 Tag's free rerolls, the
        # Voucher Tag's extra voucher and the Coupon Tag's free cards. None
        # are modelled yet, and they stay in self.tags rather than being
        # silently dropped.
        return

    def slot_price(self, slot: ShopSlot) -> int:
        """What a shop slot costs right now.

        The game recomputes a card's cost whenever anything that touches it
        changes -- set_cost runs over every card in G.I.CARD -- so a price is
        not fixed when the shop is stocked. Buy an Astronomer and the Planet
        sitting beside it becomes free; take a Coupon Tag and the whole shop
        does. Pricing at stocking time meant the run paid the old price.
        """
        if self.shop_free or slot.couponed:
            return 0
        if (slot.consumable is not None
                and slot.consumable.kind is ConsumableKind.PLANET
                and any(j.spec.free_planets for j in self.active_jokers)):
            return 0
        # set_cost's own order: the discount, then Astronomer's free planets,
        # then a rental's flat dollar, then a coupon. The edition is part of
        # `extra_cost` and so is inside the discount, not after it.
        if slot.joker is not None and slot.joker.rental:
            return 1
        edition = (slot.joker.edition if slot.joker is not None else
                   slot.card.edition if slot.card is not None else
                   Edition.NONE)
        return self.card_cost(slot.base_cost, edition)

    def pack_price(self, pack) -> int:
        """What a booster costs right now -- the price every caller must use.

        Card:set_cost zeroes a Celestial booster while Astronomer is held,
        exactly as it zeroes a Planet (card.lua:380):

            if (self.ability.set == 'Planet' or (self.ability.set == 'Booster'
                and self.ability.name:find('Celestial')))
                and #find_joker('Astronomer') > 0 then self.cost = 0 end

        Buying already knew that, and the legal-action list did not: it asked
        whether the run afforded the *full* price, so a run holding Astronomer
        and $3 was never offered the free pack beside it. Seed HELLO123,
        Blue Deck, stake 1, stood in exactly that shop. One function, so the
        offer and the charge cannot disagree again.
        """
        if self.shop_free:
            return 0
        if (pack.kind is PackKind.CELESTIAL
                and any(j.spec.free_planets for j in self.active_jokers)):
            return 0
        return self.price(pack.cost)

    def _redeem_voucher(self, voucher: Voucher) -> None:
        """What redeeming does beyond the fields read off self.vouchers.

        Most of a voucher is passive -- shop size, hand size, interest cap and
        the rest are summed wherever they are needed. Two are not. Hieroglyph
        and Petroglyph take an ante away there and then, and Grabber and
        Wasteful hand over their extra hand or discard for the round in
        progress rather than only from the next one.
        """
        if voucher.shop_slots and self.shop is not None:
            # change_shop_size fills the shop back up to its new maximum
            # there and then -- and it fills *every* empty slot, not just the
            # one it added. Redeem Overstock in a shop you have already
            # emptied and three new cards appear. The simulator only raised
            # the count for the next shop, so a purchase from the slot the
            # voucher had just created found nothing there.
            while len(self.shop.slots) < self._shop_slot_count():
                forced = self._forced_shop_slot()
                slot = forced if forced is not None else self._roll_slot()
                self.shop.slots.append(self._modify_shop_slot(slot))

        if voucher.ante_shift:
            # No floor. ease_ante is a bare addition, and the ante really does
            # go to zero and below -- measured: two Hieroglyphs at ante one
            # leave it at minus one, with get_blind_amount returning 100 for
            # anything under one. Clamping at one was not a safety net but a
            # divergence: it kept the blind at 300 where the engine asks 100,
            # and it named every pool for the wrong ante, so the whole shop
            # stream went with it.
            self.ante += voucher.ante_shift
        self.hands_left += voucher.extra_hands
        self.discards_left += voucher.extra_discards

    def _leave_shop(self) -> None:
        # A Blueprint or a Brainstorm on a Perkeo makes a copy of its own.
        for joker, answer in self.calculating_hooks("on_shop_end"):
            answer(joker, self)
        # The blind index and the ante moved on at cash-out; leaving the shop
        # only chooses which blind is now on offer.
        if self.shop is not None:
            self.reroll_cost_carried = self.shop.reroll_cost(
                sum(v.reroll_discount for v in self.vouchers))
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
            showman=any(j.spec.allows_duplicates for j in self.active_jokers),
            pool_flags=self.pool_flags,
            stickers=self.sticker_rules,
            # Two vouchers change what a pack holds rather than what it
            # costs: Telescope forces the first card of a Celestial pack to
            # the planet for the hand the run has played most, and Omen Globe
            # turns one in five Arcana cards into a Spectral.
            # Hone and Glow Up raise the run's edition rate, and a Standard
            # pack's cards are polled with it as well as with the pack's own
            # doubling -- the game multiplies the two (card.lua:1761).
            edition_rate=self.edition_rate,
            telescope=any(v.key == "v_telescope" for v in self.vouchers),
            omen_globe=any(v.key == "v_omen_globe" for v in self.vouchers),
            most_played_planet=self._most_played_planet())

        self.pack_options = []
        for entry in contents:
            self.pack_options.append(self._pack_card(entry))

        # The open_booster jokers come after the pack is filled, because that
        # is when what they make is built. Card:open queues the fill as an
        # unblockable event (card.lua:1725) and Hallucination's Tarot as a
        # blockable one (card.lua:2339), and Card:explode has already put
        # unblockable *blocking* events ahead of both that last
        # 1.5*explode_time (card.lua:2002, 2071). The fill goes at
        # 1.3*sqrt(GAMESPEED), the Tarot at 1.95*sqrt(GAMESPEED), so the
        # pack's Tarots are blanked from Hallucination's pool rather than its
        # Tarot from the pack's. A copied Hallucination rolls again.
        for joker, answer in self.calculating_hooks("on_pack_open"):
            answer(joker, self)
        self.phase = Phase.PACK

        # An Arcana or a Spectral pack deals a hand. Its cards need targets --
        # a Tarot converts cards, Cryptid copies one -- so the game draws to
        # the hand limit when the pack opens even in the middle of a shop, and
        # sends the hand back to the deck when it closes. The other three
        # packs deal nothing.
        if spec.kind in (PackKind.ARCANA, PackKind.SPECTRAL) and not self.hand:
            self._pack_dealt_hand = True
            self._draw_to_hand_size()

    def _most_played_planet(self) -> str | None:
        """The Planet for the hand this run has played most, for Telescope.

        Walked in G.handlist order with a strict `>`, so the first hand to
        hold the maximum keeps it -- and handlist runs strongest first. A tie
        between a Pair and a Two Pair, which is most of an early run, goes to
        the Two Pair. Python's max over a dict keyed in enum order gave the
        first *weakest* instead, so Telescope forced the wrong planet.
        """
        plays = {h: n for h, n in self.hand_levels.plays.items() if n > 0}
        if not plays:
            return None
        # max keeps the first maximum it meets, and HANDLIST is
        # strongest-first, which is the strict > exactly.
        best = max(HANDLIST, key=lambda h: plays.get(h, 0))
        name = PLANET_FOR_HAND.get(best)
        return shop_pool.KEY_BY_CONSUMABLE_NAME.get(name) if name else None

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
            return self._made_joker(joker)
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
            self.gain_joker(choice)
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
            self.use_consumable(choice, targets, from_pack=True)
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

    def _swap_actions(self) -> list[Action]:
        """Moving a joker one place to its left.

        The engine has a drag and puts no phase gate on it, and the
        environment's mask offers it wherever the row holds two. Here the row
        *is* the list and the order decides which joker resolves first, so
        this is the same edit -- and it is a real move rather than
        decoration: Blueprint copies the joker to its right, and a bought
        joker lands at the right-hand end with nothing there.
        """
        return [Action(ActionType.SWAP_JOKER_LEFT, index=i)
                for i in range(1, len(self.jokers))]

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
        if self.phase is Phase.ROUND_EVAL:
            # Take the money, or spend what you are holding first. A player
            # who has just seen which hand they played will often level it
            # here rather than wait for the shop.
            actions = [Action(ActionType.CASH_OUT)]
            actions += self._consumable_actions()
            actions += [Action(ActionType.SELL_CONSUMABLE, index=i)
                        for i in range(len(self.consumables))]
            actions += [Action(ActionType.SELL_JOKER, index=i)
                        for i, j in enumerate(self.jokers) if not j.eternal]
            actions += self._swap_actions()
            return actions

        if self.phase is Phase.BLIND_SELECT:
            actions = [Action(ActionType.SELECT_BLIND)]
            if self.blind is not None and self.blind.kind is not BlindKind.BOSS:
                actions.append(Action(ActionType.SKIP_BLIND))
            return actions

        if self.phase is Phase.PLAYING:
            actions: list[Action] = self._play_actions()
            if self.discards_left > 0:
                # Cerulean Bell keeps its card highlighted, so a *discard*
                # cannot go without it either -- `is_legal` has always said
                # so and this list did not, which is the two answers
                # disagreeing rather than either being wrong on its own. A
                # policy that reads the list and proposes from it had its
                # discard refused 247 decisions into a run.
                #
                # No "unless nothing is left" escape here, unlike the plays:
                # a hand that cannot be discarded can always be played, so
                # dropping the restriction would invent a move rather than
                # avoid a deadlock.
                actions += [Action(ActionType.DISCARD, cards=s)
                            for s in self._card_subsets(MAX_PLAYED)
                            if self._restriction_ok(s)]
            actions += self._consumable_actions()
            actions += [Action(ActionType.SELL_JOKER, index=i)
                        for i, j in enumerate(self.jokers) if not j.eternal]
            actions += self._swap_actions()
            return actions

        if self.phase is Phase.SHOP:
            assert self.shop is not None
            actions = [Action(ActionType.LEAVE_SHOP)]
            for i, slot in enumerate(self.shop.slots):
                if not self.affords(self.slot_price(slot)):
                    continue
                if slot.kind == "joker" and not self.room_for_joker(slot.joker):
                    continue
                if slot.kind == "consumable" and len(self.consumables) >= self.consumable_slots:
                    continue
                actions.append(Action(ActionType.BUY, index=i))
            for i, pack in enumerate(self.shop.packs):
                if self.affords(self.pack_price(pack)):
                    actions.append(Action(ActionType.BUY_PACK, index=i))
            for i, voucher in enumerate(self.shop.vouchers_on_offer()):
                if self.affords(self.price(voucher.cost)):
                    actions.append(Action(ActionType.BUY_VOUCHER, index=i))
            discount = sum(v.reroll_discount for v in self.vouchers)
            if self.affords(self.shop.reroll_cost(discount)):
                actions.append(Action(ActionType.REROLL))
            actions += self._consumable_actions()
            actions += [Action(ActionType.SELL_JOKER, index=i)
                        for i, j in enumerate(self.jokers) if not j.eternal]
            actions += self._swap_actions()
            actions += [Action(ActionType.SELL_CONSUMABLE, index=i)
                        for i in range(len(self.consumables))]
            return actions

        if self.phase is Phase.PACK:
            actions = [Action(ActionType.SKIP_PACK)]
            for i, option in enumerate(self.pack_options):
                if isinstance(option, JokerInstance):
                    if self.room_for_joker(option):
                        actions.append(Action(ActionType.PICK_PACK, index=i))
                elif isinstance(option, Card):
                    actions.append(Action(ActionType.PICK_PACK, index=i))
                else:
                    actions += self._pack_consumable_actions(i, option)
            # An open booster does not lock the row. Card:can_sell_card
            # (card.lua:1640-1653) refuses a sale only while cards are being
            # played, the controller is locked or STOP_USE is up, and asks of
            # the card only that its area is a joker-type area -- which both
            # G.jokers and G.consumeables are (game.lua:2235-2245). So a full
            # row sells a joker to take the one in the pack, which is the
            # ordinary way to play a Buffoon pack. The environment's mask has
            # always offered it; only this list refused.
            actions += [Action(ActionType.SELL_JOKER, index=i)
                        for i, j in enumerate(self.jokers) if not j.eternal]
            actions += [Action(ActionType.SELL_CONSUMABLE, index=i)
                        for i in range(len(self.consumables))]
            actions += self._swap_actions()
            return actions

        return []

    def _pack_consumable_actions(self, index: int,
                                 spec: ConsumableSpec) -> list[Action]:
        if spec.targets == 0:
            # A consumable in a pack gets its button from can_use_consumeable
            # -- UI_definitions.lua, use_and_sell_buttons, the branch for
            # `card.ability.consumeable` in G.pack_cards -- so a Judgement is
            # not takeable into a full joker row any more than it is usable
            # from a slot. can_select_card, the looser test, is only for the
            # jokers and playing cards a pack offers. This offered it
            # unconditionally, and a policy reading the legal set took
            # Judgements the game would have greyed out.
            if not self._usable_now(spec):
                return []
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
                if self._usable_now(spec):
                    actions.append(Action(ActionType.USE_CONSUMABLE, index=i))
                continue
            if not self.hand:
                continue
            for subset in self._card_subsets(spec.max_targets or spec.targets):
                if not spec.accepts(len(subset)):
                    continue
                if self._usable_now(
                        spec, tuple(self.hand[j] for j in subset)):
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
        """Whether a play is *legal*, which is narrower than it looks.

        Only Cerulean Bell belongs here. It sets ability.forced_selection on
        a card and keeps it highlighted, so the game will genuinely not let
        the hand go without it.

        The Psychic, The Eye and The Mouth used to be here too, and they are
        not legality at all: Blind:debuff_hand answers "is this hand
        debuffed", and a debuffed hand is played, consumes a hand, counts as
        played, and scores nothing. Forbidding the play instead gave the
        simulator a smaller action space than the engine -- a policy trained
        against the engine would offer a four-card hand into The Psychic,
        which the engine accepts and zeroes, and the simulator refuse it.
        """
        boss = self.boss
        if boss is None:
            return True
        if boss.forces_a_card and self.forced_card is not None:
            if not any(self.hand[i] is self.forced_card for i in cards
                       if i < len(self.hand)):
                return False
        return True

    def hand_is_debuffed(self, hand: HandType, cards: list) -> bool:
        """Blind:debuff_hand -- the boss zeroing a hand it dislikes.

        The Psychic wants five cards, The Eye a hand type not yet played this
        round, The Mouth the same type as the round's first. Failing any of
        them is allowed; it just scores nothing.

        This is the `check` form of the question and writes nothing; `_play`
        makes the one write the real call makes. The Mouth is not "a type
        played this round" -- a zeroed hand counts as played, and treating it
        as allowed let a second Three of a Kind score 5544 under a Pair's
        Mouth on seed Q4BAHUP3. It is `only_hand` (blind.lua:542-548):

            if self.only_hand and self.only_hand ~= handname then
                return true end
            if not check then self.only_hand = handname end
        """
        boss = self.boss
        if boss is None or (self.blind is not None and self.blind.disabled):
            return False
        if boss.min_cards_played and len(cards) < boss.min_cards_played:
            return True
        if boss.no_repeat_hand and hand in self.hands_played_this_round:
            return True
        if (boss.lock_first_hand_type and self.mouth_only_hand is not None
                and hand is not self.mouth_only_hand):
            return True
        return False

    def _restriction_satisfiable(self) -> bool:
        """Whether any subset satisfies the boss restriction (see _play_actions)."""
        boss = self.boss
        if boss is None:
            return True
        if not boss.forces_a_card:
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
            if cards:
                return False
        elif not self._valid_indices(cards, spec.max_targets or spec.targets):
            return False
        return self._usable_now(
            spec, tuple(self.hand[i] for i in cards))

    def is_legal(self, action: Action) -> bool:
        """Exact membership test for `legal_actions()` without building the list."""
        t, index, cards = action.type, action.index, action.cards

        # Before the phases, because the row can be rearranged in any of
        # them -- `_swap_actions` is added to every list for the same reason.
        if t is ActionType.SWAP_JOKER_LEFT:
            return 1 <= index < len(self.jokers)

        if self.phase is Phase.ROUND_EVAL:
            # The game lets you use and sell what you are holding before you
            # take the money, and nothing else.
            if t is ActionType.CASH_OUT:
                return True
            if t is ActionType.USE_CONSUMABLE:
                return self._consumable_legal(index, cards)
            if t is ActionType.SELL_CONSUMABLE:
                return 0 <= index < len(self.consumables)
            if t is ActionType.SELL_JOKER:
                return (0 <= index < len(self.jokers)
                        and not self.jokers[index].eternal)
            return False

        if self.phase is Phase.BLIND_SELECT:
            if t is ActionType.SELECT_BLIND:
                return not cards
            if t is ActionType.SKIP_BLIND:
                return self.blind is not None and self.blind.kind is not BlindKind.BOSS
            if t is ActionType.REROLL_BOSS:
                return self.can_reroll_boss
            return False

        if self.phase is Phase.PLAYING:
            if t is ActionType.PLAY:
                if not self._valid_indices(cards, MAX_PLAYED):
                    return False
                return (self._restriction_ok(cards)
                        or not self._restriction_satisfiable())
            if t is ActionType.DISCARD:
                return (self.discards_left > 0
                        and self._valid_indices(cards, MAX_PLAYED)
                        and self._restriction_ok(cards))
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
                if not self.affords(self.slot_price(slot)):
                    return False
                if slot.kind == "joker":
                    return self.room_for_joker(slot.joker)
                if slot.kind == "consumable":
                    return len(self.consumables) < self.consumable_slots
                return True
            if t is ActionType.BUY_PACK:
                return (0 <= index < len(shop.packs)
                        and self.affords(self.price(shop.packs[index].cost)))
            if t is ActionType.BUY_VOUCHER:
                offered = shop.vouchers_on_offer()
                return (0 <= index < len(offered)
                        and self.affords(self.price(offered[index].cost)))
            if t is ActionType.REROLL:
                discount = sum(v.reroll_discount for v in self.vouchers)
                return self.affords(shop.reroll_cost(discount))
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
            # Selling inside a pack: see legal_actions.
            if t is ActionType.SELL_JOKER:
                return (0 <= index < len(self.jokers)
                        and not self.jokers[index].eternal)
            if t is ActionType.SELL_CONSUMABLE:
                return 0 <= index < len(self.consumables)
            if t is ActionType.PICK_PACK:
                if not 0 <= index < len(self.pack_options):
                    return False
                option = self.pack_options[index]
                if isinstance(option, JokerInstance):
                    return not cards and self.room_for_joker(option)
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
            # Counted before the tag is handed over, which is the game's own
            # order and the reason a Speed Tag pays for its own skip. Nothing
            # incremented this at all before, so Throwback -- X0.25 per blind
            # skipped -- read zero for the whole of every run.
            self.blinds_skipped += 1
            self.skipped_this_ante.add(self.blind_index)
            self.add_tag_by_key(key)
            self.log("Skipped %s, gained %s"
                     % (self.blind.name, tag.value if tag else key))
            self._fire_immediate_tags()
            self.blind_index += 1
            self._next_blind()
            # The game fires new_blind_choice from several places, each with
            # its own `break`, and Tag.triggered stops any one tag firing
            # twice. Skipping therefore runs it once for the skip and again
            # for the blind-select screen that follows -- which is how two
            # Boss Tags, doubled off one Double Tag, both re-roll. Firing it
            # only from _next_blind left the second one held for ever.
            #
            # After _next_blind, not before: a pack tag opens its pack here,
            # and _next_blind ends by putting the phase back to BLIND_SELECT,
            # which threw the pack away.
            self._apply_blind_select_tags()
        elif t is ActionType.PLAY:
            self._play(action.cards)
        elif t is ActionType.DISCARD:
            self._discard(action.cards)
        elif t is ActionType.USE_CONSUMABLE:
            spec = self.consumables.pop(action.index)
            targets = [self.hand[i] for i in action.cards]
            self.use_consumable(spec, targets)
        elif t is ActionType.SWAP_JOKER_LEFT:
            i = action.index
            self.jokers[i - 1], self.jokers[i] = (self.jokers[i],
                                                  self.jokers[i - 1])
        elif t is ActionType.SELL_JOKER:
            from .scoring import effective_specs

            # selling_self goes to the sold card alone, while it is still in
            # the row (card.lua:1599), so a Blueprint sold beside a Diet Cola
            # or a Luchador runs theirs -- and a Brainstorm the first joker's.
            spec, source = effective_specs(list(self.jokers))[action.index]
            joker = self.jokers.pop(action.index)
            answers = not joker.debuffed and (
                source is joker
                or spec.name not in self._NOT_COPIED["on_sell"])
            self._move_joker_counters(joker, arriving=False)
            self.add_money(self.sell_value(joker), f"sold {joker.name}")
            self.note_card_sold()
            # Selling is the whole point of some jokers -- Luchador disables
            # the boss, Diet Cola leaves a tag behind -- so the effect fires
            # after it has left the list, as the game does it.
            if spec.disables_boss_on_sell and answers:
                # selling_self is a calculate_joker context, and that returns
                # nothing at all for a debuffed joker.
                self.disable_blind(joker.name)
            boss = self.boss
            if boss is not None and boss.debuff_until_sale:
                # Verdant Leaf lifts the moment any joker is sold, not just
                # Luchador -- that is the whole shape of the blind.
                self.disable_blind("a joker was sold")
            # selling_self (card.lua:1599) is shut to a debuffed joker too.
            if spec.on_sell is not None and answers:
                spec.on_sell(source, self)
        elif t is ActionType.SELL_CONSUMABLE:
            # Priced while it is still held: sell_card pays sell_cost
            # (card.lua:1608) before the card leaves.
            held = self.consumables[action.index]
            price = self.consumable_sell_value(held)
            self.consumables.pop(action.index)
            self.add_money(price, f"sold {held.name}")
            self.note_card_sold()
        elif t is ActionType.BUY:
            self._buy(action.index)
        elif t is ActionType.BUY_AND_USE:
            self.buy_and_use(action.index)
        elif t is ActionType.REROLL_BOSS:
            # A Boss Tag rerolls for nothing and goes through
            # _apply_blind_select_tags instead; this is the paid button.
            self.boss_rerolled = True
            self.add_money(-BOSS_REROLL_COST, "boss reroll")
            self._reroll_boss_blind()
        elif t is ActionType.BUY_VOUCHER:
            assert self.shop is not None
            # index names which of the shop's vouchers, since a Voucher Tag
            # puts a second one beside the round's own.
            voucher = self.shop.vouchers.pop(action.index)
            self.add_money(-self.price(voucher.cost),
                           f"bought {voucher.name}")
            self.vouchers.append(voucher)
            self._redeem_voucher(voucher)
        elif t is ActionType.REROLL:
            assert self.shop is not None
            discount = sum(v.reroll_discount for v in self.vouchers)
            self.add_money(-self.shop.reroll_cost(discount), "reroll")
            if self.shop.free_rerolls > 0:
                self.shop.free_rerolls -= 1
            else:
                self.shop.rerolls += 1
            # The jokers that count rerolls are told before the new cards are
            # made, which is the game's order -- calculate_joker fires on the
            # button, not on the shop that comes back.
            for joker, answer in self.calculating_hooks("on_reroll"):
                answer(joker, self)
            self._fill_shop(self.shop)
        elif t is ActionType.BUY_PACK:
            assert self.shop is not None
            pack = self.shop.packs.pop(action.index)
            cost = self.pack_price(pack)
            self.add_money(-cost, f"bought {pack.name}")
            self._open_pack(pack)
        elif t is ActionType.PICK_PACK:
            self._pick_pack(action.index, action.cards)
        elif t is ActionType.SKIP_PACK:
            for joker, answer in self.calculating_hooks("on_pack_skip"):
                answer(joker, self)
            self._close_pack()
        elif t is ActionType.CASH_OUT:
            self._cash_out()
        elif t is ActionType.LEAVE_SHOP:
            self._leave_shop()
        else:  # pragma: no cover
            raise ValueError(f"unhandled action {action}")

    def _buy(self, index: int) -> None:
        assert self.shop is not None
        slot = self.shop.slots.pop(index)
        self.add_money(-self.slot_price(slot), f"bought {slot.label}")
        if slot.joker is not None:
            self.gain_joker(slot.joker)
        elif slot.consumable is not None:
            self.consumables.append(self.hold_consumable(slot.consumable))
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
