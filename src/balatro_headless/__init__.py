"""Run Balatro's own Lua headlessly, in-process, with no LOVE and no window.

    from balatro_headless import HeadlessBalatro

    game = HeadlessBalatro().boot()
    game.execute("G:start_run({seed = 'ABCDEFGH'})")
    game.execute("api.select_blind()")
    game.eval("api.play({2,3,4,5})")     # -> real chips scored

The engine is the shipped game, so every joker, blind, voucher and edition
behaves exactly as it does when you play it.
"""

from .runtime import (DEFAULT_INSTALL, DEFAULT_SOURCE, ExtractionError,
                      HeadlessBalatro, engine_available, extract_source)

__all__ = ["DEFAULT_INSTALL", "DEFAULT_SOURCE", "ExtractionError",
           "HeadlessBalatro", "engine_available", "extract_source"]
