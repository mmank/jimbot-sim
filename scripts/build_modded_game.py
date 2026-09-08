"""Build a modified copy of Balatro that a bot can drive, with the UI visible.

Balatro.exe is a fused LOVE2D binary: a love.exe stub followed by the game's
files as a zip. "Fusing" is just concatenation, so a modified build is the same
stub followed by a rebuilt archive.

Nothing in the Steam install is read-write: the original is only read, and the
result is written into this project. Delete the output directory to undo.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

DEFAULT_INSTALL = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Balatro")

# Everything the game loads at runtime besides its own archive.
RUNTIME_FILES = (
    "OpenAL32.dll", "SDL2.dll", "https.dll", "love.dll", "lua51.dll",
    "luasteam.dll", "mpg123.dll", "msvcp120.dll", "msvcr120.dll",
    "steam_api64.dll", "steam_appid.txt",
)

# Appended to main.lua. It runs after main.lua has defined love.update, which
# is what bot_server wraps, and is wrapped in pcall so a failure in the mod
# leaves a playable game rather than a black window.
INJECTION = """

-- ---- injected by scripts/build_modded_game.py ----
local ok, err = pcall(function()
  require("bot_server").install()
end)
if not ok then
  print("bot_server failed to start: " .. tostring(err))
end
"""


class BuildError(RuntimeError):
    pass


def archive_offset(exe: Path) -> int:
    """Where the zip begins, i.e. the length of the love.exe stub."""
    with zipfile.ZipFile(exe) as archive:
        entries = archive.infolist()
        if not entries:
            raise BuildError(f"{exe} contains no archive entries")
        offset = min(entry.header_offset for entry in entries)
    with open(exe, "rb") as handle:
        handle.seek(offset)
        if handle.read(4) != b"PK\x03\x04":
            raise BuildError(
                f"no local file header at offset {offset}; {exe} is not a "
                "fused LOVE binary in the expected layout")
    return offset


def build(install: Path, out: Path, mod_dir: Path, force: bool = False) -> Path:
    source_exe = install / "Balatro.exe"
    if not source_exe.exists():
        raise BuildError(f"Balatro.exe not found at {source_exe}")
    if out.exists() and not force:
        raise BuildError(f"{out} already exists; pass --force to rebuild")

    out.mkdir(parents=True, exist_ok=True)
    offset = archive_offset(source_exe)
    stub = source_exe.read_bytes()[:offset]

    target_exe = out / "BalatroBot.exe"
    mods = sorted(p for p in mod_dir.glob("*.lua"))
    if not mods:
        raise BuildError(f"no .lua files to inject in {mod_dir}")

    injected_main = False
    with open(target_exe, "wb") as handle:
        handle.write(stub)
        # Rebuild rather than append: main.lua has to be replaced, and a zip
        # entry cannot be edited in place.
        with zipfile.ZipFile(source_exe) as src, \
                zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as dst:
            for entry in src.infolist():
                data = src.read(entry.filename)
                if entry.filename == "main.lua":
                    data = data.decode("utf-8") + INJECTION
                    data = data.encode("utf-8")
                    injected_main = True
                # Store already-compressed media uncompressed-ish as-is; the
                # default here keeps the build simple and the size similar.
                dst.writestr(entry.filename, data)
            for mod in mods:
                dst.writestr(mod.name, mod.read_text(encoding="utf-8"))

    if not injected_main:
        raise BuildError("main.lua not found in the archive; nothing injected")

    for name in RUNTIME_FILES:
        source = install / name
        if source.exists():
            shutil.copy2(source, out / name)

    return target_exe


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", type=Path, default=DEFAULT_INSTALL)
    parser.add_argument("--out", type=Path, default=Path("vendor/modded_game"))
    parser.add_argument("--mods", type=Path,
                        default=Path("src/jimbot_sim/bridge/mod"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        exe = build(args.install, args.out, args.mods, args.force)
    except BuildError as error:
        print(f"build failed: {error}", file=sys.stderr)
        raise SystemExit(1)

    size = exe.stat().st_size
    print(f"built {exe} ({size / 1e6:.0f} MB)")
    print(f"injected: {', '.join(p.name for p in sorted(args.mods.glob('*.lua')))}")
    print(f"\nthe Steam install at {args.install} was not modified")


if __name__ == "__main__":
    main()
