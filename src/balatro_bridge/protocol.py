"""Wire protocol between this package and a Balatro mod running in the game.

Newline-delimited JSON over TCP, chosen because Lua can emit it with a tiny
hand-rolled serialiser and because it is trivial to log and replay.

    -> {"id": 1, "cmd": "state"}
    <- {"id": 1, "ok": true, "state": {...}}
    -> {"id": 2, "cmd": "play", "cards": [0, 2, 4]}
    <- {"id": 2, "ok": true, "state": {...}}

Every reply carries the resulting state, so the client never has to poll.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 34143

# Commands the mod is expected to understand. These mirror `balatro.ActionType`
# so a policy trained against the engine can drive the real game unchanged.
COMMANDS = (
    "hello",           # handshake, returns mod + protocol version
    "state",           # no side effects, returns the current game state
    "select_blind",
    "skip_blind",
    "play",            # cards: hand indices
    "discard",         # cards: hand indices
    "use_consumable",  # index, cards
    "sell_joker",      # index
    "sell_consumable",  # index
    "buy",             # index
    "buy_voucher",
    "reroll",
    "buy_pack",        # index
    "pick_pack",       # index, cards
    "skip_pack",
    "leave_shop",
    "start_run",       # seed, deck, stake -- for scripted evaluation
)


class ProtocolError(RuntimeError):
    """The mod sent something that does not fit the protocol."""


@dataclass
class Request:
    cmd: str
    id: int = 0
    index: int | None = None
    cards: list[int] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        payload: dict[str, Any] = {"id": self.id, "cmd": self.cmd}
        if self.index is not None:
            payload["index"] = self.index
        if self.cards:
            payload["cards"] = list(self.cards)
        payload.update(self.extra)
        return json.dumps(payload, separators=(",", ":"))


@dataclass
class Reply:
    id: int
    ok: bool
    state: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def from_json(cls, line: str) -> "Reply":
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"not JSON: {line[:120]!r}") from exc
        if not isinstance(data, dict) or "ok" not in data:
            raise ProtocolError(f"malformed reply: {line[:120]!r}")
        return cls(
            id=int(data.get("id", 0)),
            ok=bool(data["ok"]),
            state=data.get("state"),
            error=data.get("error"),
        )


def action_to_request(action, request_id: int = 0) -> Request:
    """Translate an engine `Action` into a mod command."""
    from balatro.game import ActionType

    cmd = {
        ActionType.SELECT_BLIND: "select_blind",
        ActionType.SKIP_BLIND: "skip_blind",
        ActionType.PLAY: "play",
        ActionType.DISCARD: "discard",
        ActionType.USE_CONSUMABLE: "use_consumable",
        ActionType.SELL_JOKER: "sell_joker",
        ActionType.SELL_CONSUMABLE: "sell_consumable",
        ActionType.BUY: "buy",
        ActionType.BUY_VOUCHER: "buy_voucher",
        ActionType.REROLL: "reroll",
        ActionType.BUY_PACK: "buy_pack",
        ActionType.PICK_PACK: "pick_pack",
        ActionType.SKIP_PACK: "skip_pack",
        ActionType.LEAVE_SHOP: "leave_shop",
    }[action.type]
    return Request(
        cmd=cmd,
        id=request_id,
        index=action.index if action.index >= 0 else None,
        cards=list(action.cards),
    )
