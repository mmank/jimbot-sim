"""Whether two runs are still the same run: states compared in the game's words.

Every backend reports its state in the engine's shape (`jimbot_sim.state`), so
one comparison serves every pairing -- the game against its shadow in a live
run, the engine against the simulator in a sweep. It lived in the policy's
live driver, and the recording replay grew its own; a wait added to one never
reached the other.

Rows are compared by what the cards *are*, not by their ids: the engine
numbers centres by sorting `G.P_CENTERS` and the simulator from a vocabulary
frozen from a booted engine, and the two lists are the same only for the same
build. `Names` holds both and compares on the key.
"""

from __future__ import annotations

import re

from .state import ENHANCEMENT_KEYS, centres

# bot_api.lua's orderings, for reading a card row back out.
RANKS = {1: "2", 2: "3", 3: "4", 4: "5", 5: "6", 6: "7", 7: "8", 8: "9",
         9: "10", 10: "J", 11: "Q", 12: "K", 13: "A"}
SUITS = {1: "S", 2: "H", 3: "C", 4: "D"}
EDITIONS = {0: "", 1: "-foil", 2: "-holo", 3: "-poly", 4: "-neg"}
SEALS = {0: "", 1: "-gold", 2: "-red", 3: "-blue", 4: "-purple"}

# bot_api.lua's ENHANCEMENT_IDS, for reading the deck's make-up back out.
ENHANCEMENTS = {0: "plain", 1: "bonus", 2: "mult", 3: "wild", 4: "glass",
                5: "steel", 6: "stone", 7: "gold", 8: "lucky"}

# A playing card on a shelf or in a pack is reported by its *enhancement*
# centre -- c_base for a plain one -- on both sides. That does not name the
# card, so both sides call it "card" and the ranks are compared where they
# are visible.
CARD_KEYS = set(ENHANCEMENT_KEYS.values())

# A booster's centre key ends in a variant number -- p_buffoon_normal_1 and
# _2 -- and the variants are the same pack: game.lua:693 gives both weight
# 0.6, kind Buffoon, cost 4, two cards, choose one. They differ in `pos`,
# which is where the art sits on the sprite sheet.
#
# The number is not comparable, and not because the simulator is wrong.
# get_pack short-circuits the first shop of every run to a Buffoon pack and
# picks between the two with a bare `math.random(1, 2)` -- Lua's global
# stream, not `pseudorandom(pseudoseed(...))` (common_events.lua:1947). That
# stream carries whatever the game has done since it launched, so which of
# the two the player is shown is a coin flip the run's seed does not decide,
# and it came up on the wrong side of 52 of 96 sweep runs. What a player sees
# is a Buffoon Pack for $4 either way, so that is what is compared.
_PACK_VARIANT = re.compile(r"_\d+$")

# Compared after every action. Whole numbers on both sides, and the engine
# answers in Lua floats, so they are read as ints.
NUMBERS = ("ante", "dollars", "chips", "hands_left", "discards_left",
           "blind_chips", "joker_limit", "consumable_limit", "deck_size",
           "best_hand")


class Names:
    """Centre ids to keys, per side, so rows are compared by name.

    The engine numbers centres by sorting every key in `G.P_CENTERS`, and the
    simulator's side of the state dictionary is numbered from the frozen
    vocabulary that was captured from a booted engine. They are the same list
    for the same build and different lists for different ones, so both are
    read and the comparison is on the key rather than on the number.

    `live` is the engine's side, `sim` the simulator's. Built from the
    engine's own `key_list` and `blind_list`; with none given, both sides
    read the frozen vocabulary, which is right for two simulator states.
    """

    def __init__(self, key_list: str = "", blind_list: str = "") -> None:
        self.sim = self._table(centres().key_list)
        self.sim_blinds = self._table(centres().blind_list)
        self.live = self._table(key_list) if key_list else dict(self.sim)
        self.live_blinds = (self._table(blind_list) if blind_list
                            else dict(self.sim_blinds))

    @classmethod
    def of(cls, bridge) -> "Names":
        """The engine behind `bridge` against the frozen vocabulary."""
        return cls(bridge.command("key_list"), bridge.command("blind_list"))

    @staticmethod
    def _table(key_list: str) -> dict[int, str]:
        return {i: entry.partition("|")[0]
                for i, entry in enumerate(key_list.split(","), start=1)
                if entry}

    def agree(self) -> bool:
        return self.live == self.sim and self.live_blinds == self.sim_blinds

    def key(self, row: dict, live: bool) -> str:
        table = self.live if live else self.sim
        # Zero is "no centre", which on a shop row is the simulator's way of
        # saying the shelf holds a playing card.
        key = table.get(int(row.get("center") or 0), "card")
        if key in CARD_KEYS:
            return "card"
        return _PACK_VARIANT.sub("", key) if key.startswith("p_") else key

    def blind(self, row: dict, live: bool) -> str:
        table = self.live_blinds if live else self.sim_blinds
        return table.get(int(row.get("blind") or 0), "-")


# ----------------------------------------------------------------------
# what a state looks like, in one vocabulary
# ----------------------------------------------------------------------

def card_name(row: dict) -> str:
    """A hand row as a player would read it: KH, 7D-foil, 10S-gold."""
    name = "%s%s" % (RANKS.get(int(row.get("rank") or 0), "?"),
                     SUITS.get(int(row.get("suit") or 0), "?"))
    return name + EDITIONS.get(int(row.get("edition") or 0), "") \
        + SEALS.get(int(row.get("seal") or 0), "")


def hand(state: dict) -> list[str]:
    return [card_name(row) for row in state.get("hand") or []]


def rows(state: dict, names: Names, live: bool, field: str) -> list[str]:
    """A tray, by what each card is -- and for a joker, what it has become.

    The counter belongs here and was missing. Thirty-two of the jokers keep
    one and by ante six that number *is* the joker, so a counter that has
    stopped moving is a joker that has stopped working -- and nothing said
    so until it changed a score, which for a blind-clearing hand it never
    does. A Castle that never grew was invisible for exactly that reason:
    the round total was reset before the next comparison and only the run's
    best hand carried the difference.
    """
    out = []
    for row in state.get(field) or []:
        name = names.key(row, live) + EDITIONS.get(int(row.get("edition")
                                                       or 0), "")
        if field == "jokers":
            name += " %g/%g%s" % (float(row.get("counter") or 0),
                                  float(row.get("secondary") or 0),
                                  " debuffed" if row.get("debuffed") else "")
        out.append(name)
    return out


def shop(state: dict, names: Names, live: bool) -> list[str]:
    """The shelves, with the stickers a shop joker was stocked with.

    The stickers are rolled when the shop stocks the card, not when it is
    bought, so they belong to the shelf. Comparing only the key put a
    perishable Ride the Bus in the shadow and a plain one in the game, and
    said nothing until the sticker debuffed the joker eleven decisions later.
    """
    out = []
    for row in state.get("shop") or []:
        marks = "".join(m for m, k in (("E", "eternal"), ("P", "perishable"),
                                       ("R", "rental")) if row.get(k))
        out.append("%s[%d] %s $%d%s" % (row.get("area"),
                                        int(row.get("index") or 0),
                                        names.key(row, live),
                                        int(row.get("cost") or 0),
                                        " " + marks if marks else ""))
    return out


def blinds(state: dict, names: Names, live: bool) -> list[str]:
    """The run info screen's three rows: which blind, for how much, and where.

    Worth comparing for the boss above all. Which boss this ante holds is
    drawn from a pool the moment the ante turns over, it decides what the
    shop should be building for, and nothing but its chip requirement would
    otherwise show a wrong draw -- as a target twice the size, with no name
    on it.
    """
    out = []
    for row in state.get("blinds") or []:
        where = ("defeated" if row.get("defeated") else
                 "skipped" if row.get("skipped") else
                 "current" if row.get("current") else "upcoming")
        out.append("%s %s %d %s" % (row.get("kind"), names.blind(row, live),
                                    int(row.get("chips") or 0), where))
    return out


def levels(state: dict) -> dict:
    return {name: int(data.get("level") or 0)
            for name, data in (state.get("hand_levels") or {}).items()}


def deck_cards(state: dict) -> dict:
    """What the whole deck holds, by name: {"6": 4, "D": 13, "steel": 1}.

    The engine reports the deck as counts rather than as cards -- ranks,
    suits, enhancements, seals and editions over G.playing_cards -- and both
    sides have reported it all along for the observation encoder. Nothing
    compared it, so two decks holding different cards read as the same run
    until a draw happened to deal the difference: FATBOY02 stopped 299
    decisions in with a 6D in the game's hand and a red-sealed 6H in the
    shadow's, which is a deck that had disagreed for some time.

    Only the counts, so this catches a card that is not the same card. Two
    decks holding the same cards in an order that deals differently -- a card
    whose id is out of step with when it was added (see GameState._start_round
    on pseudoshuffle) -- still needs the cards themselves, which the mod does
    not report.
    """
    block = state.get("deck_cards") or {}
    out: dict[str, int] = {}

    def tally(field: str, labels: dict, base: int, prefix: str = "") -> None:
        for slot, count in enumerate(block.get(field) or []):
            label = labels.get(slot + base)
            if label is None or not count:
                continue
            out[prefix + (label.lstrip("-") or "none")] = int(count)

    tally("ranks", RANKS, 1)
    tally("suits", SUITS, 1)
    tally("enhancements", ENHANCEMENTS, 0)
    tally("seals", SEALS, 0, "seal ")
    tally("editions", EDITIONS, 0, "edition ")
    return out


def differences(state: dict, shadow: dict, names: Names) -> list[str]:
    """Every way the engine's state and the simulator's disagree.

    `state` is the engine's side (read with `names.live`), `shadow` the
    simulator's. One line per field, "field  game X / shadow Y"; the field
    is the first word, which is what a caller keys a wait or a tally on.
    """
    out = []

    def note(field, want, got):
        if want != got:
            out.append("%-14s game %s\n%-14s shadow %s"
                       % (field, want, "", got))

    note("state_name", state.get("state_name"), shadow.get("state_name"))
    for field in NUMBERS:
        note(field, int(state.get(field) or 0), int(shadow.get(field) or 0))
    note("hand", hand(state), hand(shadow))
    # Named down to what differs: a whole deck either side of the line is
    # unreadable, and what is wanted is the card that is not the same card.
    held, mirrored = deck_cards(state), deck_cards(shadow)
    if held != mirrored:
        keys = sorted(k for k in set(held) | set(mirrored)
                      if held.get(k, 0) != mirrored.get(k, 0))
        note("deck_cards", {k: held.get(k, 0) for k in keys},
             {k: mirrored.get(k, 0) for k in keys})
    note("jokers", rows(state, names, True, "jokers"),
         rows(shadow, names, False, "jokers"))
    note("consumables", rows(state, names, True, "consumables"),
         rows(shadow, names, False, "consumables"))
    note("vouchers", sorted(state.get("vouchers") or []),
         sorted(shadow.get("vouchers") or []))
    note("blinds", blinds(state, names, True), blinds(shadow, names, False))
    note("hand_levels", levels(state), levels(shadow))
    # The shelves are only worth comparing where both sides have them: the
    # engine holds the last shop's cards until the next one stocks.
    if state.get("state_name") == "SHOP" == shadow.get("state_name"):
        note("shop", shop(state, names, True), shop(shadow, names, False))
    if state.get("in_pack") and shadow.get("state_name", "").endswith("PACK"):
        note("pack", rows(state, names, True, "pack"),
             rows(shadow, names, False, "pack"))
    return out
