"""Build the native pieces. Works on the Windows laptop and the Linux box.

    python native/build.py

A script rather than CMake because there is one translation unit and two
platforms, and CMake would be more machinery than the thing it builds.

The flags are not decoration:

    -ffp-contract=off   stops the compiler folding `a*b + c` into an FMA,
                        which rounds once instead of twice. pseudohash does
                        exactly that per character, and the whole point is to
                        match Python bit for bit.
    -fno-fast-math      the same argument, defensively.
    -static-libstdc++   MinGW links against libstdc++-6.dll and friends,
    -static-libgcc      which are not on the loader's path when ctypes opens
                        the library from Python. Without these it builds fine
                        and fails at import.
"""

from __future__ import annotations

import subprocess
import sys
import sysconfig
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOWS = sys.platform == "win32"
LIBRARY = HERE / ("balatro_rng.dll" if WINDOWS else "libbalatro_rng.so")

FLAGS = ["-O2", "-ffp-contract=off", "-fno-fast-math", "-shared", "-fPIC",
         "-std=c++17"]
if WINDOWS:
    FLAGS += ["-static", "-static-libgcc", "-static-libstdc++"]


def main() -> int:
    compiler = "g++"
    command = [compiler] + FLAGS + ["-o", str(LIBRARY), str(HERE / "rng.cpp")]
    print(" ".join(command))
    result = subprocess.run(command)
    if result.returncode:
        return result.returncode
    size = LIBRARY.stat().st_size
    print("built %s (%.0f KB)" % (LIBRARY.name, size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
