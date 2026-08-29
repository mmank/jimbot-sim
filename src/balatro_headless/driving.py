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
        # with them. Settle it, then let the policy choose.
        driver.settle_pack()
        return False
    if (name == "SHOP" and not state["shop_ready"]
            and settled < SHOP_PATIENCE):
        # Open is not the same as stocked. Deciding before the cards arrive
        # means every buy is masked out and only leave and reroll remain, which
        # is not the decision the policy was trained on. Bounded, because a
        # shop bought out of jokers never stocks and would hang the run.
        driver.wait()
        return True
    return False
