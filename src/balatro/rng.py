"""Seeded, stream-separated randomness.

Every random decision draws from a named stream so that changing how often one
subsystem rolls (say, shop generation) does not shift the rolls of another.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class RunRng:
    seed: int
    _streams: dict[str, random.Random] = field(default_factory=dict, repr=False)

    def stream(self, name: str) -> random.Random:
        rng = self._streams.get(name)
        if rng is None:
            rng = random.Random(f"{self.seed}:{name}")
            self._streams[name] = rng
        return rng

    def chance(self, name: str, numerator: int, denominator: int) -> bool:
        """Balatro-style '1 in N' rolls (Oops! All 6s scales the numerator)."""
        return self.stream(name).random() < numerator / denominator

    def choice(self, name: str, seq):
        return self.stream(name).choice(seq)

    def sample(self, name: str, seq, k: int):
        return self.stream(name).sample(list(seq), k)

    def shuffle(self, name: str, seq: list) -> None:
        self.stream(name).shuffle(seq)

    def randint(self, name: str, a: int, b: int) -> int:
        return self.stream(name).randint(a, b)
