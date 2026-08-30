"""The game's own randomness, reproduced exactly.

Balatro does not draw from one stream. Every decision names a pool -- "Joker1",
"shop_pack", "erratic", "cry_e" -- and each pool keeps its own state, so how
often the shop rolls cannot shift what a card's edition rolls. That is what
makes a seed shareable, and it is also why a simulator that draws from Python's
`random` plays a *different game* on the same seed: same rules, wrong world.
A policy trained against the wrong distribution learns shop odds that are not
real, which is the sim-to-real gap that is easy to miss because every rule
looks right.

Two layers, both exact here:

    pseudohash / pseudoseed   float arithmetic over the pool state
    TW223                     LuaJIT's math.random, a Tausworthe generator
                              (L'Ecuyer 1991, period 2^223)

`pseudorandom` seeds the second from the first, which means reproducing the
game needs LuaJIT's generator bit-for-bit, not merely a good PRNG. Verified
draw-for-draw against the real engine in tests/test_rng.py -- exact doubles,
not rounded prints, because the two agree to ~14 digits under any generator
and only bit comparison tells replication from coincidence.
"""

from __future__ import annotations

import math
import struct
from typing import Iterable, Sequence

M64 = (1 << 64) - 1
# i, k, q, s -- L'Ecuyer table 3, first entry: L=64, J=4, k=223, N1=49.
_TW223_PARAMS = ((0, 63, 31, 18), (1, 58, 19, 28), (2, 55, 24, 7), (3, 47, 21, 8))


def pseudohash(text: str) -> float:
    """The game's string hash: a reverse fold over the bytes."""
    num = 1.0
    data = text.encode("latin-1", "replace")
    for i in range(len(data), 0, -1):
        num = ((1.1239285023 / num) * data[i - 1] * math.pi + math.pi * i) % 1
    return num


def _round13(value: float) -> float:
    """Lua's string.format("%.13f"), which the game uses to clamp drift."""
    return float("%.13f" % value)


class TW223:
    """LuaJIT's math.random. Seeded with a double, as math.randomseed does."""

    __slots__ = ("gen",)

    def __init__(self, seed: float) -> None:
        self.gen = [0, 0, 0, 0]
        r = 0x11090601                      # 64-k[i], four 8-bit constants
        for i in range(4):
            m = 1 << (r & 255)
            r >>= 8
            seed = seed * 3.14159265358979323846 + 2.7182818284590452354
            u = struct.unpack("<Q", struct.pack("<d", seed))[0]
            if u < m:
                u += m                      # keep the top k[i] bits non-zero
            self.gen[i] = u & M64
        for _ in range(10):
            self.step()

    def step(self) -> float:
        """One draw, as a double in [1.0, 2.0)."""
        r = 0
        for i, k, q, s in _TW223_PARAMS:
            z = self.gen[i]
            z = ((((z << q) & M64) ^ z) >> (k - s)) ^ (
                ((z & ((M64 << (64 - k)) & M64)) << s) & M64)
            r ^= z
            self.gen[i] = z
        bits = (r & 0x000FFFFFFFFFFFFF) | 0x3FF0000000000000
        return struct.unpack("<d", struct.pack("<Q", bits))[0]

    def random(self, low: float | None = None, high: float | None = None) -> float:
        d = self.step() - 1.0
        if low is None:
            return d
        if high is None:
            return math.floor(d * low) + 1.0
        return math.floor(d * (high - low + 1.0)) + low


class RunRng:
    """The pools for one run, keyed by the run's seed string."""

    def __init__(self, seed: str | int) -> None:
        self.seed = seed if isinstance(seed, str) else str(seed)
        seed = self.seed
        self.hashed_seed = pseudohash(seed)
        self.pools: dict[str, float] = {}

    def pseudoseed(self, key: str) -> float:
        """Advance `key`'s pool and return a seed for math.random."""
        state = self.pools.get(key)
        if state is None:
            state = pseudohash(key + self.seed)
        state = abs(_round13((2.134453429141 + state * 1.72431234) % 1))
        self.pools[key] = state
        return (state + self.hashed_seed) / 2

    def pseudorandom(self, key: str, low: float | None = None,
                     high: float | None = None) -> float:
        return TW223(self.pseudoseed(key)).random(low, high)

    def random_element(self, items: Sequence, key: str):
        """The game's pseudorandom_element: draw from an ordered sequence.

        Order is the caller's job, exactly as in Lua, where the table is
        sorted by sort_id (or by key) first -- an unsorted pool draws
        reproducibly from the wrong place.
        """
        index = int(TW223(self.pseudoseed(key)).random(len(items)))
        return items[index - 1]

    def chance(self, key: str, numerator: float, denominator: float) -> bool:
        """A "1 in N" roll, exactly as the game words it.

        The game writes these as

            pseudorandom(key) < G.GAME.probabilities.normal / odds

        which is a float draw compared against a ratio, not an integer draw
        from 1..N. The two agree on how often they fire and disagree on *which*
        draws fire, so a simulator using the wrong form matches the odds and
        still diverges hand by hand from the same seed. The numerator is the
        run's probability numerator, which Oops! All 6s doubles.
        """
        return self.pseudorandom(key) < numerator / denominator

    def shuffle(self, items: list, key: str = "shuffle") -> None:
        """The game's pseudoshuffle, in place.

        Note the bounds: Lua walks #list down to 2 and swaps with
        math.random(i), which is 1-based and inclusive. The caller is
        responsible for sorting by sort_id first, as CardArea does -- a deck
        in a different starting order shuffles reproducibly into a different
        deck.
        """
        rng = TW223(self.pseudoseed(key))
        for i in range(len(items), 1, -1):
            j = int(rng.random(i))
            items[i - 1], items[j - 1] = items[j - 1], items[i - 1]

    def state(self) -> dict[str, float]:
        return dict(self.pools)

    # -- bridges for the simulator's call sites ------------------------------
    #
    # These name a pool first, matching the game, so a caller that rolls more
    # often cannot shift another subsystem's draws.

    def choice(self, key: str, items: Sequence):
        return self.random_element(list(items), key)

    def randint(self, key: str, low: int, high: int) -> int:
        return int(self.pseudorandom(key, low, high))

    def sample(self, key: str, items: Iterable, count: int) -> list:
        """Draw `count` distinct items.

        Not yet matched to the engine. The game has no single "sample": each
        call site draws its own way, and shop slots in particular re-roll
        against a pool that changes as cards are taken. Faithful sampling has
        to be done per call site, so anything relying on this is unverified.
        """
        pool = list(items)
        out = []
        for _ in range(min(count, len(pool))):
            pick = self.random_element(pool, key)
            pool.remove(pick)
            out.append(pick)
        return out


def random_string(length: int, rng: TW223) -> str:
    """How the game builds a starting seed: 1-9, A-N, P-Z."""
    out = []
    for _ in range(length):
        if rng.random() > 0.7:
            out.append(chr(int(rng.random(ord("1"), ord("9")))))
        elif rng.random() > 0.45:
            out.append(chr(int(rng.random(ord("A"), ord("N")))))
        else:
            out.append(chr(int(rng.random(ord("P"), ord("Z")))))
    return "".join(out)
