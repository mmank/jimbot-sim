"""Load the native RNG, if it has been built.

Everything here is optional. The Python implementation in rng.py stays the
reference -- it is the one the engine was verified against -- and this is a
faster copy that must agree with it bit for bit. tests/test_native_rng.py is
what enforces that; if it ever fails, the native library is wrong, not rng.py.

Built by hand rather than installed, so absence is normal and the caller
should check `available()` rather than catch an ImportError.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_LIBRARY = _ROOT / "native" / (
    "balatro_rng.dll" if sys.platform == "win32" else "libbalatro_rng.so")

_SIGNATURES = {
    "balatro_pseudohash": (ctypes.c_double, [ctypes.c_char_p, ctypes.c_int]),
    "balatro_round13": (ctypes.c_double, [ctypes.c_double]),
    "balatro_pool_step": (ctypes.c_double, [ctypes.c_double]),
    "balatro_tw223_new": (ctypes.c_void_p, [ctypes.c_double]),
    "balatro_tw223_free": (None, [ctypes.c_void_p]),
    "balatro_tw223_step": (ctypes.c_double, [ctypes.c_void_p]),
    "balatro_tw223_draw": (ctypes.c_double, [ctypes.c_void_p, ctypes.c_double,
                                             ctypes.c_double, ctypes.c_int]),
    "balatro_bench_tw223": (ctypes.c_double, [ctypes.c_void_p, ctypes.c_int]),
    "balatro_bench_pseudohash": (ctypes.c_double, [ctypes.c_char_p,
                                                   ctypes.c_int, ctypes.c_int]),
}

_LIB = None


def available() -> bool:
    return load() is not None


def load():
    """The loaded library, or None when it has not been built."""
    global _LIB
    if _LIB is None:
        if not _LIBRARY.exists():
            return None
        lib = ctypes.CDLL(str(_LIBRARY))
        for name, (restype, argtypes) in _SIGNATURES.items():
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = argtypes
        _LIB = lib
    return _LIB


def pseudohash(text: str) -> float:
    data = text.encode("latin-1", "replace")
    return load().balatro_pseudohash(data, len(data))


def round13(value: float) -> float:
    return load().balatro_round13(value)


def pool_step(state: float) -> float:
    """One pseudoseed pool advance: abs(round13((2.134... + s*1.724...) % 1))."""
    return load().balatro_pool_step(state)


class TW223:
    """LuaJIT's math.random, native. Same draws as rng.TW223, bit for bit."""

    __slots__ = ("_handle",)

    def __init__(self, seed: float) -> None:
        self._handle = load().balatro_tw223_new(seed)

    def step(self) -> float:
        return load().balatro_tw223_step(self._handle)

    def random(self, low: float | None = None,
               high: float | None = None) -> float:
        if low is None:
            return load().balatro_tw223_draw(self._handle, 0.0, 0.0, 0)
        if high is None:
            return load().balatro_tw223_draw(self._handle, low, 0.0, 1)
        return load().balatro_tw223_draw(self._handle, low, high, 2)

    def __del__(self) -> None:
        handle, self._handle = getattr(self, "_handle", None), None
        if handle is not None and _LIB is not None:
            _LIB.balatro_tw223_free(handle)
