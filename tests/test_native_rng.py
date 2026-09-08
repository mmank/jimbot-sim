"""The native RNG must agree with the Python one bit for bit.

Not "to within a tolerance". The two layers of Balatro's randomness compound
-- pseudohash feeds pseudoseed feeds math.randomseed feeds a Tausworthe
generator -- so a difference in the last bit of a hash becomes a different
joker in the shop three calls later, and then a different run. Comparing
printed values would pass on any generator that is merely close; these compare
the raw doubles.

Two failures are specifically anticipated, because they are compiler
behaviour rather than logic, and both would show up here first:

  pseudohash computes `(a / num) * byte * pi + pi * i` per character. A
  compiler may contract the multiply-add into an FMA, rounding once where
  Python rounds twice. native/build.py passes -ffp-contract=off; this is what
  proves it took.

  round13 is Lua's string.format("%.13f"), so it is printf's rounding. glibc
  rounds correctly and MinGW historically routed %f through MSVCRT, which did
  not.

Skipped when the library has not been built -- it is compiled by hand with
python native/build.py, not installed.
"""

import random
import struct

import pytest

from jimbot_sim import native, rng

pytestmark = pytest.mark.skipif(not native.available(),
                                reason="native/ has not been built")

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"


def bits(value: float) -> int:
    """The double's raw bits, so 'equal' means equal and not 'close'."""
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def test_pseudohash_matches_on_the_pools_the_game_names():
    """The literal keys first: these are the ones a divergence would ruin."""
    for key in ("Joker1", "Joker2", "shop_pack", "erratic", "cry_e", "Voucher",
                "Tarot1", "anc1", "lucky_money", "wheel_of_fortune", "boss",
                "Joker4Buffoon1", "stake_shop_joker_eternal", "seal", "front"):
        assert bits(native.pseudohash(key)) == bits(rng.pseudohash(key)), key


def test_pseudohash_matches_on_pool_names_joined_to_seeds():
    """What pseudoseed actually hashes: the key with the run's seed appended."""
    rand = random.Random(0)
    for _ in range(3000):
        key = "".join(rand.choice(ALPHABET) for _ in range(rand.randint(1, 12)))
        seed = "".join(rand.choice(ALPHABET) for _ in range(8))
        text = key + seed
        assert bits(native.pseudohash(text)) == bits(rng.pseudohash(text)), text


def test_round13_matches():
    rand = random.Random(1)
    values = [rand.random() for _ in range(5000)]
    values += [0.0, 1.0, 0.5, 1e-14, 0.99999999999995, 0.12345678901235]
    for value in values:
        assert bits(native.round13(value)) == bits(rng._round13(value)), value


def test_the_pool_step_matches():
    """pseudoseed's advance, which every named draw goes through."""
    rand = random.Random(2)
    for _ in range(3000):
        state = rand.random()
        expected = abs(rng._round13((2.134453429141 + state * 1.72431234) % 1))
        assert bits(native.pool_step(state)) == bits(expected), state


def test_the_generator_matches_draw_for_draw():
    rand = random.Random(3)
    for _ in range(120):
        seed = rand.random()
        mine, theirs = native.TW223(seed), rng.TW223(seed)
        for draw in range(40):
            assert bits(mine.step()) == bits(theirs.step()), (seed, draw)


def test_the_draw_forms_match():
    """random(), random(n) and random(a, b) -- the three shapes Lua offers."""
    rand = random.Random(4)
    for _ in range(60):
        seed = rand.random()
        mine, theirs = native.TW223(seed), rng.TW223(seed)
        assert bits(mine.random()) == bits(theirs.random())
        assert bits(mine.random(52)) == bits(theirs.random(52))
        assert bits(mine.random(1, 6)) == bits(theirs.random(1, 6))
