"""A scripted policy, good enough to be a baseline and to exercise every state.

It is deliberately simple and readable: pick the best-scoring hand, discard what
that hand does not use, buy jokers, level the hand you actually play. It is not
trying to be a strong Balatro player -- it is the bar a learned policy has to
clear, and the thing that proves the driver can reach ante 8.
"""

from __future__ import annotations

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
