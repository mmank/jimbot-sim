"""Which phases a driver advances by itself, and which it asks the policy about.

This is interface, not mechanism. The observation and the legality mask were
already shared between training and play; *which phases get offered at all* was
not, and it turns out to be just as much a part of the contract. The agent has
no cash-out action because the training environment cashes out on its own -- so
a driver that offers ROUND_EVAL as a decision asks a question the policy has
never seen. It picked `clear` twenty-three times in a row, that being the only
legal action and one that changes nothing.

The decision lives here; the mechanics differ per runtime and live behind
`Driver`. The engine advances time by pumping frames; the real game advances on
its own and is waited for. Both cash out at the same moment.
"""

from __future__ import annotations

from typing import Protocol

# States the game resolves by itself, given time.
AUTO_STATES = {"DRAW_TO_HAND", "HAND_PLAYED", "NEW_ROUND", "PLAY_TAROT"}
PACK_STATES = {"TAROT_PACK", "PLANET_PACK", "SPECTRAL_PACK", "STANDARD_PACK",
               "BUFFOON_PACK"}
TERMINAL_STATES = {"GAME_OVER", "MENU", "SPLASH"}

# Frames to advance per poll while the game resolves a state on its own.
AUTO_PUMP_STEP = 12

# How long to wait for a shop to stock before deciding anyway. A shop whose
# joker row the player bought out never becomes "ready", and waiting on it
# forever is a hang -- it is still perfectly usable for vouchers, packs,
# rerolling and leaving.
SHOP_PATIENCE = 60

# How long to wait for the game's own use-suppression counter to clear.
#
# stop_use() raises G.GAME.STOP_USE and queues a chain of seven nested events
# to lower it again -- a guard so that a card cannot be used part-way through
# an animation. Nothing in headless is animating, but the chain still needs
# seven ticks of the event manager, and a driver that stops pumping the moment
# a decision phase is reached hands the policy a state where the counter is
# still up.
#
# While it is up, Card:can_sell_card and Card:can_use_consumeable both return
# false for everything. Measured at a blind select holding one joker: every
# other condition clear, STOP_USE = 1, and the joker unsellable. So the agent
# could neither sell a joker nor use a consumable on the blind select screen,
# which is where a player does both -- selling before a boss, using a Tarot on
# the deck. It is not a rule of the game; it is the driver reading too early.
STOP_USE_PATIENCE = 30


class Driver(Protocol):
    """What advancing a phase needs, in whichever runtime."""

    def wait(self) -> None:
        """Let the game get further: pump frames, or wait for the real clock."""

    def cash_out(self) -> None:
        """Take the round's payout and move to the shop."""

    def settle_pack(self) -> None:
        """Let a booster finish dealing before its contents are chosen."""


def is_over(state) -> bool:
    return state["state_name"] in TERMINAL_STATES or bool(state["won"])


def advance(state, driver: Driver, settled: int = 0) -> bool:
    """Advance one phase the policy is not asked about.

    Returns True when the driver acted and the state should be read again;
    False when this is a phase the policy decides. `settled` is how many times
    the caller has already advanced without a decision in between, which bounds
    the one wait here that can otherwise never end.
    """
    name = state["state_name"]
    if is_over(state):
        return False
    if name in AUTO_STATES:
        driver.wait()
        return True
    if name == "ROUND_EVAL":
        driver.cash_out()
        return True
    if name in PACK_STATES:
        # A pack deals its contents over several frames, and its targeting hand
        # with them. Settle it, then let the policy choose -- once the guard
        # opening it raised is down too (below). Returning here without that
        # put every fresh pack to the policy with no joker sellable and no
        # consumable usable.
        driver.settle_pack()
    if (state.get("stop_use") or 0) > 0 and settled < STOP_USE_PATIENCE:
        # Bounded, like the shop wait: if it somehow never clears, deciding
        # with a stale guard is better than hanging the run.
        driver.wait()
        return True
    if (name == "SHOP" and not state["shop_ready"]
            and settled < SHOP_PATIENCE):
        # Open is not the same as stocked. Deciding before the cards arrive
        # means every buy is masked out and only leave and reroll remain, which
        # is not the decision the policy was trained on. Bounded, because a
        # shop bought out of jokers never stocks and would hang the run.
        driver.wait()
        return True
    return False
