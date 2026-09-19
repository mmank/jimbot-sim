"""Drive the headless engine through the same client the real game uses.

The bridge client knows a lot that is not about sockets: that a bought card has
not arrived until it is in a tray, that a pack deals its targeting hand over
several frames, that selling is refused while a consumable resolves. None of
that should be written twice.

So this is not a second client. It is the same one, with three things swapped:
commands go to bot_api in-process instead of over a socket, state comes back as
a Lua table instead of JSON, and waiting means pumping frames instead of
sleeping. Everything else -- every action, every consequence it waits for --
is inherited.
"""

from __future__ import annotations

from typing import Any

from .client import BalatroBridge, BridgeError

# Frames per pump. Enough for a queued event chain to make progress without
# overshooting a state the caller is waiting to observe.
PUMP_FRAMES = 8


def to_python(value: Any) -> Any:
    """Convert a Lua table to plain dicts and lists, recursively.

    Lua has one table type for both, so arrays arrive keyed 1..n; the client
    expects a list there and a dict everywhere else.
    """
    if not hasattr(value, "keys"):
        return value
    keys = list(value.keys())
    if keys and keys == list(range(1, len(keys) + 1)):
        return [to_python(value[k]) for k in keys]
    return {k: to_python(value[k]) for k in keys}


class HeadlessBridge(BalatroBridge):
    """A BalatroBridge backed by the in-process engine."""

    def __init__(self, engine) -> None:
        super().__init__()
        self.engine = engine
        self._sock = object()      # satisfies the base class's connected check

    # -- transport ----------------------------------------------------------

    def connect(self, retries: int = 1, delay: float = 1.0) -> "HeadlessBridge":
        return self

    def close(self) -> None:
        pass

    def pump(self, frames: int = PUMP_FRAMES) -> None:
        self.engine.execute(f"api.pump({int(frames)})")

    def _pause(self, seconds: float) -> None:
        # Time does not pass on its own here; it is pumped. The requested
        # duration is ignored deliberately -- what a caller means by "wait a
        # moment" is "let the game get further", and frames are that currency.
        self.pump()

    def set_speed(self, speed: float, reduced_motion: bool = True) -> dict:
        # The engine sets its own, and pumps frames rather than waiting for
        # them: see headless_patch.fast_forward.
        return {}

    def command(self, cmd: str, *args: Any) -> Any:
        fn = self.engine.eval(f"BOT_CMD.{cmd}")
        if fn is None:
            raise BridgeError(f"unknown command: {cmd}")
        table = self.engine.eval("{}")
        for i, arg in enumerate(args, start=1):
            table[i] = str(arg)
        try:
            result = fn(table)
        except Exception as error:                 # a Lua error, e.g. a refusal
            raise BridgeError(str(error)) from None
        # Actions queue events; let them run, as the real game's frames would.
        self.pump()
        return to_python(result)

    def state(self) -> dict:
        return to_python(self.engine.eval("BOT_CMD.state()"))
