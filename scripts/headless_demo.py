"""Show the real game running headless: content counts, a hand, a score."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from balatro_headless import HeadlessBalatro  # noqa: E402

SEED = "ABCDEFGH"


def count(game, kind: str) -> int:
    return game.eval("(function() local n=0 for _,v in pairs(G.P_CENTERS) do"
                     f" if v.set=='{kind}' then n=n+1 end end return n end)()")


def show_hand(game) -> str:
    return game.eval("(function() local t={} for i,c in ipairs(G.hand.cards) do"
                     " t[#t+1]=i..':'..c.base.value..c.base.suit:sub(1,1) end"
                     " return table.concat(t,'  ') end)()")


def main() -> None:
    started = time.perf_counter()
    game = HeadlessBalatro().boot()
    print(f"booted the real game in {(time.perf_counter() - started) * 1000:.0f} ms\n")

    print("content loaded straight from the game:")
    for kind in ("Joker", "Tarot", "Planet", "Spectral", "Voucher"):
        print(f"  {kind + 's':10s} {count(game, kind)}")
    print()

    game.execute(f"G:start_run({{seed = '{SEED}'}})")
    game.execute("api.select_blind()")
    print(f"seed {SEED} -- {game.eval('G.GAME.blind.name')}, "
          f"need {game.eval('G.GAME.blind.chips')} chips, "
          f"${game.eval('G.GAME.dollars')}, "
          f"{game.eval('G.GAME.current_round.hands_left')} hands / "
          f"{game.eval('G.GAME.current_round.discards_left')} discards")
    print("  hand:", show_hand(game))

    scored = game.eval("api.play({2,3,4,5})")
    print(f"  played 8H 8D 7S 7H -> Two Pair for {scored} chips")
    print("  (20 base + 30 card chips) x 2 mult = 100\n")

    print("the same hand, with jokers, scored by the real engine:")
    for jokers, note in [((), "no jokers"),
                         (("j_joker",), "Joker, +4 Mult"),
                         (("j_duo",), "The Duo, X2 Mult"),
                         (("j_joker", "j_duo"), "Joker then The Duo"),
                         (("j_duo", "j_joker"), "The Duo then Joker"),
                         (("j_blueprint", "j_duo"), "Blueprint copying The Duo")]:
        fresh = HeadlessBalatro().boot()
        fresh.execute(f"G:start_run({{seed = '{SEED}'}})")
        for joker in jokers:
            fresh.execute(f'add_joker("{joker}")')
        fresh.execute("api.select_blind()")
        print(f"  {note:30s} {fresh.eval('api.play({2,3,4,5})'):>5}")


if __name__ == "__main__":
    main()
