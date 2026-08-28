"""Boot Balatro's own Lua under LuaJIT, with no LOVE and no window.

This is the fidelity-first path: rather than reimplementing 150 jokers and
hoping the maths matches, we run the game's actual code. `Balatro.exe` is a
LOVE2D fused binary -- the LOVE runtime with the game's Lua appended as a zip --
so the Lua extracts cleanly and, since the logic only reaches the outside world
through the global `love` table, a stubbed `love` is enough to run it headless.

Nothing here modifies the installed game; the source is extracted to a working
directory and read from there.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from lupa import luajit21

DEFAULT_INSTALL = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Balatro\Balatro.exe")
DEFAULT_SOURCE = Path("vendor/balatro_src")


def engine_available(source: Path = DEFAULT_SOURCE,
                     exe: Path = DEFAULT_INSTALL) -> bool:
    """Whether the engine can run here.

    Either an already-extracted tree or a game to extract from will do. The
    extracted Lua is platform-independent, so a Linux training box needs only
    the tree copied across -- it never needs the Windows binary.
    """
    return (Path(source).exists() and any(Path(source).glob("*.lua")))         or Path(exe).exists()

# main.lua's require order, which the game depends on: globals.lua creates the
# global `G`, and button_callbacks.lua then hangs G.FUNCS off it.
REQUIRE_ORDER = (
    "engine/object", "bit", "engine/string_packer", "engine/controller",
    "back", "tag", "engine/event", "engine/node", "engine/moveable",
    "engine/sprite", "engine/animatedsprite", "functions/misc_functions",
    "game", "globals", "engine/ui", "functions/UI_definitions",
    "functions/state_events", "functions/common_events",
    "functions/button_callbacks", "functions/test_functions", "card",
    "cardarea", "blind", "card_character", "engine/particles", "engine/text",
    "challenges",
)

# Lua and data only. Sprite atlases and audio are never read headless, and the
# CJK fonts are 63 MB of the archive -- but the game does check that its font
# file exists before registering it, so the 35 KB English face comes along.
EXTRACT_SUFFIXES = (".lua", ".txt", ".json", ".fs")
EXTRACT_EXTRA = ("resources/fonts/m6x11plus.ttf",)


class ExtractionError(RuntimeError):
    pass


def extract_source(exe: Path = DEFAULT_INSTALL, dest: Path = Path("vendor/balatro_src"),
                   force: bool = False) -> Path:
    """Unpack the Lua source from the fused LOVE executable."""
    dest = Path(dest)
    if dest.exists() and any(dest.glob("*.lua")) and not force:
        return dest
    if not Path(exe).exists():
        raise ExtractionError(f"Balatro not found at {exe}")
    try:
        archive = zipfile.ZipFile(exe)
    except zipfile.BadZipFile as exc:
        raise ExtractionError(
            f"{exe} is not a fused LOVE binary; cannot read the appended archive"
        ) from exc
    dest.mkdir(parents=True, exist_ok=True)
    for name in archive.namelist():
        if name.endswith(EXTRACT_SUFFIXES) or name in EXTRACT_EXTRA:
            archive.extract(name, dest)
    if not (dest / "main.lua").exists():
        raise ExtractionError(f"no main.lua in {exe}; unexpected archive layout")
    return dest


class HeadlessBalatro:
    """A live Balatro game running in-process, with rendering stubbed out."""

    def __init__(self, source: Path | None = None, exe: Path = DEFAULT_INSTALL,
                 stub_dir: Path | None = None, game_speed: int = 64,
                 width: int = 1280, height: int = 720,
                 unlock_all: bool = True) -> None:
        self.game_speed = game_speed
        # A fresh profile locks 45 jokers; training on that is training on a
        # different game. Off only if you deliberately want a new-player pool.
        self.unlock_all = unlock_all
        self.width = width
        self.height = height
        self.source = Path(source) if source else extract_source(exe)
        self.stub_dir = Path(stub_dir or Path(__file__).parent)
        self.lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        self._booted = False

    # ------------------------------------------------------------------

    def _lua_path(self, path: Path) -> str:
        return str(Path(path).resolve()).replace("\\", "/")

    def boot(self) -> "HeadlessBalatro":
        """Install the love stub, load every game module, run start_up()."""
        src = self._lua_path(self.source)
        stub = self._lua_path(self.stub_dir)
        # The mod the real game runs is on the path too. bot_api.lua touches
        # neither love nor luasocket, so it loads here unchanged -- and loading
        # the same file is the point: when the two had separate action tables
        # they drifted, and headless silently lacked actions the game offers
        # (the shop's buy-and-use button, for one).
        mod = self._lua_path(
            Path(__file__).resolve().parents[1] / "balatro_bridge" / "mod")
        self.lua.execute(f'''
            package.path = "{src}/?.lua;{stub}/?.lua;{mod}/?.lua;" .. package.path
            LOVE_STUB = require("love_stub")
            love = LOVE_STUB.install({{ root = "{src}" }})
        ''')
        for module in REQUIRE_ORDER:
            self.lua.execute(f'require("{module}")')
        self.lua.execute('HEADLESS = require("headless_patch")')
        self.lua.execute("HEADLESS.pre_boot()")
        self.lua.execute("G:start_up()")
        self.lua.execute(f"HEADLESS.apply({{width={self.width}, height={self.height}}})"
                         ".flush_events().override_ui_functions().fast_forward(%d)" % self.game_speed)
        if self.unlock_all:
            self.lua.execute("HEADLESS.unlock_all()")
        self.lua.execute('api = require("headless_api")')
        self.lua.execute('BOT = require("bot_api")')
        self._booted = True
        return self

    @property
    def G(self):
        """The game's global state table."""
        return self.lua.globals().G

    def eval(self, expression: str) -> Any:
        return self.lua.eval(expression)

    def execute(self, chunk: str) -> Any:
        return self.lua.execute(chunk)
