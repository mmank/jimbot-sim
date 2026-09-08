"""A scripted policy, good enough to be a baseline and to exercise every state.

It is deliberately simple and readable: pick the best-scoring hand, discard what
that hand does not use, buy jokers, level the hand you actually play. It is not
trying to be a strong Balatro player -- it is the bar a learned policy has to
clear, and the thing that proves the driver can reach ante 8.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

# Interest pays $1 per $5 held, capped at $5, so a float earns. But starting
# money is $4: a reserve above zero in ante 1 means buying nothing at all, and
# measured over 20 seeds that is much worse than spending early.
INTEREST_FLOOR = 0

# Tags worth more than a blind's cash-out. Free packs and free jokers are
# straightforwardly good; Orbital levels a hand three times, which is the
# single biggest scaling event available this early.
WORTH_SKIPPING = {
    "tag_orbital",      # +3 levels to a poker hand
    "tag_investment",   # $25 after the boss
    "tag_rare",         # free rare joker
    "tag_uncommon",     # free uncommon joker
    "tag_meteor",       # free Celestial pack -- planets
    "tag_buffoon",      # free Buffoon pack -- jokers
    "tag_charm",        # free Arcana pack
    "tag_ethereal",     # free Spectral pack
    "tag_top_up",       # two free common jokers
    "tag_voucher",      # extra voucher in the shop
    "tag_double",       # copies the next tag
    "tag_economy",      # doubles money
    "tag_skip",         # Speed Tag: $5 per blind skipped this run
}


class GreedyPolicy:
    name = "greedy"

    def __init__(self, reserve: int = INTEREST_FLOOR, buy_packs: bool = True,
                 reroll_below_ante: int = 0, skip_for_tags: bool = False,
                 skip_until_ante: int = 3) -> None:
        self.reserve = reserve
        self.buy_packs = buy_packs
        self.reroll_below_ante = reroll_below_ante
        self.skip_for_tags = skip_for_tags
        self.skip_until_ante = skip_until_ante

    # ------------------------------------------------------------------

    def blind(self, run, state: dict) -> tuple[str, Any]:
        # Skipping trades the blind's cash-out and a round of hand-levelling for
        # the tag on offer. Both of the ante's tags are visible here, exactly as
        # they are on the real blind select screen, so this is a real choice.
        if (self.skip_for_tags and state["skippable"]
                and state["offered_tag"] in WORTH_SKIPPING
                and state["ante"] <= self.skip_until_ante):
            return ("skip", None)
        return ("select", None)

    def hand(self, run, state: dict) -> tuple[str, Any]:
        best, estimate = run.best_play()
        if not best:
            return ("play", [1])

        needed = state["blind_chips"] - state["chips"]
        hands = max(1, state["hands_left"])
        last_hand = state["hands_left"] <= 1
        no_discards = state["discards_left"] <= 0

        # A planet card for the hand we are about to play is free score.
        planet = self._planet_to_use(run)
        if planet is not None:
            return ("use", planet)

        # The blind is cleared across every remaining hand, not by one of them.
        # Comparing against the whole target made this discard until it ran out
        # of discards, every single blind, and then play into a dead position.
        share = needed / hands
        if estimate >= share or last_hand or no_discards:
            return ("play", best)

        junk = run.best_discard()
        if junk:
            return ("discard", junk)
        return ("play", best)

    def shop(self, run, state: dict) -> tuple[str, Any]:
        planet = self._planet_to_use(run)
        if planet is not None:
            return ("use", planet)

        items = run.shop_contents()
        dollars = state["dollars"]
        room_for_joker = state["joker_count"] < state["joker_limit"]
        room_for_consumable = state["consumable_count"] < state["consumable_limit"]

        # `buyable` is the game's own check_for_buy_space: affordability alone
        # is not enough, and buying into a full slot silently no-ops.
        affordable = [i for i in items
                      if i["buyable"] and i["cost"] <= dollars - self.reserve]

        jokers = [i for i in affordable if i["set"] == "Joker" and room_for_joker]
        if jokers:
            # Rarity is a better quality signal than price: a $6 common is
            # usually worse than a $6 uncommon.
            jokers.sort(key=lambda i: (i.get("rarity") or 0, i["cost"]))
            return ("buy", (jokers[-1]["area"], jokers[-1]["index"]))

        # Planets are the compounding buy: levelling the hand you actually play
        # scales every future hand, and they cost $3. Tarots need a target card
        # and this policy has no good use for them, so it does not buy them.
        planets = [i for i in affordable
                   if i["set"] == "Planet" and room_for_consumable]
        if planets:
            return ("buy", (planets[0]["area"], planets[0]["index"]))

        # Full consumable slots block booster packs. Holding a tarot we cannot
        # target is worth less than the pack it is blocking, so cash it in.
        if not room_for_consumable:
            held = run.consumables()
            if held:
                return ("sell", ("consumeables", held[0]["index"]))

        if self.buy_packs:
            packs = [i for i in affordable if i["set"] == "Booster"]
            # Celestial packs hold planets, which is the compounding buy.
            packs.sort(key=lambda i: 0 if "celestial" in i["key"] else 1)
            if packs and dollars >= packs[0]["cost"] + self.reserve:
                return ("buy", (packs[0]["area"], packs[0]["index"]))

        if state["ante"] < self.reroll_below_ante and \
                state["reroll_cost"] <= dollars - self.reserve:
            return ("reroll", None)

        return ("leave", None)

    def pack(self, run, state: dict) -> tuple[str, Any]:
        contents = run.pack_contents()
        if not contents:
            return ("skip_pack", None)

        # A Buffoon pack card is a joker: only take one if there is room.
        for item in contents:
            if item["set"] == "Joker":
                if state["joker_count"] < state["joker_limit"]:
                    return ("pick", item["index"])
                return ("skip_pack", None)

        # Planets and playing cards are always safe to take.
        for item in contents:
            if item["set"] in ("Planet", "Default", "Enhanced"):
                return ("pick", item["index"])

        # Tarots need a target card; taking them blind can fizzle, so only take
        # one when there is a hand to apply it to.
        if state["hand_size"] > 0:
            return ("pick", contents[0]["index"])
        return ("skip_pack", None)

    # ------------------------------------------------------------------

    def _planet_to_use(self, run) -> int | None:
        for item in run.consumables():
            if item["set"] == "Planet":
                return item["index"]
        return None


# ---------------------------------------------------------------------------
# Tuned policy
# ---------------------------------------------------------------------------

# Balatro pays $1 interest per $5 held, capped at $5 -- money above $25 earns
# nothing. Diagnostics showed runs dying with $70-85 in the bank, which is the
# most wasteful thing a policy can do.
INTEREST_CAP = 25

# Reaching ante 8 needs ~100,000 chips. Additive mult cannot get there; only
# XMult, retriggers and hand levels scale that far. Keys are validated against
# the game at construction, so a rename surfaces loudly rather than silently
# demoting a joker to "mediocre".
XMULT_JOKERS = {
    "j_duo", "j_trio", "j_family", "j_order", "j_tribe",
    "j_cavendish", "j_card_sharp", "j_baseball", "j_hologram", "j_vampire",
    "j_obelisk", "j_madness", "j_flower_pot", "j_seance", "j_photograph",
    "j_baron", "j_throwback", "j_glass", "j_campfire", "j_constellation",
    "j_lucky_cat", "j_bloodstone", "j_arrowhead", "j_onyx_agate",
    "j_rough_gem", "j_idol", "j_hit_the_road", "j_erosion", "j_ramen",
    "j_stuntman", "j_drivers_license", "j_invisible", "j_blackboard",
    "j_blueprint", "j_brainstorm",
    "j_caino", "j_triboulet", "j_yorick", "j_chicot", "j_perkeo",
}

RETRIGGER_JOKERS = {
    "j_dusk", "j_hack", "j_sock_and_buskin", "j_hanging_chad", "j_selzer",
    "j_mime",
}

ECONOMY_JOKERS = {
    "j_golden", "j_rocket", "j_business", "j_delayed_grat", "j_cloud_9",
    "j_to_the_moon", "j_faceless", "j_mail", "j_trading", "j_egg",
    "j_credit_card", "j_bootstraps",
}


def validate_joker_keys(game) -> list[str]:
    """Return any curated key the installed game does not recognise."""
    known = set(game.eval(
        "(function() local t={} for k,v in pairs(G.P_CENTERS) do"
        " if v.set=='Joker' then t[#t+1]=k end end return table.concat(t,',') end)()"
    ).split(","))
    curated = XMULT_JOKERS | RETRIGGER_JOKERS | ECONOMY_JOKERS
    return sorted(curated - known)


class TunedPolicy(GreedyPolicy):
    """Greedy plus an economy model, joker quality and shop rerolling.

    Three things the diagnostics said were costing runs: money died unspent,
    hand levels stalled at 2, and joker slots filled with whatever cost most.
    """

    name = "tuned"

    def __init__(self, interest_cap: int = INTEREST_CAP, max_rerolls: int = 4,
                 early_ante: int = 2, flush_seed: int = 3,
                 seek_flush: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self.interest_cap = interest_cap
        self.max_rerolls = max_rerolls
        self.early_ante = early_ante
        # How many of a suit are needed before digging for the flush.
        self.flush_seed = flush_seed if seek_flush else 99
        self._rerolls_this_shop = 0
        self._last_round = None

    def quality(self, key: str, rarity: int = 0) -> int:
        """Rough tier, best first. Rarity alone is a poor guide -- Cavendish is
        a common that multiplies, and most rares do not."""
        if key in XMULT_JOKERS:
            return 4
        if key in RETRIGGER_JOKERS:
            return 3
        if key in ECONOMY_JOKERS:
            return 2
        return 1 if rarity >= 3 else 0

    def spendable(self, state: dict) -> int:
        """Money above the interest cap earns nothing and is free to spend.

        Below the cap, spending still beats hoarding early: a joker bought in
        ante 1 compounds for seven antes; $5 of interest does not.
        """
        if state["ante"] <= self.early_ante:
            return state["dollars"]
        return max(0, state["dollars"] - self.interest_cap)

    def hand(self, run, state: dict) -> tuple[str, Any]:
        planet = self._planet_to_use(run)
        if planet is not None:
            return ("use", planet)

        best, estimate = run.best_play()
        if not best:
            return ("play", [1])

        needed = state["blind_chips"] - state["chips"]
        share = needed / max(1, state["hands_left"])
        must_play = (state["hands_left"] <= 1 or state["discards_left"] <= 0)

        if must_play or estimate >= share:
            return ("play", best)

        junk = self._discard_toward_flush(run) or run.best_discard()
        return ("discard", junk) if junk else ("play", best)

    def _discard_toward_flush(self, run) -> list[int] | None:
        """Dig for a flush, the standard Balatro opening.

        A flush is 35 chips x 4 mult at level one against Two Pair's 20 x 2, and
        it levels faster, so holding a suit and pitching everything else is
        usually better than banking a mediocre made hand. Only worth doing from
        three of a suit: from two, the draw is too thin.
        """
        hand = run.hand()
        if not hand:
            return None
        suits = Counter(card["suit"] for card in hand)
        suit, count = suits.most_common(1)[0]
        if count < self.flush_seed or count >= 5:
            return None
        junk = [c["index"] for c in hand if c["suit"] != suit]
        return junk[:5] or None

    def shop(self, run, state: dict) -> tuple[str, Any]:
        if state["round"] != self._last_round:
            self._last_round = state["round"]
            self._rerolls_this_shop = 0

        planet = self._planet_to_use(run)
        if planet is not None:
            return ("use", planet)

        budget = self.spendable(state)
        items = run.shop_contents()
        affordable = [i for i in items if i["buyable"] and i["cost"] <= budget]
        room_for_consumable = state["consumable_count"] < state["consumable_limit"]

        # Planets first: $3 buys a permanent multiplier on every future hand of
        # that type, the cheapest scaling available.
        planets = [i for i in affordable
                   if i["set"] == "Planet" and room_for_consumable]
        if planets:
            return ("buy", (planets[0]["area"], planets[0]["index"]))

        jokers = [i for i in affordable if i["set"] == "Joker"]
        if jokers:
            jokers.sort(key=lambda i: (self.quality(i["key"], i.get("rarity") or 0),
                                       i.get("rarity") or 0, -i["cost"]))
            best = jokers[-1]
            has_room = state["joker_count"] < state["joker_limit"]
            if has_room and (self.quality(best["key"], best.get("rarity") or 0) > 0
                             or state["ante"] <= self.early_ante):
                return ("buy", (best["area"], best["index"]))

        if not room_for_consumable:
            held = run.consumables()
            spare = [c for c in held if c["set"] != "Planet"]
            if spare:
                return ("sell", ("consumeables", spare[0]["index"]))

        packs = [i for i in affordable if i["set"] == "Booster"]
        if packs:
            def pack_rank(item):
                key = item["key"]
                return 0 if "celestial" in key else (1 if "buffoon" in key else 2)
            packs.sort(key=pack_rank)
            return ("buy", (packs[0]["area"], packs[0]["index"]))

        vouchers = [i for i in affordable if i["set"] == "Voucher"]
        if vouchers:
            return ("buy", (vouchers[0]["area"], vouchers[0]["index"]))

        # Nothing worth buying: spend the surplus looking for something that is.
        # This is what stops money dying in the bank.
        if (self._rerolls_this_shop < self.max_rerolls
                and state["reroll_cost"] <= budget - 4):
            self._rerolls_this_shop += 1
            return ("reroll", None)

        return ("leave", None)

    def pack(self, run, state: dict) -> tuple[str, Any]:
        contents = run.pack_contents()
        if not contents:
            return ("skip_pack", None)

        planets = [c for c in contents if c["set"] == "Planet"]
        if planets:
            return ("pick", planets[0]["index"])

        jokers = [c for c in contents if c["set"] == "Joker"]
        if jokers and state["joker_count"] < state["joker_limit"]:
            jokers.sort(key=lambda c: self.quality(c["key"]))
            return ("pick", jokers[-1]["index"])

        cards = [c for c in contents if c["set"] in ("Default", "Enhanced")]
        if cards:
            return ("pick", cards[0]["index"])

        if state["hand_size"] > 0:
            return ("pick", contents[0]["index"])
        return ("skip_pack", None)
