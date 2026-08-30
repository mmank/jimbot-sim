"""The Python RNG must agree with the real engine draw for draw.

Comparisons here are on exact doubles, never on printed values: any decent
generator agrees with LuaJIT's to fourteen digits, so a rounded print cannot
tell replication from coincidence. Lua's "%a" prints a double exactly.
"""

import pytest

from balatro.rng import TW223, RunRng, pseudohash
from balatro_headless.runtime import HeadlessBalatro

KEYS = ["Joker1", "shop_pack", "Tarot", "erratic", "front", "cry_e", "stdset1"]
SEEDS = ["TESTSEED", "ABCD1234", "7EVEN", "XYZZY"]


@pytest.fixture(scope="module")
def lua():
    game = HeadlessBalatro().boot()
    return lambda body: game.eval("(function() %s end)()" % body)


def test_pseudohash_matches(lua):
    for key in KEYS + SEEDS:
        assert pseudohash(key) == float(lua(f'return pseudohash("{key}")'))


def test_pool_state_advances_identically(lua):
    """Fifty advances deep: drift would compound, so depth is the test."""
    for seed in SEEDS:
        rng = RunRng(seed)
        for step in range(1, 51):
            rng.pseudoseed("Joker1")
            expected = float(lua(f'''
                local s = pseudohash("Joker1".."{seed}")
                for i = 1, {step} do
                    s = math.abs(tonumber(string.format("%.13f",
                        (2.134453429141 + s*1.72431234) % 1)))
                end
                return s'''))
            assert rng.pools["Joker1"] == expected, f"{seed} step {step}"


def test_tw223_reproduces_math_random_bit_for_bit(lua):
    for seed in (0.5, 0.001, 0.86389516317876769, 0.999999):
        expected = lua(
            f'math.randomseed({seed!r}) local t = {{}} '
            f'for i = 1, 100 do t[i] = string.format("%a", math.random()) end '
            f'return table.concat(t, " ")').split()
        rng = TW223(seed)
        for i, want in enumerate(expected):
            assert rng.random() == float.fromhex(want), f"seed {seed} draw {i}"


@pytest.mark.parametrize("low, high", [(1, 5), (1, 52), (1, 150), (3, 9)])
def test_integer_draws_match(lua, low, high):
    for key in KEYS:
        seed = float(lua(f'return pseudohash("{key}".."TESTSEED")'))
        expected = [int(x) for x in lua(
            f'math.randomseed({seed!r}) local t = {{}} '
            f'for i = 1, 40 do t[i] = math.random({low},{high}) end '
            f'return table.concat(t, ",")').split(",")]
        rng = TW223(seed)
        assert [int(rng.random(low, high)) for _ in expected] == expected


def test_pseudorandom_end_to_end(lua):
    """The whole path the game takes: named pool -> seed -> bounded draw."""
    for seed in SEEDS:
        rng = RunRng(seed)
        lua(f'G.GAME.pseudorandom = {{seed = "{seed}"}}; '
            f'G.GAME.pseudorandom.hashed_seed = pseudohash("{seed}")')
        for _ in range(20):
            for key in KEYS:
                assert rng.pseudorandom(key, 1, 100) == float(
                    lua(f'return pseudorandom("{key}", 1, 100)'))


def test_shuffle_matches_pseudoshuffle(lua):
    """A deck order is 52 draws of compounding error if the bounds are wrong."""
    for seed in SEEDS:
        rng = RunRng(seed)
        deck = list(range(1, 53))
        rng.shuffle(deck, "nr")
        expected = [int(x) for x in lua(f'''
            G.GAME.pseudorandom = {{seed = "{seed}"}}
            G.GAME.pseudorandom.hashed_seed = pseudohash("{seed}")
            local t = {{}} for i = 1, 52 do t[i] = {{sort_id = i, n = i}} end
            pseudoshuffle(t, pseudoseed("nr"))
            local out = {{}} for i = 1, 52 do out[i] = t[i].n end
            return table.concat(out, ",")''').split(",")]
        assert deck == expected
