"""TCP client for the in-game mod.

This is the Python half of the hybrid setup: policies train against the fast
Python engine, then this drives the real game to check that the engine's rules
and the real ones agree. The Lua half is not written yet -- see README.
"""

from __future__ import annotations

import socket
from typing import Any, Iterator

from .protocol import (DEFAULT_HOST, DEFAULT_PORT, PROTOCOL_VERSION,
                       ProtocolError, Reply, Request, action_to_request)


class BridgeClient:
    """Blocking newline-delimited JSON client."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buffer = b""
        self._next_id = 1

    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), self.timeout)
        self._sock.settimeout(self.timeout)
        self._buffer = b""

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> "BridgeClient":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------------

    def _send(self, request: Request) -> Reply:
        if self._sock is None:
            raise ProtocolError("not connected; call connect() first")
        request.id = self._next_id
        self._next_id += 1
        self._sock.sendall(request.to_json().encode("utf-8") + b"\n")
        reply = Reply.from_json(self._read_line())
        if reply.id != request.id:
            raise ProtocolError(f"reply id {reply.id} does not match request "
                                f"{request.id}; the mod is out of sync")
        if not reply.ok:
            raise ProtocolError(reply.error or "the mod rejected the command")
        return reply

    def _read_line(self) -> str:
        assert self._sock is not None
        while b"\n" not in self._buffer:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ProtocolError("the mod closed the connection")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line.decode("utf-8")

    # ------------------------------------------------------------------

    def hello(self) -> dict[str, Any]:
        reply = self._send(Request("hello", extra={"version": PROTOCOL_VERSION}))
        return reply.state or {}

    def state(self) -> dict[str, Any]:
        reply = self._send(Request("state"))
        if reply.state is None:
            raise ProtocolError("the mod returned no state")
        return reply.state

    def start_run(self, seed: str | None = None, deck: str = "Red Deck",
                  stake: str = "White Stake") -> dict[str, Any]:
        extra: dict[str, Any] = {"deck": deck, "stake": stake}
        if seed is not None:
            extra["seed"] = seed
        reply = self._send(Request("start_run", extra=extra))
        return reply.state or {}

    def send_action(self, action) -> dict[str, Any]:
        """Apply an engine `Action` to the running game."""
        reply = self._send(action_to_request(action))
        return reply.state or {}

    def play_policy(self, policy, max_actions: int = 5000) -> Iterator[dict[str, Any]]:
        """Drive the real game with a policy that maps a GameState to an Action.

        The state adapter must be able to reconstruct a `GameState` the policy
        understands; anything the mod reports that the engine cannot represent
        raises rather than being silently dropped.
        """
        from .adapter import state_from_json

        raw = self.state()
        for _ in range(max_actions):
            game = state_from_json(raw)
            if game.is_over:
                return
            raw = self.send_action(policy(game))
            yield raw
