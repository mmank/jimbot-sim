"""Pull the game's own Lua source out of the Steam install.

Run from the superproject root, alongside `build_modded_game.py`'s own
convention of a CWD-relative default that means "the project this submodule
sits under":

    cd external/jimbot-sim
    python scripts/extract_lua_source.py --out ../../vendor/balatro_src

Balatro.exe is a fused LOVE2D binary: a love.exe stub followed by the game's
files as a zip (see `build_modded_game.py`, which reads the same archive to
build the modded copy). This is the other half of reading it: not rebuilding
an exe, just unpacking the archive's code into `vendor/balatro_src`,
gitignored, about 4 MB.

Filtered to `.lua` and `.fs` (the code and its shaders) plus the two files
the headless engine reaches for even with nothing on screen -- the pixel
font it draws with and SDL's controller database -- rather than the whole
archive, textures and sound included, which is the other ~85 MB of it and
nothing here reads.

Nothing here needs it to *play* the live game over the bridge; that never
reads this tree. It exists as the Lua ground truth to check the Rust and
Python simulators against, and for the headless LuaJIT engine in this
package, which loads it directly to run the real game logic without a
window.

The Steam install itself is only ever read.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

DEFAULT_INSTALL = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Balatro")
DEFAULT_OUT = Path("vendor/balatro_src")

CODE_SUFFIXES = (".lua", ".fs")
# Reached for even with nothing on screen; see the module docstring.
ALSO = ("resources/fonts/m6x11plus.ttf", "resources/gamecontrollerdb.txt")


def extract(install: Path, out: Path, force: bool = False) -> int:
    source_exe = install / "Balatro.exe"
    if not source_exe.exists():
        raise SystemExit(f"Balatro.exe not found at {source_exe}\n"
                         "pass --install if Steam put it somewhere else")
    if out.exists() and any(out.iterdir()) and not force:
        raise SystemExit(f"{out} already exists and is not empty; "
                         "pass --force to overwrite")

    out.mkdir(parents=True, exist_ok=True)
    # zipfile finds the archive's central directory from the end of the
    # file, so the love.exe stub in front of it is no obstacle -- no need to
    # locate the offset the way build_modded_game.py does to *rebuild* one.
    count = 0
    with zipfile.ZipFile(source_exe) as archive:
        for entry in archive.infolist():
            if entry.filename.endswith(CODE_SUFFIXES) or entry.filename in ALSO:
                archive.extract(entry, out)
                count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", type=Path, default=DEFAULT_INSTALL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    count = extract(args.install, args.out, args.force)
    print(f"extracted {count} files from {args.install / 'Balatro.exe'}")
    print(f"  to {args.out}")
    print(f"\nthe Steam install was not modified")


if __name__ == "__main__":
    main()
