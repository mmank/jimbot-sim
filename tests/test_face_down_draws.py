"""Face-down draws, against the game's own Lua.

The House, The Wheel, The Mark and The Fish deal cards face down, and Amber
Acorn turns the joker row over. No rule reads it -- a face-down card scores as
itself -- but The Wheel's roll is a draw on a pool of its own (`wheel`), one
per card dealt, and a policy that plays fair must know what it cannot see.

`data/facedown.txt` was recorded off the real game, booted headless by
rumbot's `scripts/gen_facedown_fixture.py`, and is the same file the Rust
simulator (rumbot-sim's `tests/facedown_fixture.rs`) is checked against: a
face-down boss on the opening blind, a fixed script of discards and plays, and
after every step the hand in order with the face-down cards starred, the joker
row the same way, and the `wheel` pool's state.

The rows cover Pareidolia under The Mark (every card is a face card), Oops!
All 6s under The Wheel (one and two of them: 2 in 7, 4 in 7), Chicot (nothing
turns over and the Wheel never rolls), and Amber Acorn on rows of one, two and
five -- and beside Chicot, which leaves the row face up but shuffled all the
same.
"""

from pathlib import Path

import pytest

from jimbot_sim.blinds import BOSSES, FINISHER_BOSSES, BlindKind, make_blind
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

FIXTURE = Path(__file__).parent / "data" / "facedown.txt"
BY_NAME = {b.name: b for b in BOSSES + FINISHER_BOSSES}
JOKER_NAMES = {
    "j_pareidolia": "Pareidolia",
    "j_oops": "Oops! All 6s",
    "j_chicot": "Chicot",
    "j_joker": "Joker",
    "j_greedy_joker": "Greedy Joker",
    "j_lusty_joker": "Lusty Joker",
    "j_wrathful_joker": "Wrathful Joker",
    "j_gluttenous_joker": "Gluttonous Joker",
}


def _cases():
    cases = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if line.startswith("case "):
            cases.append((line[5:], []))
        elif line.startswith("step "):
            cases[-1][1].append(line[5:])
        else:
            raise ValueError(f"unreadable fixture line: {line}")
    return cases


CASES = _cases()


def _start(header: str) -> GameState:
    seed, deck, stake, _key, boss, jokers = header.split(" ")
    game = GameState(seed=seed, deck=deck.replace("_", " "), stake=int(stake))
    if jokers != "-":
        for key in jokers.split(","):
            game.jokers.append(JokerInstance(JOKERS[JOKER_NAMES[key]]))
    # The harness puts the boss on the opening blind, out of reach.
    blind = make_blind(
        BlindKind.BOSS,
        1,
        BY_NAME[boss.replace("_", " ")],
        game.deck_config.get("ante_scaling", 1),
        game.blind_scaling,
    )
    blind.target = 999_999_999
    blind.on_deck = True
    game.blind = blind
    game.step(Action(ActionType.SELECT_BLIND))
    return game


def _action(text: str) -> Action:
    kind, picks = text.split(" ", 1)
    cards = tuple(int(p) - 1 for p in picks.split(","))
    return Action(
        {"discard": ActionType.DISCARD, "play": ActionType.PLAY}[kind], cards=cards
    )


def _observe(game: GameState) -> tuple:
    hand = " ".join(
        f"{c.suit.value}{c.rank.short}{'*' if c.face_down else ''}" for c in game.hand
    )
    jokers = (
        " ".join(
            f"{j.name.replace(' ', '_')}{'*' if j.face_down else ''}"
            for j in game.jokers
        )
        or "-"
    )
    return hand, jokers, game.rng.state().get("wheel")


def _expected(recorded: str) -> tuple:
    hand, jokers, wheel = (part.strip() for part in recorded.split(" | ", 2))
    return hand, jokers, None if wheel == "-" else float(wheel)


def test_the_fixture_is_whole():
    # The fixture's own totals: 87 cases, and the recording does turn cards
    # over.
    assert len(CASES) == 87
    assert any("*" in step for _, steps in CASES for step in steps)


@pytest.mark.parametrize("header,steps", CASES, ids=[h for h, _ in CASES])
def test_face_down_draws_match_the_game(header, steps):
    game = _start(header)
    for step in steps:
        what, recorded = step.split(" | ", 1)
        if what != "deal":
            action = _action(what)
            assert game.is_legal(action), f"{what} is not legal here"
            game.step(action)
        assert _observe(game) == _expected(recorded), f"after {what}"


def test_no_boss_is_blank():
    """Every boss now does something the engine models: the four face-down
    ones mark what they deal, and nothing is left blank for want of a rule."""
    blank = {
        b.name
        for b in BOSSES + FINISHER_BOSSES
        if b.chip_mult == 2.0
        and not any(
            getattr(b, f.name)
            for f in b.__dataclass_fields__.values()
            if f.name not in ("name", "text", "chip_mult", "is_finisher")
        )
    }
    assert blank == set()
