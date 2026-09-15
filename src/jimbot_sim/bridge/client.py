"""Talk to a running, visible Balatro.

The headless engine and the real game expose the same `state` shape, so an
agent can drive either. What differs is timing: the real game animates, so an
action here only *starts* something. Every action therefore waits for the game
to become actionable again rather than assuming it already is.
"""

from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 34143
DEFAULT_BUILD = Path("vendor/modded_game/BalatroBot.exe")


class BridgeError(RuntimeError):
    pass


class NotReady(BridgeError):
    """The game is mid-animation and cannot accept the action yet."""


class BalatroBridge:
    """A client for the modded game."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: float = 15.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buffer = b""
        self._next_id = 1

    # ------------------------------------------------------------------

    def connect(self, retries: int = 1, delay: float = 1.0) -> "BalatroBridge":
        last: Exception | None = None
        for _ in range(max(1, retries)):
            try:
                self._sock = socket.create_connection((self.host, self.port),
                                                      self.timeout)
                self._sock.settimeout(self.timeout)
                self._buffer = b""
                return self
            except OSError as error:      # game not up yet
                last = error
                time.sleep(delay)
        raise BridgeError(
            f"could not reach the game on {self.host}:{self.port} -- is the "
            f"modded build running? ({last})")

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> "BalatroBridge":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------

    def _readline(self) -> str:
        assert self._sock is not None
        while b"\n" not in self._buffer:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise BridgeError("the game closed the connection")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line.decode("utf-8")

    def command(self, cmd: str, *args: Any) -> Any:
        if self._sock is None:
            raise BridgeError("not connected; call connect() first")
        request_id = self._next_id
        self._next_id += 1
        payload = " ".join([str(request_id), cmd, *(str(a) for a in args)])
        self._sock.sendall(payload.encode("utf-8") + b"\n")

        # Ctrl+C landing inside recv leaves an unread reply on the socket, so
        # the next request would read the previous answer. Skip anything older
        # than what was just asked for rather than erroring on it.
        while True:
            reply = json.loads(self._readline())
            reply_id = reply.get("id", 0)
            if reply_id >= request_id:
                break
        if reply_id != request_id:
            raise BridgeError(f"reply id {reply_id} != {request_id}; "
                              "the connection is out of sync")
        if not reply.get("ok"):
            raise BridgeError(reply.get("error") or "the game rejected the command")
        return reply.get("result")

    # ------------------------------------------------------------------

    def _pause(self, seconds: float) -> None:
        """Let the game advance between polls.

        The seam between driving the real game and driving the engine: here
        time passes on its own and we wait for it, while the headless engine
        advances only when it is pumped. Every wait below goes through this, so
        both are driven by exactly the same action code.
        """
        time.sleep(seconds)

    def hello(self) -> dict:
        return self.command("hello")

    def state(self) -> dict:
        return self.command("state")

    def wait_ready(self, timeout: float = 30.0, poll: float = 0.05) -> dict:
        """Block until the game is between animations and can take input."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.state()
            if state.get("ready"):
                return state
            self._pause(poll)
        raise NotReady(f"game did not become actionable within {timeout}s "
                       f"(stuck in {self.state().get('state_name')})")

    def wait_until(self, predicate, timeout: float = 30.0,
                   poll: float = 0.05) -> dict:
        """Block until `predicate(state)` holds and the game is actionable.

        Waiting on `ready` alone is not enough: several phases are themselves
        "ready", so an action fired from one of them would appear to have
        settled before the game had even begun animating it.
        """
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            last = self.state()
            if last.get("ready") and predicate(last):
                return last
            self._pause(poll)
        raise NotReady(f"condition not met within {timeout}s "
                       f"(state {last.get('state_name')})")

    def wait_for(self, predicate, timeout: float = 30.0,
                 poll: float = 0.05) -> dict:
        """Wait for a consequence, without requiring the game to be actionable.

        wait_until also demands `ready`, which is right for "may I act now" and
        wrong for "did that land": straight after a purchase the shop is busy
        restocking and reports not-ready, so a consequence that had already
        happened would never be observed.
        """
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            last = self.state()
            if predicate(last):
                return last
            self._pause(poll)
        raise NotReady(f"consequence not observed within {timeout}s "
                       f"(state {last.get('state_name')})")

    def wait_hand_dealt(self, timeout: float = 20.0) -> dict:
        """Wait for dealing to finish, not merely to have started.

        Cards arrive over several frames. A hand of three on its way to eight
        is indistinguishable from a finished hand of three unless you either
        know the limit or watch it stop changing -- so do both: settle for the
        full hand, and accept a smaller one that has stopped growing (the deck
        can run short).
        """
        deadline = time.time() + timeout
        last, stable = -1, 0
        while time.time() < deadline:
            state = self.state()
            size = len(state.get("hand") or [])
            limit = int(state.get("hand_limit") or 0)
            if size and limit and size >= limit:
                return state
            stable = stable + 1 if size == last and size > 0 else 0
            if stable >= 3:
                return state
            last = size
            self._pause(0.1)
        return self.state()

    def act(self, cmd: str, *args: Any, until=None,
            settle: float = 30.0) -> dict:
        """Run an action and wait for the change it causes to land."""
        self.command(cmd, *args)
        if until is None:
            return self.wait_ready(timeout=settle)
        return self.wait_until(until, timeout=settle)

    # convenience wrappers -------------------------------------------------

    def start_run(self, seed: str | None = None, deck: str | None = None) -> dict:
        args = [seed or "-"]
        if deck:
            # Spaces would break the whitespace-delimited command line.
            args.append(deck.replace(" ", "_"))
        self.command("start_run", *args)
        return self.wait_ready()

    def select_blind(self) -> dict:
        # Selecting is done when the blind is up and cards are actually dealt.
        return self.act("select_blind",
                        until=lambda s: (s["state_name"] == "SELECTING_HAND"
                                         and len(s.get("hand") or []) > 0))

    def toggle(self, index: int) -> dict:
        before = self.state().get("selection_size", 0)
        return self.act("toggle", index, settle=5.0,
                        until=lambda s: s.get("selection_size") != before)

    def play(self) -> dict:
        state = self.state()
        if not state.get("selection_size"):
            raise BridgeError("play called with no cards selected")
        hands = state["hands_left"]
        return self.act("play",
                        until=lambda s: (s.get("hands_left", hands) < hands
                                         or s["state_name"] != "SELECTING_HAND"))

    def discard(self) -> dict:
        state = self.state()
        if not state.get("selection_size"):
            raise BridgeError("discard called with no cards selected")
        left = state["discards_left"]
        return self.act("discard",
                        until=lambda s: s.get("discards_left", left) < left)

    def cash_out(self) -> dict:
        # Wait for the Cash Out button itself, not merely for the round-eval
        # box: the payout rows are still being added until the button appears,
        # and cashing out early tears the box out from under them.
        self.wait_until(lambda s: s.get("cash_out_ready"), timeout=30.0)
        state = self.act("cash_out",
                         until=lambda s: s["state_name"] != "ROUND_EVAL")
        # The payout is not one number: the blind reward, a dollar per unused
        # hand and the interest each arrive as their own event, and leaving
        # ROUND_EVAL does not mean they have. Reading the money before they
        # land reports the round as having paid nothing.
        return self.wait_money_settled() or state

    def wait_money_settled(self, stable: int = 4, timeout: float = 20.0) -> dict:
        """Wait for the bankroll to stop moving."""
        deadline = time.time() + timeout
        last, run = None, 0
        while time.time() < deadline:
            state = self.state()
            money = state.get("dollars")
            run = run + 1 if money == last else 0
            if run >= stable:
                return state
            last = money
            self._pause(0.05)
        return self.state()

    def _shop_size(self) -> int:
        return len(self.state().get("shop") or [])

    def buy(self, area: str, index: int) -> dict:
        """Buy a shop item and wait for it to actually be bought.

        Waiting on `ready` is not enough: in the shop it is already true, and
        the purchase itself is queued behind whatever animation is in flight,
        so this returned before anything had happened -- silently, which then
        derailed every later action in a replay. Wait for a consequence: money
        spent, or the item off the shelf.
        """
        # Wait until the item is actually on the shelf, not merely until the
        # shop exists: the rows fill in over several frames.
        def offered(state):
            return any(i["area"] == area and i["index"] == index
                       for i in (state.get("shop") or []))

        # Precondition is that *this* item is on the shelf -- not that the
        # joker row has cards. A row the player bought out is legitimately
        # empty while vouchers and packs are still perfectly buyable.
        self.wait_until(lambda s: s.get("shop_settled") and offered(s),
                        timeout=30.0)
        before = self.state()
        money = before["dollars"]
        item = next(i for i in before["shop"]
                    if i["area"] == area and i["index"] == index)
        cost = int(item["cost"])
        jokers = len(before.get("jokers") or [])
        consumables = len(before.get("consumables") or [])
        deck = before.get("deck_size", 0)
        # How many are redeemed, not which: `vouchers` is a list of keys, and
        # comparing the list itself with `>` raised on the first voucher any
        # client ever bought -- an empty Lua table arrives as a dict, and a
        # dict does not order against a list. Nothing had bought one before:
        # the recordings' replays go through their own handlers and no policy
        # driven over this client had reached a shop with money to spare.
        vouchers = len(before.get("vouchers") or [])

        self.command("buy", area, index)

        # Wait for the money to leave. Not for the item to vanish from the
        # shop listing: that listing is empty for a frame or two while the
        # rows restock, and "the item is not there" is then trivially true --
        # which reported purchases that had not happened.
        def paid(state):
            if cost > 0:
                return state["dollars"] <= money - cost
            # A free item pays nothing, so there is no money to watch. The old
            # fallback -- "the item is no longer at that index" -- is wrong
            # rather than merely weak: buying out of a row re-indexes it, so
            # the card behind the one just bought slides into the same index
            # and the condition never comes true. Seltzer at $0 hung on this
            # for the full timeout. Arrival is the honest signal for these.
            return True

        def arrived(state):
            # The money leaves before the card lands, and until it has landed
            # in the joker tray the game will not let it be sold or used --
            # can_sell_card requires area.config.type == 'joker'. Waiting only
            # for payment hands back a card that is still in flight.
            #
            # A playing card goes to neither tray. The Magic Trick voucher puts
            # them in the shop, and buy_from_shop sends them straight to the
            # deck, so that is where their arrival shows up.
            return (len(state.get("jokers") or []) > jokers
                    or len(state.get("consumables") or []) > consumables
                    or state.get("deck_size", deck) > deck
                    or len(state.get("vouchers") or []) > vouchers)

        def bought(state):
            if state.get("in_pack") or state["state_name"] != "SHOP":
                return True
            return paid(state) and arrived(state)

        self.wait_for(bought, timeout=30.0)
        return self.state()

    def sell(self, area: str, index: int) -> dict:
        """Sell a card and wait for it to actually leave the tray.

        Money arrives before the card does: ease_dollars lands while the card
        is still dissolving, so waiting on the balance alone reports a joker
        that is visibly still there.

        And *that* card, not the row's length. An Invisible Joker held for two
        rounds copies another joker into the row as it is sold (card.lua:1599,
        2371-2390), so the row is as long afterwards as before and a wait for
        it to shrink reported a finished sale as "the card was never used".
        Jokers carry an id to watch; consumables do not, and nothing sold from
        there makes another, so their count still serves.
        """
        # can_sell_card opens with the same guard as can_use_consumeable, so a
        # sell issued straight after a buy or a reroll is refused outright.
        self.wait_idle()
        key = "jokers" if area == "jokers" else "consumables"
        before = self.state()
        row = before.get(key) or []
        money, held = before["dollars"], len(row)
        sold = (row[index - 1].get("id")
                if key == "jokers" and 0 < index <= held else None)

        def gone(state):
            now = state.get(key) or []
            if sold is None:
                return len(now) < held
            return all(r.get("id") != sold for r in now)

        self.command("sell", area, index)
        self._await_use(lambda s: s["dollars"] != money and gone(s))
        return self.state()

    def buy_pack(self, area: str, index: int) -> dict:
        """Buy a booster pack and wait for it to open.

        The purchase and the pack opening are separate steps; returning on the
        money change leaves the caller acting on a shop that is about to become
        a pack screen.
        """
        self.wait_until(
            lambda s: (s.get("shop_settled")
                       and any(i["area"] == area and i["index"] == index
                               for i in (s.get("shop") or []))),
            timeout=30.0)
        before = self.state()
        money = before["dollars"]
        cost = int(next(i for i in before["shop"]
                        if i["area"] == area and i["index"] == index)["cost"])
        self.command("buy", area, index)

        # A free pack pays nothing, so there is no money to watch -- the same
        # case buy() already handles. Astronomer zeroes a Celestial booster
        # (card.lua:380) and re-costs the one already on the shelf the moment
        # it arrives (card.lua:616-621); requiring the balance to move sat in
        # PLANET_PACK for the whole timeout. Seed H7NS6Y2Y, Checkered, stake 1.
        def paid(state):
            return cost <= 0 or state["dollars"] != money

        # Wait for the pack to open *and* stock itself, not merely for the
        # state to flip.
        self.wait_for(lambda s: (s.get("in_pack") and paid(s)
                                 and s.get("pack")), timeout=30.0)
        return self.state()

    def reroll(self) -> dict:
        """Reroll the shop and wait for the shelves to actually change.

        Not for the money to move: a reroll is free under Chaos the Clown, and
        the Reroll Surplus and Reroll Glut vouchers make it free again, so
        watching the bankroll waits for something that never happens. What
        always changes is the shelf -- the jokers on offer are replaced.
        """
        before = self.state()
        money = before["dollars"]
        shelf = [(i["area"], i["index"], i["center"])
                 for i in (before.get("shop") or [])]

        self.command("reroll")

        def rerolled(state):
            if state["dollars"] != money:
                return True
            current = [(i["area"], i["index"], i["center"])
                       for i in (state.get("shop") or [])]
            return bool(current) and current != shelf

        self.wait_for(rerolled, timeout=30.0)
        return self.state()

    def _await_use(self, landed, timeout: float = 30.0) -> dict:
        """Wait for a card use to land, reporting the game's reason if it does not.

        The use itself runs inside a game event, well after the command was
        answered, so a refusal there cannot come back as a command error. The
        game leaves its reason in the state instead.
        """
        try:
            return self.wait_for(landed, timeout=timeout)
        except NotReady:
            reason = (self.state().get("last_refusal") or "").strip()
            raise BridgeError(reason or "the card was never used") from None

    def wait_idle(self, stable: int = 3, timeout: float = 20.0) -> dict:
        """Wait for the game to be idle and *stay* idle.

        A single not-busy reading is not enough: a consumable resolves as a
        chain of queued events, and between two of them the game reads idle for
        a frame. Selecting in that gap and firing the next action lands right
        back inside the chain, where can_use_consumeable refuses it.
        """
        deadline = time.time() + timeout
        run = 0
        while time.time() < deadline:
            if self.state().get("busy"):
                run = 0
            else:
                run += 1
                if run >= stable:
                    return self.state()
            self._pause(0.05)
        return self.state()

    def select(self, cards) -> dict:
        """Highlight exactly `cards` (1-based hand positions), and check it took.

        The game refuses a selection rather than reporting one: over the hand
        limit, or mid-animation, `toggle` is a no-op. Verifying here turns a
        wrong-target bug into a plain error at the point it happens.
        """
        self.command("clear")
        for index_in_hand in cards:
            self.command("toggle", index_in_hand)
        state = self.state()
        got = len(state.get("selected") or [])
        if got != len(cards):
            raise BridgeError(
                f"selected {got} cards, wanted {len(cards)} ({list(cards)}) "
                f"of a hand of {len(state.get('hand') or [])}")
        return state

    def buy_and_use(self, area: str, index: int, cards=None) -> dict:
        """Buy a consumable and use it in one click, the shop's second button.

        Not a buy followed by a use: the card never reaches the consumable
        slots, so this works with them full, and waiting for it to land there
        would wait forever.
        """
        # Same precondition as buy: the item has to be on the shelf, and the
        # rows fill in over several frames.
        def offered(state):
            return any(i["area"] == area and i["index"] == index
                       for i in (state.get("shop") or []))

        self.wait_until(lambda s: s.get("shop_settled") and offered(s),
                        timeout=30.0)
        if cards:
            self.wait_idle()
            self.select(cards)
        before = self.state()
        money = before["dollars"]
        levels = dict(before.get("hand_levels") or {})
        stocked = len(before.get("shop") or [])
        self.command("buy_and_use", area, index)
        # The card leaving the shelf is the signal, not the money. What the
        # effect does to the balance is not predictable in the right direction:
        # a Hermit *doubles* it, so waiting for the balance to fall by the cost
        # waits forever. The real game only ever passed that test because the
        # payment lands a frame before the effect does -- the engine completes
        # both before anything can look.
        self._await_use(lambda s: (len(s.get("shop") or []) < stocked
                                   or s["dollars"] != money
                                   or dict(s.get("hand_levels") or {}) != levels))
        # Then let the use finish before anything else is attempted.
        self.wait_idle()
        return self.state()

    def use_consumable(self, index: int, cards=None) -> dict:
        """Use a held consumable, optionally on chosen cards.

        Usable during a pack as well as in a shop or a hand: a tarot held in
        the consumable slots can be played while an Arcana pack is open.
        """
        # Wait *before* selecting, not after. A consumable still resolving
        # clears the highlight, so a selection made while busy is silently
        # thrown away and the card is then used on nothing.
        self.wait_idle()
        if cards:
            self.wait_hand_dealt()
            self.select(cards)
        # Watch *which* consumables are held, not how many. A card that
        # consumes itself and creates another -- The Fool copying the last
        # Tarot used -- leaves the count where it started, and in the headless
        # engine the whole exchange happens inside a single pump, so there is
        # no moment when the count is down by one to be observed. Polling the
        # length there waits forever for a card that was used immediately.
        before = list(self.state().get("consumables") or [])
        self.command("use_consumable", index)
        self._await_use(
            lambda s: list(s.get("consumables") or []) != before)
        # A consumable that *creates* one moves the count twice: The Fool
        # removes itself, which satisfies the wait above, and only then does
        # the copy it made arrive. Returning on the first change leaves the new
        # card still in flight, so the next action addresses a slot that does
        # not exist yet and is refused as "the card was never used".
        self.wait_idle()
        return self.state()

    def pick_pack(self, index: int, cards=None,
                  closes: bool | None = None) -> dict:
        """Take a card from a pack and wait for it to arrive.

        The pack closing and the card landing are separate: moving on as soon
        as the pack shuts leaves the card still flying across the screen, which
        is visible as a joker drifting over the next screen.

        `closes` is whether this pick is the pack's last, when the caller
        knows. The state does not say how many picks are left, so without it
        the wait for the pack to close runs its full length after the first
        pick of a Mega pack, which stays open for the second.
        """
        # The pack's cards are dealt a few frames after the pack screen opens,
        # exactly as the shop's are. Picking before they land addresses an
        # index that does not exist yet.
        self.wait_for(lambda s: s.get("pack"), timeout=30.0)
        # A tarot taken from an Arcana pack is applied to cards chosen in hand,
        # exactly as one used from the consumable slots is. Without selecting
        # them first the card is taken but lands on nothing.
        # Idle first, whether or not there are targets: the pack opening leaves
        # STOP_USE raised for a couple of seconds, and can_use_consumeable
        # checks that before it checks anything about the card.
        self.wait_idle()
        if cards:
            self.wait_hand_dealt()
            self.select(cards)
        before = self.state()
        jokers = len(before.get("jokers") or [])
        consumables = len(before.get("consumables") or [])
        deck = before.get("deck_size", 0)
        packed = len(before.get("pack") or [])
        levels = before.get("hand_levels")
        self.command("pick_pack", index)
        self._await_use(
            lambda s: (len(s.get("jokers") or []) != jokers
                       or len(s.get("consumables") or []) != consumables
                       or s.get("deck_size", deck) != deck
                       or len(s.get("pack") or []) < packed
                       # A Planet used straight out of a pack owns nothing
                       # afterwards: it is not kept, no card moves, and the
                       # only mark it leaves is the hand it levelled.
                       or s.get("hand_levels") != levels
                       or not s.get("in_pack")))
        # Then let the pack finish closing, if it is going to. A Mega pack
        # allows two picks and legitimately stays open, so this must not be
        # treated as a failure -- it is a courtesy wait, not a condition.
        #
        # When the caller says the pack stays open there is no closing to
        # wait for, but the pick itself still has to finish, and the
        # courtesy wait was doing that too by running out its eight seconds.
        # The Hanged Man taken from a Mega Arcana pack destroys its two cards
        # in events 0.4s and 0.2s apart and then dissolves them
        # (card.lua:1271-1291); returning as the pack shrank handed back a
        # hand still holding them, and a live run stopped on the hand size.
        # use_card holds G.CONTROLLER.locks.use until the use is done, which
        # is what `busy` reads.
        if closes is False:
            return self.wait_idle()
        try:
            self.wait_until(lambda s: not s.get("in_pack"), timeout=8.0)
        except NotReady:
            pass
        return self.state()

    def leave_shop(self) -> dict:
        # The shop must have finished animating in before it can be closed.
        self.wait_until(lambda s: s.get("shop_settled"), timeout=30.0)
        return self.act("leave_shop",
                        until=lambda s: s["state_name"] != "SHOP")

    def skip_pack(self) -> dict:
        """Skip a booster, waiting for it to be skippable and then to be gone.

        `act` waits only for the game to be actionable, which it already is
        while the pack is still opening -- so the skip landed on a pack whose
        button did not exist yet and did nothing, leaving the replay in a pack
        it thought it had left.
        """
        self.wait_for(lambda s: s.get("in_pack"), timeout=30.0)
        self.wait_idle()
        self.command("skip_pack")
        return self.wait_for(lambda s: not s.get("in_pack"), timeout=30.0)


def launch(build: Path = DEFAULT_BUILD, wait: float = 90.0) -> subprocess.Popen:
    """Start the modded build and wait until it actually answers.

    Waiting for the port to accept is not enough: the OS completes a TCP
    handshake from the listen backlog before the game has called accept(), so a
    probe connection appears to succeed while the game is still loading -- and
    that probe then occupies the single client slot. The only reliable signal is
    a protocol reply, so this handshakes properly and hands back a live client.
    """
    build = Path(build)
    if not build.exists():
        raise BridgeError(
            f"{build} not found -- run scripts/build_modded_game.py first")
    # Kept rather than inherited, because the game says why it is quitting on
    # stderr and nowhere else. Without this the failure reads "exited during
    # startup (code 0)", which is indistinguishable between a Lua error in the
    # mod, a missing dependency, and the actual answer the first time it
    # happened: Steam was not running, so the Steam API refused and the game
    # closed itself cleanly.
    errors = tempfile.TemporaryFile()
    process = subprocess.Popen([str(build)], cwd=str(build.parent),
                               stderr=errors)

    def _exit_reason() -> str:
        try:
            errors.seek(0)
            tail = errors.read().decode("utf-8", "replace").strip()
        except OSError:                                   # pragma: no cover
            tail = ""
        if "steam" in tail.lower():
            return (" -- Steam is not running. The build needs the Steam "
                    "client up even though it launches outside it.")
        lines = [line for line in tail.splitlines() if line.strip()]
        return ("\n  " + "\n  ".join(lines[-6:])) if lines else ""

    deadline = time.time() + wait
    while time.time() < deadline:
        if process.poll() is not None:
            raise BridgeError(
                "the game exited during startup (code "
                f"{process.returncode}){_exit_reason()}")
        bridge = BalatroBridge(timeout=5.0)
        try:
            bridge.connect()
            bridge.hello()
            return process, bridge
        except (BridgeError, OSError, ValueError):
            bridge.close()
            time.sleep(1.0)
    process.terminate()
    raise BridgeError(f"the game did not answer within {wait}s")
