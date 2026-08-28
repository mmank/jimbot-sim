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

        reply = json.loads(self._readline())
        if reply.get("id") != request_id:
            raise BridgeError(f"reply id {reply.get('id')} != {request_id}; "
                              "the connection is out of sync")
        if not reply.get("ok"):
            raise BridgeError(reply.get("error") or "the game rejected the command")
        return reply.get("result")

    # ------------------------------------------------------------------

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
            time.sleep(poll)
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
            time.sleep(poll)
        raise NotReady(f"condition not met within {timeout}s "
                       f"(state {last.get('state_name')})")

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
        return self.act("cash_out",
                        until=lambda s: s["state_name"] != "ROUND_EVAL")

    def buy(self, area: str, index: int) -> dict:
        self.wait_until(lambda s: s.get("shop_settled"), timeout=30.0)
        return self.act("buy", area, index)

    def leave_shop(self) -> dict:
        # The shop must have finished animating in before it can be closed.
        self.wait_until(lambda s: s.get("shop_settled"), timeout=30.0)
        return self.act("leave_shop",
                        until=lambda s: s["state_name"] != "SHOP")

    def skip_pack(self) -> dict:
        return self.act("skip_pack")


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
    process = subprocess.Popen([str(build)], cwd=str(build.parent))

    deadline = time.time() + wait
    while time.time() < deadline:
        if process.poll() is not None:
            raise BridgeError(
                f"the game exited during startup (code {process.returncode})")
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
