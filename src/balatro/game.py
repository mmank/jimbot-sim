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
from .blinds import Blind, BlindKind, BossEffect, make_blind, pick_boss
from .cards import Card, Edition, Enhancement, Rank, Seal, Suit, standard_deck
from .consumables import ConsumableKind, ConsumableSpec
from .hands import PLANET_FOR_HAND, HandLevels, HandType, evaluate
from .jokers import REGISTRY as JOKER_REGISTRY, JokerInstance, Rarity
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
WIN_ANTE = 8


class Phase(Enum):
    BLIND_SELECT = "blind_select"
    PLAYING = "playing"
    SHOP = "shop"
    PACK = "pack"
    GAME_OVER = "game_over"
    WON = "won"


class ActionType(Enum):
    SELECT_BLIND = "select_blind"
    SKIP_BLIND = "skip_blind"
    PLAY = "play"
    DISCARD = "discard"
    USE_CONSUMABLE = "use_consumable"
    SELL_JOKER = "sell_joker"
    SELL_CONSUMABLE = "sell_consumable"
    BUY = "buy"
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
    INVESTMENT = "Investment Tag"
    ECONOMY = "Economy Tag"
    JUGGLE = "Juggle Tag"


TAG_POOL = list(Tag)


@dataclass
class GameState:
    seed: int = 0
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
    ante_tags: list[Tag] = field(default_factory=list)  # skip rewards, [small, big]

    hand_levels: HandLevels = field(default_factory=HandLevels.new)

    base_hand_size: int = BASE_HAND_SIZE
    joker_slots: int = BASE_JOKER_SLOTS

    blind: Blind | None = None
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
    pack_options: list = field(default_factory=list)
    pack_picks_left: int = 0

    logs: list[str] = field(default_factory=list)
    verbose: bool = False
    _satisfiable_cache: tuple | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.rng = RunRng(self.seed)
        if not self.full_deck:
            self.full_deck = standard_deck()
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
        if joker in self.jokers:
            self.jokers.remove(joker)
            self.log(f"{joker.name} destroyed{f' ({reason})' if reason else ''}")

    def remove_card(self, card: Card) -> None:
        for pile in (self.full_deck, self.draw_pile, self.hand, self.discard_pile):
            if card in pile:
                pile.remove(card)

    def add_card(self, card: Card) -> None:
        self.full_deck.append(card)
        self.draw_pile.append(card)
        for joker in self.jokers:
            if joker.name == "Hologram":
                joker.counter += 0.25

    def random_face_card(self) -> Card:
        rank = self.rng.choice("face_card", [Rank.JACK, Rank.QUEEN, Rank.KING])
        return Card(rank, self.rng.choice("face_suit", list(Suit)))

    def random_consumables(self, kind: ConsumableKind, count: int) -> list[ConsumableSpec]:
        pool = cons.by_kind(kind)
        return [self.rng.choice(f"consumable_{kind.value}", pool) for _ in range(count)]

    def add_consumables(self, specs: list[ConsumableSpec]) -> None:
        for spec in specs:
            if len(self.consumables) < self.consumable_slots:
                self.consumables.append(spec)

    def add_random_joker(self, source: str = "", rarity: Rarity | None = None) -> None:
        if len(self.jokers) >= self.joker_slots:
            return
        if rarity is None:
            spec = shop_mod.random_joker_spec(self.rng, "granted")
        else:
            pool = [s for s in JOKER_REGISTRY.values() if s.rarity is rarity]
            spec = self.rng.choice("granted_rarity", pool)
        self.jokers.append(JokerInstance(spec))
        self.log(f"{source}: gained {spec.name}")

    # ------------------------------------------------------------------
    # derived state
    # ------------------------------------------------------------------

    @property
    def boss(self) -> BossEffect | None:
        if self.blind is None or self.blind.kind is not BlindKind.BOSS:
            return None
        if any(j.name == "Chicot" for j in self.jokers):
            return None
        return self.blind.boss

    @property
    def consumable_slots(self) -> int:
        return BASE_CONSUMABLE_SLOTS + sum(v.consumable_slots for v in self.vouchers)

    @property
    def hand_size(self) -> int:
        size = self.base_hand_size
        size += sum(v.hand_size for v in self.vouchers)
        size += sum(j.spec.hand_size for j in self.jokers)
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

    def _next_blind(self) -> None:
        if self.blind_index == 0:
            # Both skip rewards for the ante are rolled up front, because the
            # real game shows them on the blind select screen -- deciding
            # whether to skip the Small Blind means knowing both tags.
            self.ante_tags = [self.rng.choice(f"tag_{self.ante}_{i}", TAG_POOL)
                              for i in range(2)]
        kind = [BlindKind.SMALL, BlindKind.BIG, BlindKind.BOSS][self.blind_index]
        boss = pick_boss(self.rng, self.ante) if kind is BlindKind.BOSS else None
        self.blind = make_blind(kind, self.ante, boss)
        self.phase = Phase.BLIND_SELECT

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
        for joker in list(self.jokers):
            if joker.spec.on_round_start is not None:
                joker.spec.on_round_start(joker, self)

        hands = BASE_HANDS + sum(v.extra_hands for v in self.vouchers)
        hands += sum(j.spec.extra_hands for j in self.jokers)
        discards = BASE_DISCARDS + sum(v.extra_discards for v in self.vouchers)
        discards += sum(j.spec.extra_discards for j in self.jokers)
        boss = self.boss
        if boss is not None:
            hands = max(1, hands + boss.hands_delta) if boss.hands_delta > -50 else 1
            discards = max(0, discards + boss.discards_delta) if boss.discards_delta > -50 else 0
        self.hands_left = hands
        self.discards_left = discards

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

    def _draw_to_hand_size(self) -> None:
        while len(self.hand) < self.hand_size and self.draw_pile:
            self.hand.append(self.draw_pile.pop())
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
        self.rng = RunRng(self.seed ^ 0xA5A5)
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
            if boss.level_down_played_hand:
                self.hand_levels.levels[result.hand] = max(
                    1, self.hand_levels.levels[result.hand] - 1)
            if boss.zero_money_on_most_played and self._is_most_played(result.hand):
                self.money = 0
                self.log(f"{boss.name}: money set to $0")

        for card in held:
            if card.seal is Seal.BLUE:
                self.add_consumables([cons.REGISTRY[PLANET_FOR_HAND[result.hand]]])

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
            for card in self.rng.sample("hook", self.hand,
                                        min(boss.discard_random_on_play, len(self.hand))):
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
                self.add_consumables(self.random_consumables(ConsumableKind.TAROT, 1))
            self.hand.remove(card)
            self.discard_pile.append(card)
        self._draw_to_hand_size()

    def _beat_blind(self) -> None:
        assert self.blind is not None
        reward = self.blind.reward
        unused = max(0, self.hands_left)
        interest = min(self.interest_cap, max(0, self.money) // 5)
        self.add_money(reward, f"{self.blind.name} reward")
        self.add_money(unused, "unused hands")
        self.add_money(interest, "interest")

        for card in self.hand:
            if card.enhancement is Enhancement.GOLD:
                self.add_money(3, "gold card")

        for joker in list(self.jokers):
            if joker.spec.round_end is not None:
                joker.spec.round_end(joker, self)

        # The hand is gone once the blind is beaten, so targeted consumables
        # cannot be used again until the next round starts.
        self.discard_pile.extend(self.hand)
        self.hand = []

        was_boss = self.blind.kind is BlindKind.BOSS
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

    def _roll_slot(self, tag: str) -> ShopSlot:
        kind = shop_mod.weighted_pick(self.rng, f"{tag}_kind", shop_mod.SLOT_WEIGHTS)
        if kind == "joker":
            spec = shop_mod.random_joker_spec(self.rng, tag)
            edition = shop_mod.random_edition(self.rng, f"{tag}_edition")
            joker = JokerInstance(spec, edition=edition)
            return ShopSlot("joker", self.price(shop_mod.joker_price(spec, edition)),
                            joker=joker)
        kind_enum = ConsumableKind.TAROT if kind == "tarot" else ConsumableKind.PLANET
        spec = self.rng.choice(f"{tag}_consumable", cons.by_kind(kind_enum))
        return ShopSlot("consumable", self.price(spec.cost), consumable=spec)

    def _fill_shop(self, shop: Shop) -> None:
        shop.slots = [self._roll_slot(f"shop_{self.round_number}_{i}_{shop.rerolls}")
                      for i in range(self._shop_slot_count())]

    def _open_shop(self) -> None:
        shop = Shop()
        self._fill_shop(shop)
        shop.packs = [
            self._roll_pack(f"pack_{self.round_number}_{i}") for i in range(2)
        ]
        owned = {v.name for v in self.vouchers}
        available = [v for v in shop_mod.VOUCHERS if v.name not in owned]
        shop.voucher = self.rng.choice("voucher", available) if available else None
        self.shop = shop
        self.phase = Phase.SHOP
        self._apply_shop_tags()

    def _roll_pack(self, tag: str) -> PackSpec:
        kind = shop_mod.weighted_pick(self.rng, f"{tag}_kind",
                                      shop_mod.PACK_APPEARANCE_WEIGHTS)
        options = [p for p in shop_mod.PACKS if p.kind is kind]
        return self.rng.choice(f"{tag}_size", options)

    def _apply_shop_tags(self) -> None:
        for tag in list(self.tags):
            if tag is Tag.UNCOMMON:
                self.add_random_joker(tag.value, Rarity.UNCOMMON)
            elif tag is Tag.RARE:
                self.add_random_joker(tag.value, Rarity.RARE)
            elif tag is Tag.ECONOMY:
                self.add_money(min(40, max(0, self.money)), tag.value)
            elif tag in (Tag.CHARM, Tag.METEOR, Tag.BUFFOON):
                kind = {Tag.CHARM: PackKind.ARCANA, Tag.METEOR: PackKind.CELESTIAL,
                        Tag.BUFFOON: PackKind.BUFFOON}[tag]
                free = next(p for p in shop_mod.PACKS
                            if p.kind is kind and p.size == "normal")
                self._open_pack(free)
            elif tag is Tag.JUGGLE:
                continue  # consumed at round start; modelled as a no-op for now
            else:
                continue
            self.tags.remove(tag)

    def _leave_shop(self) -> None:
        self.shop = None
        self.blind_index += 1
        if self.blind_index > 2:
            self.blind_index = 0
            self.ante += 1
        self._next_blind()

    # ------------------------------------------------------------------
    # packs
    # ------------------------------------------------------------------

    def _open_pack(self, spec: PackSpec) -> None:
        self.pack = spec
        self.pack_picks_left = spec.picks
        tag = f"packopen_{self.round_number}_{spec.name}"
        if spec.kind is PackKind.ARCANA:
            pool = cons.by_kind(ConsumableKind.TAROT)
            self.pack_options = [self.rng.choice(tag, pool) for _ in range(spec.options)]
        elif spec.kind is PackKind.CELESTIAL:
            pool = cons.by_kind(ConsumableKind.PLANET)
            self.pack_options = [self.rng.choice(tag, pool) for _ in range(spec.options)]
        elif spec.kind is PackKind.SPECTRAL:
            pool = cons.by_kind(ConsumableKind.SPECTRAL)
            self.pack_options = [self.rng.choice(tag, pool) for _ in range(spec.options)]
        elif spec.kind is PackKind.BUFFOON:
            self.pack_options = [
                JokerInstance(shop_mod.random_joker_spec(self.rng, f"{tag}_{i}"))
                for i in range(spec.options)
            ]
        else:  # standard playing cards
            self.pack_options = [self._random_playing_card(f"{tag}_{i}")
                                 for i in range(spec.options)]
        self.phase = Phase.PACK

    def _random_playing_card(self, tag: str) -> Card:
        card = Card(self.rng.choice(f"{tag}_rank", list(Rank)),
                    self.rng.choice(f"{tag}_suit", list(Suit)))
        if self.rng.chance(f"{tag}_enh", 2, 5):
            card.enhancement = self.rng.choice(
                f"{tag}_which", [e for e in Enhancement if e is not Enhancement.NONE])
        card.edition = shop_mod.random_edition(self.rng, f"{tag}_ed")
        return card

    def _close_pack(self) -> None:
        self.pack = None
        self.pack_options = []
        self.pack_picks_left = 0
        self.phase = Phase.SHOP if self.shop is not None else Phase.BLIND_SELECT

    def _pick_pack(self, index: int, card_indices: tuple[int, ...]) -> None:
        choice = self.pack_options[index]
        if isinstance(choice, JokerInstance):
            self.jokers.append(choice)
            self.log(f"Pack: took {choice.name}")
        elif isinstance(choice, Card):
            self.add_card(choice)
            self.log(f"Pack: added {choice} to deck")
        else:  # ConsumableSpec
            if choice.targets > 0 or choice.kind is ConsumableKind.TAROT:
                targets = [self.hand[i] for i in card_indices]
                if choice.apply is not None and choice.accepts(len(targets)):
                    choice.apply(self, targets)
                    self.log(f"Pack: used {choice.name}")
                elif len(self.consumables) < self.consumable_slots:
                    self.consumables.append(choice)
            elif len(self.consumables) < self.consumable_slots:
                self.consumables.append(choice)
            else:
                if choice.apply is not None:
                    choice.apply(self, [])
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
            self.tags.append(tag)
            self.log(f"Skipped {self.blind.name}, gained {tag.value}")
            self.blind_index += 1
            self._next_blind()
        elif t is ActionType.PLAY:
            self._play(action.cards)
        elif t is ActionType.DISCARD:
            self._discard(action.cards)
        elif t is ActionType.USE_CONSUMABLE:
            spec = self.consumables.pop(action.index)
            targets = [self.hand[i] for i in action.cards]
            if spec.apply is not None:
                spec.apply(self, targets)
            self.log(f"Used {spec.name}")
        elif t is ActionType.SELL_JOKER:
            joker = self.jokers.pop(action.index)
            self.add_money(joker.sell_value, f"sold {joker.name}")
            self.cards_sold += 1
            # Selling is the whole point of some jokers -- Luchador disables
            # the boss, Diet Cola leaves a tag behind -- so the effect fires
            # after it has left the list, as the game does it.
            if joker.spec.on_sell is not None:
                joker.spec.on_sell(joker, self)
        elif t is ActionType.SELL_CONSUMABLE:
            spec = self.consumables.pop(action.index)
            self.add_money(max(1, spec.cost // 2), f"sold {spec.name}")
            self.cards_sold += 1
        elif t is ActionType.BUY:
            self._buy(action.index)
        elif t is ActionType.BUY_VOUCHER:
            assert self.shop is not None and self.shop.voucher is not None
            self.add_money(-self.price(self.shop.voucher.cost),
                           f"bought {self.shop.voucher.name}")
            self.vouchers.append(self.shop.voucher)
            self.shop.voucher_bought = True
        elif t is ActionType.REROLL:
            assert self.shop is not None
            discount = sum(v.reroll_discount for v in self.vouchers)
            self.add_money(-self.shop.reroll_cost(discount), "reroll")
            self.shop.rerolls += 1
            self._fill_shop(self.shop)
        elif t is ActionType.BUY_PACK:
            assert self.shop is not None
            pack = self.shop.packs.pop(action.index)
            self.add_money(-self.price(pack.cost), f"bought {pack.name}")
            self._open_pack(pack)
        elif t is ActionType.PICK_PACK:
            self._pick_pack(action.index, action.cards)
        elif t is ActionType.SKIP_PACK:
            self._close_pack()
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
