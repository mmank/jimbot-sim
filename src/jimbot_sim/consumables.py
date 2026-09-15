"""Tarot, Planet and Spectral cards.

Like jokers, consumables are a registry of specs. `targets` is how many cards
from the current hand the effect needs; the engine only offers a consumable as a
legal action when that many cards are selected.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable

from .cards import Card, Edition, Enhancement, Rank, Seal, Suit
from .hands import PLANET_FOR_HAND, HandType

if TYPE_CHECKING:  # pragma: no cover
    from .game import GameState


class ConsumableKind(Enum):
    TAROT = "tarot"
    PLANET = "planet"
    SPECTRAL = "spectral"


ApplyHook = Callable[["GameState", list[Card]], None]


@dataclass(frozen=True)
class ConsumableSpec:
    name: str
    kind: ConsumableKind
    text: str
    targets: int = 0
    max_targets: int | None = None
    apply: ApplyHook | None = None
    cost: int = 3

    def accepts(self, n_selected: int) -> bool:
        low = self.targets
        high = self.max_targets if self.max_targets is not None else self.targets
        return low <= n_selected <= high

    def __reduce__(self):
        """Pickle and copy as the registry entry, by name.

        Same reason as JokerSpec.__reduce__, and the same closure: `apply` is
        built by _planet_apply and its kin, which pickle cannot reach. A
        consumable held in the row would otherwise make a run unserialisable
        exactly as a joker does.
        """
        return (_registered, (self.name,))


@dataclass
class ConsumableInstance:
    """One consumable actually held, rather than the kind of thing it is.

    The registry entry is a centre -- what a Death is. This is a card: what
    *this* Death is, which for a consumable means its edition and nothing
    else. Perkeo is why the difference has to exist. Its copy is Negative,
    and Card:add_to_deck raises G.consumeables' card limit for a negative
    consumable exactly as it raises the joker limit for a negative joker, so
    the copy costs no slot. Holding the row as shared registry singletons
    left nowhere to record that, and no way to tell which of two Fools was
    the free one when a Fool was later used.

    Everything the spec answers is forwarded, so `held.name`, `held.targets`
    and `held.accepts(n)` read the same as they always did.
    """

    spec: ConsumableSpec
    edition: Edition = Edition.NONE

    def __getattr__(self, name: str):
        # Only reached for names the instance itself does not define. The
        # guard matters during copy and unpickle, when spec is not set yet
        # and the delegation would recurse.
        if name == "spec":
            raise AttributeError(name)
        return getattr(self.spec, name)

    def __repr__(self) -> str:
        if self.edition is Edition.NONE:
            return "<%s>" % self.spec.name
        return "<%s %s>" % (self.edition.value, self.spec.name)


REGISTRY: dict[str, ConsumableSpec] = {}


def _registered(name: str) -> ConsumableSpec:
    """The spec of this name, for ConsumableSpec.__reduce__ to restore through."""
    try:
        return REGISTRY[name]
    except KeyError:
        raise LookupError(
            "no consumable named %r is registered, so a copy of one cannot "
            "be restored; every spec is built inside register()" % (name,)
        ) from None


def register(spec: ConsumableSpec) -> ConsumableSpec:
    REGISTRY[spec.name] = spec
    return spec


# --------------------------------------------------------------------------
# planets
# --------------------------------------------------------------------------

def _planet_apply(hand: HandType) -> ApplyHook:
    def apply(game: "GameState", cards: list[Card]) -> None:
        game.hand_levels.level_up(hand)
        game.log(f"{PLANET_FOR_HAND[hand]}: {hand.label} to level "
                 f"{game.hand_levels.levels[hand]}")
    return apply


for _hand, _planet in PLANET_FOR_HAND.items():
    register(ConsumableSpec(_planet, ConsumableKind.PLANET,
                            f"Level up {_hand.label}", apply=_planet_apply(_hand)))


# --------------------------------------------------------------------------
# tarots
# --------------------------------------------------------------------------

def _enhance(enh: Enhancement) -> ApplyHook:
    def apply(game: "GameState", cards: list[Card]) -> None:
        for card in cards:
            game.set_enhancement(card, enh)
    return apply


def _to_suit(suit: Suit) -> ApplyHook:
    """The four suit tarots. set_suit, because a converted card remembers.

    Card:set_base carries suit_nominal_original across the change, and that
    remembered suit orders the hand -- so a Heart turned into a Spade sits
    behind a natural Spade of the same rank.
    """
    def apply(game: "GameState", cards: list[Card]) -> None:
        for card in cards:
            card.set_suit(suit)
    return apply


def _tarot(name: str, text: str, targets: int, apply: ApplyHook,
           max_targets: int | None = None) -> None:
    register(ConsumableSpec(name, ConsumableKind.TAROT, text, targets=targets,
                            max_targets=max_targets, apply=apply))


_tarot("The Magician", "Enhance 2 cards into Lucky Cards", 1, _enhance(Enhancement.LUCKY), 2)
_tarot("The Empress", "Enhance 2 cards into Mult Cards", 1, _enhance(Enhancement.MULT), 2)
_tarot("The Hierophant", "Enhance 2 cards into Bonus Cards", 1, _enhance(Enhancement.BONUS), 2)
_tarot("The Lovers", "Enhance 1 card into a Wild Card", 1, _enhance(Enhancement.WILD))
_tarot("The Chariot", "Enhance 1 card into a Steel Card", 1, _enhance(Enhancement.STEEL))
_tarot("Justice", "Enhance 1 card into a Glass Card", 1, _enhance(Enhancement.GLASS))
_tarot("The Devil", "Enhance 1 card into a Gold Card", 1, _enhance(Enhancement.GOLD))
_tarot("The Tower", "Enhance 1 card into a Stone Card", 1, _enhance(Enhancement.STONE))
_tarot("The Star", "Convert up to 3 cards to Diamonds", 1, _to_suit(Suit.DIAMONDS), 3)
_tarot("The Moon", "Convert up to 3 cards to Clubs", 1, _to_suit(Suit.CLUBS), 3)
_tarot("The Sun", "Convert up to 3 cards to Hearts", 1, _to_suit(Suit.HEARTS), 3)
_tarot("The World", "Convert up to 3 cards to Spades", 1, _to_suit(Suit.SPADES), 3)


def _strength(game: "GameState", cards: list[Card]) -> None:
    order = list(Rank)
    for card in cards:
        card.rank = order[(order.index(card.rank) + 1) % len(order)]


def _hanged_man(game: "GameState", cards: list[Card]) -> None:
    """Destroy the selected cards, paying Glass Joker by a side door.

    Every other tarot that destroys leaves Glass Joker with nothing, because
    the shatter is queued behind the joker check -- see
    GameState.note_cards_destroyed. The Hanged Man is the one the game
    patched around, with a second handler that fires on the consumable being
    used and counts the selected Glass Cards directly, so it pays whether or
    not the flag has caught up.
    """
    glass = [c for c in cards if c.enhancement is Enhancement.GLASS]
    if glass:
        for joker in list(game.jokers):
            if joker.spec.on_glass_shattered is not None:
                joker.spec.on_glass_shattered(joker, list(glass), game)
    for card in cards:
        game.remove_card(card)


def _death(game: "GameState", cards: list[Card]) -> None:
    """Convert the left selected card into the right one.

    Exactly two, never more: c_death is `max_highlighted = 2,
    min_highlighted = 2`, and can_use_consumeable will not let the card be
    used at any other count. The loop over the highlighted cards in the game's
    own implementation is defensive rather than reachable.

    Which one is "right" is decided by screen position rather than by the
    order the cards were clicked in, so it is resolved against the hand here
    rather than trusting the order the caller passed them in.
    """
    if len(cards) != 2:
        return
    order = {id(c): i for i, c in enumerate(game.hand)}
    left, right = sorted(cards, key=lambda c: order.get(id(c), -1))
    left.rank = right.rank
    left.set_suit(right.suit)
    left.enhancement, left.edition, left.seal = (
        right.enhancement, right.edition, right.seal)
    # copy_card walks every field of the card's ability table, so everything
    # living there comes across, not just what is printed on the card. Two of
    # those fields matter:
    #
    #   perma_bonus         the chips a Hiker left. Copying the visible
    #                       properties and stopping loses five chips a
    #                       trigger on a card played for the rest of the run.
    #   played_this_ante    whether the card has already been played this
    #                       ante, which is exactly what The Pillar debuffs.
    #                       So a Death can debuff a fresh card by copying a
    #                       spent one onto it, and launder a spent one by
    #                       copying a fresh one the other way.
    #
    # The rest of the table is the enhancement's own numbers -- bonus, mult,
    # h_dollars and so on -- which come across with the enhancement itself.
    left.extra_chips = right.extra_chips
    left.played_this_ante = right.played_this_ante


def _hermit(game: "GameState", cards: list[Card]) -> None:
    game.add_money(min(20, max(0, game.money)), "The Hermit")


def _temperance(game: "GameState", cards: list[Card]) -> None:
    game.add_money(min(50, sum(game.sell_value(j) for j in game.jokers)),
                   "Temperance")


_tarot("Strength", "Increase the rank of up to 2 cards", 1, _strength, 2)
_tarot("The Hanged Man", "Destroy up to 2 cards", 1, _hanged_man, 2)
_tarot("Death", "Convert the left card into the right card", 2, _death)
_tarot("The Hermit", "Double your money (max $20)", 0, _hermit)
_tarot("Temperance", "Gain the total sell value of your Jokers (max $50)", 0, _temperance)


def _high_priestess(game: "GameState", cards: list[Card]) -> None:
    game.add_consumables(game.random_consumables(ConsumableKind.PLANET, 2, "pri"))


def _emperor(game: "GameState", cards: list[Card]) -> None:
    game.add_consumables(game.random_consumables(ConsumableKind.TAROT, 2, "emp"))


def _judgement(game: "GameState", cards: list[Card]) -> None:
    game.add_random_joker("Judgement", append="jud")


def _editionless(game) -> list:
    """The jokers with no edition, oldest first.

    Order decides the answer: the caller draws from this by index, and the
    game draws with pseudorandom_element, which sorts by sort_id before
    indexing. Building it in row order returns the wrong joker as soon as
    anything has been dragged -- reproducibly, from the right stream, which is
    what made it invisible.

    Hex shows what that costs: on the Ghost Deck it keeps one joker and
    destroys the rest, so a single draw decides the run.
    """
    return sorted((j for j in game.jokers if j.edition is Edition.NONE),
                  key=lambda j: j.uid)


def _wheel_of_fortune(game: "GameState", cards: list[Card]) -> None:
    """One in four to put an edition on a joker that has none.

    Three draws, all against the same pool name -- "wheel_of_fortune" -- so
    they come out of one stream in order: the chance, then which joker, then
    which edition. The simulator used three names of its own and picked the
    edition uniformly from three, where the game polls the ordinary edition
    bands widened twenty-five times so that something always lands.

    Recording 9's wheel at step 116 used to land one joker out, and was
    papered over with an extra draw whenever the card came out of a pack.
    The stream was never the problem; the pool's order was. Its jokers are
    Space Joker, Ride the Bus, Egg and Reserved Parking, and the game's ids
    for them are 57, 52, 70 and 107: Ride the Bus is the older, though it was
    bought second. The simulator aged a joker when it joined the row, so it
    sorted Space Joker first. Aged where it is built (JokerInstance.uid), the
    plain draw lands on Ride the Bus, and every wheel in recordings 3, 5, 8
    and 9 agrees with no special case for packs.
    """
    plain = _editionless(game)
    if not plain:
        return
    if not game.rng.chance("wheel_of_fortune",
                           1 * game.probability_scale(), 4):
        return
    joker = game.rng.random_element(plain, "wheel_of_fortune")
    from .shop_pool import poll_edition
    name = poll_edition(game.rng, "wheel_of_fortune", no_negative=True,
                        guaranteed=True)
    joker.edition = {"foil": Edition.FOIL, "holo": Edition.HOLOGRAPHIC,
                     "polychrome": Edition.POLYCHROME,
                     "none": Edition.NONE}[name]
    game.log(f"Wheel of Fortune: {joker.name} is now {joker.edition.value}")


_tarot("The High Priestess", "Create 2 random Planet cards", 0, _high_priestess)
_tarot("The Emperor", "Create 2 random Tarot cards", 0, _emperor)
_tarot("Judgement", "Create a random Joker", 0, _judgement)
_tarot("The Wheel of Fortune", "1 in 4 chance to add an edition to a random Joker",
       0, _wheel_of_fortune)


# --------------------------------------------------------------------------
# spectrals
# --------------------------------------------------------------------------

def _spectral(name: str, text: str, targets: int, apply: ApplyHook,
              max_targets: int | None = None) -> None:
    register(ConsumableSpec(name, ConsumableKind.SPECTRAL, text, targets=targets,
                            max_targets=max_targets, apply=apply, cost=4))


def _seal(seal: Seal) -> ApplyHook:
    def apply(game: "GameState", cards: list[Card]) -> None:
        for card in cards:
            card.seal = seal
    return apply


def _aura(game: "GameState", cards: list[Card]) -> None:
    """A random edition on one card in hand.

    poll_edition('aura', nil, true, true) -- the guaranteed form, bands
    widened twenty-five times so something always lands, and no negative.
    Picking uniformly from three, which is what this did, gives polychrome
    far more often than the game does.
    """
    from .shop_pool import poll_edition
    for card in cards:
        name = poll_edition(game.rng, "aura", no_negative=True,
                            guaranteed=True)
        card.edition = {"foil": Edition.FOIL, "holo": Edition.HOLOGRAPHIC,
                        "polychrome": Edition.POLYCHROME,
                        "none": Edition.NONE}[name]


def _black_hole(game: "GameState", cards: list[Card]) -> None:
    for hand in HandType:
        game.hand_levels.level_up(hand)


def _immolate(game: "GameState", cards: list[Card]) -> None:
    """Destroy 5 random cards *in hand*, and gain twenty dollars.

    Not five from the deck, which is what this took: the game copies
    G.hand.cards, shuffles that copy under the pool name "immolate" and takes
    the first five. Cards still in the deck are never at risk, so the card the
    player is looking at is exactly the card that can burn.
    """
    doomed = sorted(game.hand, key=lambda card: card.uid)
    game.rng.shuffle(doomed, "immolate")
    for card in doomed[:5]:
        game.remove_card(card)
    game.add_money(20, "Immolate")


def _ectoplasm(game: "GameState", cards: list[Card]) -> None:
    """Negative onto a joker with no edition, and a growing bite out of the hand.

    The cost is not the flat -1 the card prints. G.GAME.ecto_minus starts at
    one and rises by one after every use, so a run's second Ectoplasm costs
    two hand size and its third costs three. Measured: eight becomes seven on
    the first use with ecto_minus left reading two.

    A run whose jokers all carry an edition cannot use it at all -- see
    GameState.can_use_consumable -- so the empty case here is belt and braces.
    """
    plain = _editionless(game)
    if plain:
        game.rng.choice("ectoplasm", plain).edition = Edition.NEGATIVE
        game.base_hand_size -= game.ecto_minus
        game.ecto_minus += 1


SUITS_BY_LETTER = {"S": Suit.SPADES, "H": Suit.HEARTS,
                   "D": Suit.DIAMONDS, "C": Suit.CLUBS}
RANKS_BY_LETTER = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR,
                   "5": Rank.FIVE, "6": Rank.SIX, "7": Rank.SEVEN,
                   "8": Rank.EIGHT, "9": Rank.NINE, "T": Rank.TEN,
                   "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
                   "A": Rank.ACE}
# Every enhancement but Stone: the cards these three make are always
# enhanced, and Stone is excluded because it has no rank or suit to give.
_SPE_POOL = ["m_bonus", "m_mult", "m_wild", "m_glass", "m_steel", "m_gold",
             "m_lucky"]


def _destroy_and_make(game: "GameState", count: int, ranks: list[str],
                      rank_key: str | None, suit_key: str) -> None:
    """Familiar, Grim and Incantation: one card out, several in.

    All three work the same way and the simulator had all three wrong in the
    same three ways. The card destroyed is a *random* one from the hand, not
    a card the player selected -- the game draws it with
    pseudorandom_element(G.hand.cards, 'random_destroy') and none of them asks
    you to choose. The cards made go into the *hand*, not into the deck to be
    drawn later. And they are always enhanced: create_playing_card picks a
    centre from every enhancement but Stone.

    Rank and suit come from the same pool name -- "familiar_create" for both
    halves of a Familiar card -- so the two draws come out of one stream in
    that order.

    Grim draws no rank at all: card.lua:1322-1324 sets `_rank = 'A'` and only
    the suit goes through pseudorandom_element. A draw from a one-element list
    is not free -- pseudoseed advances `grim_create` either way -- so drawing
    one here put every Ace after the first a step behind the game's suit.
    `rank_key=None` says there is no draw.
    """
    if game.hand:
        doomed = game.rng.random_element(sorted(game.hand,
                                                key=lambda c: c.uid),
                                         "random_destroy")
        game.remove_card(doomed)
    for _ in range(count):
        rank = RANKS_BY_LETTER[ranks[0] if rank_key is None
                               else game.rng.random_element(ranks, rank_key)]
        suit = SUITS_BY_LETTER[game.rng.random_element(
            ["S", "H", "D", "C"], suit_key)]
        card = Card(rank, suit)
        card.enhancement = Enhancement(
            game.rng.random_element(_SPE_POOL, "spe_card")[2:])
        game.add_card_to_hand(card)


def _familiar(game: "GameState", cards: list[Card]) -> None:
    _destroy_and_make(game, 3, ["J", "Q", "K"],
                      "familiar_create", "familiar_create")


_spectral("Talisman", "Add a Gold Seal to 1 card", 1, _seal(Seal.GOLD))
_spectral("Deja Vu", "Add a Red Seal to 1 card", 1, _seal(Seal.RED))
_spectral("Trance", "Add a Blue Seal to 1 card", 1, _seal(Seal.BLUE))
_spectral("Medium", "Add a Purple Seal to 1 card", 1, _seal(Seal.PURPLE))
_spectral("Aura", "Add a random edition to 1 card in hand", 1, _aura)
_spectral("Black Hole", "Level up every poker hand", 0, _black_hole)
_spectral("Immolate", "Destroy 5 random cards in deck, gain $20", 0, _immolate)
_spectral("Ectoplasm", "Add Negative to a random Joker, -1 hand size", 0, _ectoplasm)
_spectral("Familiar",
          "Destroy 1 random card in hand, add 3 random Enhanced face cards",
          0, _familiar)


def by_kind(kind: ConsumableKind) -> list[ConsumableSpec]:
    return [s for s in REGISTRY.values() if s.kind is kind]


# --------------------------------------------------------------------------
# the consumables the shop can offer that were never registered
# --------------------------------------------------------------------------
#
# A whole-run comparison found these by crashing on them: the shop rolled a
# Spectral, the pool named one the simulator had never heard of, and the
# lookup failed. Nine Spectrals and The Fool. Registering them is what lets a
# shop be built at all; the effects that need machinery this engine does not
# have are marked rather than faked, so nothing here quietly does nothing
# while looking implemented.


def _fool(game: "GameState", cards: list[Card]) -> None:
    """Copy the last Tarot or Planet used this run, itself excepted."""
    from .shop_pool import NAME_BY_CONSUMABLE_KEY
    last = getattr(game, "last_tarot_planet", "")
    if not last or last == "c_fool":
        return
    name = NAME_BY_CONSUMABLE_KEY.get(last)
    if name in REGISTRY:
        game.add_consumables([REGISTRY[name]])


def _grim(game: "GameState", cards: list[Card]) -> None:
    _destroy_and_make(game, 2, ["A"], None, "grim_create")


def _incantation(game: "GameState", cards: list[Card]) -> None:
    _destroy_and_make(game, 4,
                      ["2", "3", "4", "5", "6", "7", "8", "9", "T"],
                      "incantation_create", "incantation_create")


def _cryptid(game: "GameState", cards: list[Card]) -> None:
    """Two copies of a chosen card, into the hand.

    G.hand:emplace, not into the deck to be drawn later -- the copies are
    there to be played with the hand they came from. They join the deck too,
    so they come round again in later rounds.
    """
    for card in cards:
        for _ in range(2):
            copy = card.copy()
            game.add_card_to_hand(copy)


def _ankh(game: "GameState", cards: list[Card]) -> None:
    """Copy a random joker, destroy the others.

    The copy is chosen from every joker held, but only the ones that can be
    destroyed are destroyed -- an eternal joker survives even when it was not
    the one chosen, which is the edge case the game's own comment calls out.
    """
    if not game.jokers:
        return
    chosen = game.rng.choice("ankh_choice", game.jokers)
    for joker in list(game.jokers):
        if joker is not chosen:
            game.destroy_joker(joker, "Ankh")
    game.add_joker_copy(chosen, "Ankh")


def _hex(game: "GameState", cards: list[Card]) -> None:
    """Make one joker Polychrome and destroy the rest.

    The one chosen is drawn from the jokers with no edition yet, not from all
    of them, so a run whose jokers are already editioned loses nothing and
    gains nothing.
    """
    plain = _editionless(game)
    if not plain:
        return
    chosen = game.rng.choice("hex", plain)
    chosen.edition = Edition.POLYCHROME
    for joker in list(game.jokers):
        if joker is not chosen:
            game.destroy_joker(joker, "Hex")
    game.log("Hex: %s is now polychrome" % chosen.name)


def _ouija(game: "GameState", cards: list[Card]) -> None:
    """Every card in hand becomes one random rank. Costs a hand size."""
    rank = game.rng.choice("ouija", list(Rank))
    for card in game.hand:
        card.rank = rank
    game.base_hand_size -= 1
    game.log("Ouija: the hand is all %ss" % rank.name.title())


def _sigil(game: "GameState", cards: list[Card]) -> None:
    """Every card in hand becomes one random suit."""
    suit = game.rng.choice("sigil", list(Suit))
    for card in game.hand:
        card.set_suit(suit)
    game.log("Sigil: the hand is all %s" % suit.name.title())


def _the_soul(game: "GameState", cards: list[Card]) -> None:
    game.add_random_joker("The Soul", legendary=True, append="sou")


def _wraith(game: "GameState", cards: list[Card]) -> None:
    """A random Rare joker, and every dollar you had.

    The rarity is not rolled: the game passes 0.99, which lands in the rare
    band, so this is always rare and never legendary.
    """
    from .jokers import Rarity          # jokers imports this module
    game.add_random_joker("Wraith", rarity=Rarity.RARE, append="wra")
    game.add_money(-game.money, "Wraith")


_tarot("The Fool", "Copy the last Tarot or Planet card used this run", 0, _fool)

_spectral("Ankh", "Copy a random Joker, destroy the others", 0, _ankh)
_spectral("Cryptid", "Create 2 copies of a selected card", 1, _cryptid)
_spectral("Grim", "Destroy 1 random card in hand, add 2 random Enhanced Aces",
          0, _grim)
_spectral("Hex", "Add Polychrome to a random Joker, destroy the others", 0, _hex)
_spectral("Incantation",
          "Destroy 1 random card in hand, add 4 random Enhanced numbered cards",
          0, _incantation)
_spectral("Ouija", "Convert all cards in hand to a single random rank, -1 hand size",
          0, _ouija)
_spectral("Sigil", "Convert all cards in hand to a single random suit", 0, _sigil)
_spectral("The Soul", "Create a Legendary Joker", 0, _the_soul)
_spectral("Wraith", "Create a random Rare Joker, set money to $0", 0, _wraith)
