"""Present a simulator run in the shape `BOT.state()` returns.

One state shape for every backend: the simulator, the headless engine and the
running game all report in the engine's shape, so whatever reads a state --
an encoder, a comparison, a report -- cannot tell which produced it. This
lived in the training repository as balatro_env/sim_state.py, which still
re-exports it; it moved here beside `run.py` so the backends and their shared
shape are one library.

The observation encoder is the contract between the agent and the game, and
it is written against the engine's state dictionary. Rather than write a
second encoder for the simulator -- which is how the two would drift -- this
builds the same dictionary out of a GameState, so `encode` and
`legal_actions` cannot tell which engine produced it.

Only the keys the encoder and the action mask actually read are built. The
engine's own state carries a good deal more (last_refusal, shop_ready,
stop_use, the selected indices) that exists for the socket client driving the
real game, and inventing simulator answers for those would be fiction nobody
reads.

The id tables below are the engine's, not the simulator's, and they are not
the same. `SUIT_IDS` in bot_api.lua is Spades, Hearts, Clubs, Diamonds --
Clubs before Diamonds -- while the simulator's Suit enum runs Spades, Hearts,
Diamonds, Clubs. Mapping by enum position would silently swap the two suits
in every observation, which is the sort of mistake that trains a policy on a
game nobody is playing.
"""

from __future__ import annotations

import json
import math
import pathlib
from typing import Any

from jimbot_sim.cards import Card, Edition, Enhancement, Rank, Seal, Suit

VOCABULARY_PATH = pathlib.Path(__file__).with_name("vocabulary.json")

# bot_api.lua's own orderings, copied rather than derived.
SUIT_IDS = {Suit.SPADES: 1, Suit.HEARTS: 2, Suit.CLUBS: 3, Suit.DIAMONDS: 4}
RANK_IDS = {rank: i for i, rank in enumerate(Rank, start=1)}
EDITION_IDS = {Edition.NONE: 0, Edition.FOIL: 1, Edition.HOLOGRAPHIC: 2,
               Edition.POLYCHROME: 3, Edition.NEGATIVE: 4}
SEAL_IDS = {Seal.NONE: 0, Seal.GOLD: 1, Seal.RED: 2, Seal.BLUE: 3,
            Seal.PURPLE: 4}
ENHANCEMENT_KEYS = {
    Enhancement.NONE: "c_base", Enhancement.BONUS: "m_bonus",
    Enhancement.MULT: "m_mult", Enhancement.WILD: "m_wild",
    Enhancement.GLASS: "m_glass", Enhancement.STEEL: "m_steel",
    Enhancement.STONE: "m_stone", Enhancement.GOLD: "m_gold",
    Enhancement.LUCKY: "m_lucky",
}
# The order bot_api.lua declares, which the encoder indexes directly.
ENHANCEMENT_IDS = {
    "c_base": 0, "m_bonus": 1, "m_mult": 2, "m_wild": 3, "m_glass": 4,
    "m_steel": 5, "m_stone": 6, "m_gold": 7, "m_lucky": 8,
}
RARITY_IDS = {"COMMON": 1, "UNCOMMON": 2, "RARE": 3, "LEGENDARY": 4}
# SHOP_SETS in encoding.py, one-based when it reaches the encoder.
SHOP_SET_IDS = {"Joker": 1, "Tarot": 2, "Planet": 3, "Spectral": 4,
                "Voucher": 5, "Booster": 6}

# The simulator's phases, named as the engine names its states. A phase with
# no engine equivalent would break the state one-hot, so every one is mapped.
STATE_BY_PHASE = {
    "blind_select": "BLIND_SELECT",
    "playing": "SELECTING_HAND",
    "round_eval": "ROUND_EVAL",
    "shop": "SHOP",
    "game_over": "GAME_OVER",
    "won": "GAME_OVER",
}
PACK_STATE_BY_KIND = {
    "arcana": "TAROT_PACK", "celestial": "PLANET_PACK",
    "spectral": "SPECTRAL_PACK", "standard": "STANDARD_PACK",
    "buffoon": "BUFFOON_PACK",
}


class Centres:
    """Key -> the 1-based id the encoder's vocabulary is built on.

    The engine numbers centres by sorting every key in G.P_CENTERS, so the id
    of a joker depends on the whole table and cannot be worked out from the
    simulator's own registries. The list is frozen in vocabulary.json,
    captured from a booted engine, and both environments read it -- which is
    what makes an observation from one loadable by a policy trained on the
    other.
    """

    def __init__(self, path: pathlib.Path = VOCABULARY_PATH) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.key_list: str = data["key_list"]
        self.tag_list: str = data["tag_list"]
        self.blind_list: str = data["blind_list"]
        self.centre_id: dict[str, int] = {}
        self.centre_set: dict[str, str] = {}
        for i, entry in enumerate(self.key_list.split(","), start=1):
            key, _, kind = entry.partition("|")
            self.centre_id[key] = i
            self.centre_set[key] = kind
        self.tag_id = {t: i for i, t in
                       enumerate(self.tag_list.split(","), start=1) if t}
        self.blind_id = {b: i for i, b in
                         enumerate(self.blind_list.split(","), start=1) if b}

    def of(self, key: str) -> int:
        return self.centre_id.get(key, 0)


_CENTRES: Centres | None = None


def centres() -> Centres:
    global _CENTRES
    if _CENTRES is None:
        _CENTRES = Centres()
    return _CENTRES


# ----------------------------------------------------------------------
# rows
# ----------------------------------------------------------------------

def _card_row(card: Card, highlighted: bool) -> dict:
    key = ENHANCEMENT_KEYS[card.enhancement]
    return {
        "rank": RANK_IDS[card.rank],
        "suit": SUIT_IDS[card.suit],
        # The encoder maps this through vocab.enhancement_index, which is
        # keyed on the *centre* id, not on ENHANCEMENT_IDS.
        "center": centres().of(key),
        # card.base.nominal -- the rank's own chip value, ten for a face card
        # and eleven for an ace. Reporting the Hiker bonus instead left every
        # card in every observation worth zero chips, which is the single
        # largest thing the policy could not see.
        "chips": card.rank.chips,
        # What Hiker and friends have added to this card for good. Kept beside
        # the nominal rather than folded into it: the fix that made `chips`
        # the nominal did so because reporting the bonus *instead* left every
        # card reading as zero chips, and swapping which of the two is visible
        # is not the same as showing both.
        "extra_chips": int(card.extra_chips),
        "highlighted": 1 if highlighted else 0,
        "debuffed": 1 if card.debuffed else 0,
        "edition": EDITION_IDS[card.edition],
        "seal": SEAL_IDS[card.seal],
    }


def _joker_row(game, joker) -> dict:
    """One joker in the row, with the state that decides what it is worth.

    Which joker sits in a slot used to be the whole of it, and for the first
    three antes that is nearly enough: everything is still near its starting
    value, so identity all but fixes worth. It comes apart later. Thirty-two
    of the jokers keep a live counter -- Ride the Bus counts hands, Obelisk
    multiplies, Popcorn counts down to nothing -- and by ante six that number
    *is* the joker. A Ride the Bus at +2 Mult and one at +60 encoded
    identically, so the policy could not tell a built engine from a fresh one,
    could not price selling one, and could not know whether its board could
    take the next blind. The plateau sat exactly where identity stops
    predicting worth.

    `debuffed` matters for the same reason and was invisible too: a perished
    joker, or one a boss has switched off, scored nothing while still reading
    as present and healthy.
    """
    from jimbot_sim import shop_pool
    key = shop_pool.KEY_BY_JOKER_NAME.get(joker.name, "")
    return {
        "center": centres().of(key),
        "sellable": 0 if joker.eternal else 1,
        "sell_cost": game.sell_value(joker),
        "rarity": RARITY_IDS.get(joker.spec.rarity.name, 1),
        "edition": EDITION_IDS[joker.edition],
        # What the joker has grown to. The meaning is the joker's own --
        # Mult for Popcorn, a multiplier for Obelisk, Chips for Runner -- and
        # the one-hot beside it says which reading applies.
        "counter": float(joker.counter),
        # The second number the few that keep two use: Yorick's countdown,
        # Invisible Joker's rounds held.
        "secondary": float(joker.secondary),
        "debuffed": 1 if joker.debuffed else 0,
        # The stake's stickers. Eternal was readable as sellable=0, which
        # confounded it with every other reason a joker cannot be sold;
        # perishable and rental were not readable at all. A rental is the
        # worst of the three to hide, because the shop discounts it to $1 --
        # so the agent saw a cheap joker, bought it, and paid $3 a round it
        # could neither see nor attribute.
        "eternal": 1 if joker.eternal else 0,
        "perishable": 1 if joker.perishable else 0,
        "perish_tally": int(joker.perish_tally),
        "rental": 1 if joker.rental else 0,
        # Hands played since it joined the row, which is what the jokers that
        # count hands actually measure from.
        "hands_held": int(game.hands_played - joker.hands_at_create),
    }


def _consumable_row(game, held, usable: bool) -> dict:
    from jimbot_sim import shop_pool
    key = shop_pool.KEY_BY_CONSUMABLE_NAME.get(held.name, "")
    return {
        "center": centres().of(key),
        "sellable": 1,
        "usable": 1 if usable else 0,
        "edition": EDITION_IDS[held.edition],
    }


def _shop_key_and_set(slot) -> tuple[str, str]:
    from jimbot_sim import shop_pool
    if slot.joker is not None:
        return shop_pool.KEY_BY_JOKER_NAME.get(slot.joker.name, ""), "Joker"
    if slot.consumable is not None:
        key = shop_pool.KEY_BY_CONSUMABLE_NAME.get(slot.consumable.name, "")
        return key, slot.consumable.kind.value.title()
    return "", "Joker"


def _shop_row(game, slot) -> dict:
    key, kind = _shop_key_and_set(slot)
    price = game.slot_price(slot)
    card = slot.card
    return {
        "area": "shop_jokers",
        "index": 0,                       # filled in by shop_rows
        "center": centres().of(key),
        "set": SHOP_SET_IDS.get(kind, 1),
        "cost": price,
        "buyable": 1 if (game.affords(price) and _has_room(game, slot)) else 0,
        # The shop's second button, and it asks more than affordability: the
        # game's own test is `consumeable and affordable and can_use(card)`.
        # Dropping can_use offered buy-and-use for a Judgement with no joker
        # room, which the engine refuses.
        "buy_and_usable": 1 if (slot.consumable is not None
                                and game.affords(price)
                                and game.can_use_consumable(slot.consumable,
                                                            ())) else 0,
        "edition": EDITION_IDS[slot.joker.edition] if slot.joker is not None
        else (EDITION_IDS[card.edition] if card is not None else 0),
        "seal": SEAL_IDS[card.seal] if card is not None else 0,
        # The stickers are rolled when the shop stocks the joker, not when it
        # is bought, so they are knowable at the only moment they can still be
        # acted on. This is the row where hiding them cost the most: a rental
        # is priced at $1 however expensive the joker, so the shop showed a
        # bargain and said nothing about the $3 a round behind it.
        **_stickers(slot.joker),
    }


def _stickers(joker) -> dict:
    """Eternal, perishable and rental for a joker that may not be one.

    Shop and pack rows hold vouchers, boosters and playing cards too, and a
    row that simply omitted the keys would encode as a joker with no stickers
    rather than as something that cannot carry them. Zero for all three is the
    same vector either way; spelling it out keeps the row shape constant, which
    is what the encoder assumes.
    """
    if joker is None:
        return {"eternal": 0, "perishable": 0, "rental": 0}
    return {"eternal": 1 if joker.eternal else 0,
            "perishable": 1 if joker.perishable else 0,
            "rental": 1 if joker.rental else 0}


def _has_room(game, slot) -> bool:
    """check_for_buy_space: a joker needs a joker slot, a consumable a
    consumable one -- and a Negative needs neither.

    button_callbacks.lua:2396 adds one to the limit when the card is
    Negative. This counted the row and stopped there, so a Negative joker
    in a full row was buyable to the simulator (whose room_for_joker has
    the rule and a test against the game) and masked out here; the
    hand-written policy's flat run was the first thing to notice, since it
    is the first player to have read both.
    """
    if slot.joker is not None:
        return game.room_for_joker(slot.joker)
    if slot.consumable is not None:
        return len(game.consumables) < game.consumable_slots
    return True                            # a playing card goes to the deck


def shop_rows(game) -> list[dict]:
    """The whole shop, in the engine's own area order.

    Every card in all three rows is one entry of the same list, so `buy 2`
    means the third thing on offer whichever row it sits in. Listing only the
    card slots left the agent unable to buy a voucher or a booster pack at
    all, and their action stayed masked out for the whole of a run.

    The order is BOT.state's, which is *not* headless_api's SHOP_AREAS:
    jokers, vouchers, boosters against jokers, boosters, vouchers. The
    observation is built from the first, so this follows the first -- getting
    it wrong points every buy at the wrong shelf.
    """
    if game.shop is None:
        return []
    rows = [_shop_row(game, slot) for slot in game.shop.slots]

    for voucher in game.shop.vouchers_on_offer():
        price = game.price(voucher.cost)
        rows.append({
            "area": "shop_vouchers", "index": 0,
            "center": centres().of(voucher.key),
            "set": SHOP_SET_IDS["Voucher"], "cost": price,
            "buyable": 1 if game.affords(price) else 0,
            "buy_and_usable": 0, "edition": 0, "seal": 0,
            **_stickers(None),
        })

    for pack in game.shop.packs:
        # The game's price, not the list price: Astronomer zeroes a Celestial
        # pack (card.lua:380), and this row's `cost` and `buyable` have to say
        # what the legal-action list says. See GameState.pack_price.
        price = game.pack_price(pack)
        rows.append({
            "area": "shop_booster", "index": 0,
            "center": centres().of(pack.key),
            "set": SHOP_SET_IDS["Booster"], "cost": price,
            # Opened rather than stored, so no slot has to be free for it --
            # what comes out is what needs room, and that is checked when a
            # card is picked. See the note in bot_api.lua.
            "buyable": 1 if game.affords(price) else 0,
            "buy_and_usable": 0, "edition": 0, "seal": 0,
            **_stickers(None),
        })

    # 1-based within each area, as the engine numbers them.
    counters: dict[str, int] = {}
    for row in rows:
        counters[row["area"]] = counters.get(row["area"], 0) + 1
        row["index"] = counters[row["area"]]
    return rows


def _pack_row(game, option, picked: tuple = ()) -> dict:
    """One card on offer inside an opened pack.

    `usable` is what gates PICK_PACK, and it is the game's own
    can_select_card: a consumable taken from a pack is used the instant it is
    taken -- the pack screen calls use_card, not buy -- so it faces the same
    test as using one from the slots; a playing card always goes to the deck;
    a joker needs a free slot unless it is Negative. "A joker is always
    takeable" is what this said, and the engine agreed with it, which is how
    a policy came to take a sixth joker into five slots.

    With the cards actually selected, and this asked with nothing for months.
    An Arcana pack deals a hand precisely so the Tarot inside it can be aimed
    at one, and every targeting Tarot is gated on how many are highlighted --
    The Hierophant two, The Devil one, The Hanged Man two -- so asking with an
    empty selection answered no for all of them, always. The mask therefore
    offered *nothing* in an Arcana pack but skip, whatever the agent selected
    first, and no policy trained here has ever taken a Tarot from one. The
    same fix was made for held consumables and this row was missed.

    The engine had it right, which is what makes it a divergence as well as a
    bug: bot_api's can_use reads G.hand.highlighted, so the two backends
    disagreed about the same pack.
    """
    from jimbot_sim import shop_pool
    from jimbot_sim.jokers import JokerInstance

    if isinstance(option, JokerInstance):
        key = shop_pool.KEY_BY_JOKER_NAME.get(option.name, "")
        return {"center": centres().of(key), "set": SHOP_SET_IDS["Joker"],
                "edition": EDITION_IDS[option.edition], "seal": 0,
                "usable": 1 if game.room_for_joker(option) else 0,
                **_stickers(option)}
    if isinstance(option, Card):
        return {"center": centres().of(ENHANCEMENT_KEYS[option.enhancement]),
                "set": SHOP_SET_IDS["Joker"],
                "edition": EDITION_IDS[option.edition],
                "seal": SEAL_IDS[option.seal], "usable": 1,
                **_stickers(None)}
    key = shop_pool.KEY_BY_CONSUMABLE_NAME.get(option.name, "")
    return {"center": centres().of(key),
            "set": SHOP_SET_IDS.get(option.kind.value.title(), 2),
            "edition": 0, "seal": 0,
            "usable": 1 if game.can_use_consumable(option, picked) else 0,
            **_stickers(None)}


# ----------------------------------------------------------------------
# the state
# ----------------------------------------------------------------------

def state_dict(game, selection: tuple[int, ...] = (),
               toggles_used: int = 0, joker_swaps_used: int = 0) -> dict:
    """A simulator run, in the shape the observation encoder reads.

    `selection` is the picking the environment is holding on the agent's
    behalf. The simulator has no notion of a highlighted card -- it takes the
    indices with the action -- so the environment owns that and hands it in.
    """
    from jimbot_sim.blinds import BlindKind
    from jimbot_sim.game import Phase
    from jimbot_sim.hands import HandType, evaluate

    blind = game.blind
    hand = list(game.hand)
    chosen = tuple(i for i in selection if i < len(hand))

    if game.phase is Phase.PACK and game.pack is not None:
        state_name = PACK_STATE_BY_KIND.get(game.pack.kind.value, "SHOP")
    else:
        state_name = STATE_BY_PHASE.get(game.phase.value, "SELECTING_HAND")

    levels = {}
    for h in HandType:
        chips, mult = game.hand_levels.values(h)
        levels[h.label] = {"level": game.hand_levels.levels[h],
                           "played": game.hand_levels.plays[h],
                           "chips": chips, "mult": mult}

    made = {"name": "", "level": 0, "chips": 0, "mult": 0, "cards": 0,
            "estimate": 0}
    picked = [hand[i] for i in chosen]
    if chosen:
        result = game.evaluate_selection(picked)
        chips, mult = game.hand_levels.values(result.hand)
        # The line the game shows while a player is choosing: the hand's own
        # chips *plus the nominals of the cards that will actually score*,
        # and a count of those rather than of the ones highlighted. Two pair
        # played with a fifth card scores four of the five, and the display
        # says four. Reporting the selection instead told the agent a
        # non-scoring card was pulling its weight.
        card_chips = sum(c.rank.chips for c in result.scoring)
        made = {"name": result.hand.label,
                "level": game.hand_levels.levels[result.hand],
                "chips": chips + card_chips, "mult": mult,
                "cards": len(result.scoring), "estimate": 0}

    deck = _deck_counts(game.full_deck)

    boss_blind = game.ante_boss or ""
    rows = []
    for index, kind in enumerate((BlindKind.SMALL, BlindKind.BIG,
                                  BlindKind.BOSS)):
        rows.append(_blind_row(game, kind, index, boss_blind))

    # The tag for the blind *on deck*, which is knowable in the shop and on
    # the select screen -- the whole point of it, since skipping trades the
    # blind's money and chips for exactly this. Gating on a blind being in
    # force reported "no tag" for every step of every shop.
    tag_key = ""
    if game.blind_index < len(game.ante_tag_keys):
        tag_key = game.ante_tag_keys[game.blind_index]

    return {
        "state_name": state_name,
        "in_run": 1,
        # What the run was started with. Constant for its whole length, and
        # reported by name because that is what bot_api.lua reads out of
        # G.GAME.selected_back.
        "deck": game.deck,
        "stake": int(game.stake),
        "ante": game.ante,
        "round": game.round_number,
        "dollars": game.money,
        "chips": game.chips_scored,
        # Zero outside a round, as the engine reports it: the blind is held
        # through the select screen and the shop so it can be offered, but it
        # is not in force, and the game shows no target until it is.
        "blind_chips": game.blind_target,
        "hands_left": game.hands_left,
        "discards_left": game.discards_left,
        "joker_limit": game.joker_slots,
        "consumable_limit": game.consumable_slots,
        # current_round.reroll_cost, which the game keeps between shops --
        # it is reset to round_resets.reroll_cost at the start of a round and
        # climbs as the shop is rerolled. Reporting zero outside a shop said
        # a reroll was free on almost every step of a run.
        "reroll_cost": _reroll_cost(game),
        "won": 1 if game.phase is Phase.WON else 0,
        "selection_size": len(chosen),
        "highlight_limit": 5,
        # The simulator has no sort buttons: it hands the environment the hand
        # in one order and the agent picks by index, so there is nothing to
        # press and nothing to waste a step on. Reported as already sorted so
        # the mask never offers the action.
        "sorted_rank": 1,
        "sorted_suit": 1,
        "toggles_used": toggles_used,
        "joker_swaps_used": joker_swaps_used,
        # In force, not merely on deck -- same rule as blind_chips.
        "boss": 1 if (blind is not None and blind.kind is BlindKind.BOSS
                      and game.blind_target) else 0,
        # blind_on_deck() ~= 'Boss': about which blind is next, not about the
        # one being played, so it stays true through a small or big round.
        "skippable": 1 if game.blind_index < 2 else 0,
        "offered_tag": centres().tag_id.get(tag_key, 0),
        "best_hand": int(game.best_hand),
        "deck_size": len(game.full_deck),
        "deck_cards": deck,
        # The four values the run holds on behalf of a joker. Names rather
        # than ids, because that is what the engine reports and what its
        # ranks and suits are keyed on -- see the encoder, which maps both
        # sides through the same tables.
        "idol_rank": _rank_id(game.idol_rank),
        "idol_suit": _suit_id(game.idol_suit),
        "ancient_suit": _suit_id(game.ancient_suit),
        "mail_rank": _rank_id(game.mail_rank),
        "castle_suit": _suit_id(game.castle_suit),
        # Redeemed, not on offer. The shop block already shows what is for
        # sale; this is what the run has permanently become.
        "vouchers": sorted(v.key for v in game.vouchers),
        "blinds": rows,
        "hand_levels": levels,
        "selected_hand": made,
        "hand": [_card_row(c, i in chosen) for i, c in enumerate(hand)],
        "jokers": [_joker_row(game, j) for j in game.jokers],
        # With the cards actually selected, not with nothing. Every targeting
        # consumable is gated on how many are highlighted -- The Star takes
        # one to three, Death exactly two, Strength one or two -- and asking
        # with an empty selection answers no for all of them, always. So the
        # mask never offered a targeting Tarot at all, whatever the agent had
        # picked, and a policy could hold a Death for a whole run without ever
        # being allowed to use it.
        #
        # The recordings never caught it: they drive the recorded action
        # directly and never consult the mask. It took forking a recorded
        # position into both engines and playing legal actions from there --
        # the engine said usable, this said not.
        "consumables": [
            _consumable_row(game, c,
                            game.can_use_consumable(c, tuple(picked)))
            for c in game.consumables],
        "shop": shop_rows(game),
        "pack": [_pack_row(game, o, tuple(picked))
                 for o in game.pack_options],
    }


def _reroll_cost(game) -> int:
    """What a reroll would cost, in or out of a shop."""
    if game.shop is not None:
        return game.shop.reroll_cost(
            sum(v.reroll_discount for v in game.vouchers))
    return game.reroll_cost_carried


def _blind_row(game, kind, index: int, boss_key: str) -> dict:
    """One row of the run info screen: what this blind asks and pays.

    Built rather than read off the run, because the whole point of the row is
    that it is knowable *before* the blind is in force -- which boss is coming
    decides what to build for while there is still a shop to spend in.
    """
    from jimbot_sim.blinds import (BLIND_MULT, BLIND_REWARD, BlindKind,
                                ante_base_chips)

    key = boss_key if kind is BlindKind.BOSS else (
        "bl_small" if kind is BlindKind.SMALL else "bl_big")
    mult = BLIND_MULT[kind]
    if kind is BlindKind.BOSS and boss_key:
        effect = game._pick_boss()
        if effect is not None:
            mult = effect.chip_mult
    target = int(ante_base_chips(game.ante, game.blind_scaling) * mult
                 * game.deck_config.get("ante_scaling", 1))
    reward = BLIND_REWARD[kind]
    return {
        "kind": kind.value.title(),
        "blind": centres().blind_id.get(key, 0),
        "chips": target,
        "reward": reward,
        # Defeated and skipped are different states on the run info screen,
        # and a blind that was skipped was never beaten. Reporting every
        # passed blind as defeated told the agent it had won rounds it had
        # walked away from.
        "defeated": 1 if (index < game.blind_index
                          and index not in game.skipped_this_ante) else 0,
        "skipped": 1 if index in game.skipped_this_ante else 0,
        # "Current" is the game's own blind_states value, and it is set while
        # the blind is being played -- not while it merely sits next in line.
        "current": 1 if (index == game.blind_index
                         and game.blind_target) else 0,
    }


# The same ids as the dicts above, hung on the enum members themselves.
#
# Enum.__hash__ is a Python-level method, so every `RANK_IDS[card.rank]` costs
# a call; _deck_counts alone does five per card over the whole deck, and the
# profile showed 1,956,574 of them across six thousand steps -- 326 a step,
# about a tenth of one. An attribute read is 21ns against 71ns for the dict.
#
# The dicts stay the source of truth and are what everything else reads; this
# is a cache of them, built once, for the one loop hot enough to care. The
# names are prefixed because these members are shared with the whole program.
for _member, _id in SUIT_IDS.items():
    _member.engine_suit_index = _id - 1
for _member, _id in RANK_IDS.items():
    _member.engine_rank_index = _id - 1
for _member in Enhancement:
    _member.engine_enhancement_index = ENHANCEMENT_IDS[ENHANCEMENT_KEYS[_member]]
for _member, _id in SEAL_IDS.items():
    _member.engine_seal_index = _id
for _member, _id in EDITION_IDS.items():
    _member.engine_edition_index = _id


def _rank_id(rank) -> int:
    """RANK_IDS, with zero for "no such value yet".

    Ids rather than the engine's words for them, because the card rows already
    carry ranks and suits this way and the encoder already has the tables --
    bot_api.lua turns G.GAME.current_round's names into the same ids at its
    end, so the two meet on the numbers rather than on the spelling.
    """
    return 0 if rank is None else RANK_IDS[rank]


def _suit_id(suit) -> int:
    return 0 if suit is None else SUIT_IDS[suit]


def _deck_counts(cards) -> dict:
    ranks = [0] * 13
    suits = [0] * 4
    enhancements = [0] * 9
    seals = [0] * 5
    editions = [0] * 5
    for card in cards:
        ranks[card.rank.engine_rank_index] += 1
        suits[card.suit.engine_suit_index] += 1
        enhancements[card.enhancement.engine_enhancement_index] += 1
        seals[card.seal.engine_seal_index] += 1
        editions[card.edition.engine_edition_index] += 1
    extra = [c.extra_chips for c in cards]
    return {"extra_chips_mean": (sum(extra) / len(extra)) if extra else 0.0,
            "extra_chips_share": (sum(1 for e in extra if e)
                                  / len(extra)) if extra else 0.0,
            "ranks": ranks, "suits": suits, "enhancements": enhancements,
            "seals": seals, "editions": editions}
