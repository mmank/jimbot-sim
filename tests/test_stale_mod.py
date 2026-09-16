"""A modded build whose baked-in mod is not the checkout's is refused.

The mod is written into the executable when it is built and never read from
the source tree again. ICEMAN18 stopped on Invisible Joker's rounds -- game 0,
shadow 1 -- because the build predated the export of `invis_rounds` by a week.
"""

import zipfile

import pytest

from jimbot_sim.bridge import BridgeError, require_current_mod, stale_mod
from jimbot_sim.bridge.client import MOD_DIR


def _build(tmp_path, change=None, drop=None):
    """A stand-in for the fused exe: a zip holding the mod files."""
    exe = tmp_path / "BalatroBot.exe"
    with zipfile.ZipFile(exe, "w") as archive:
        archive.writestr("main.lua", "-- the game")
        for path in sorted(MOD_DIR.glob("*.lua")):
            if path.name == drop:
                continue
            text = path.read_text(encoding="utf-8")
            if path.name == change:
                text += "\n-- an older build\n"
            archive.writestr(path.name, text)
    return exe


def test_a_build_of_this_mod_is_current(tmp_path):
    exe = _build(tmp_path)
    assert stale_mod(exe) == []
    require_current_mod(exe)


def test_a_changed_file_is_named(tmp_path):
    exe = _build(tmp_path, change="bot_api.lua")
    assert stale_mod(exe) == ["bot_api.lua"]
    with pytest.raises(BridgeError, match="bot_api.lua"):
        require_current_mod(exe)


def test_a_missing_file_is_named(tmp_path):
    exe = _build(tmp_path, drop="bot_server.lua")
    assert stale_mod(exe) == ["bot_server.lua"]


def test_no_build_is_not_stale(tmp_path):
    assert stale_mod(tmp_path / "absent.exe") == []
