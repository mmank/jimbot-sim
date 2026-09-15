"""What a Blueprint or a Brainstorm changes while a hand scores.

A copier calls the copied joker's calculate_joker with `context.blueprint`
set (card.lua:2310, 2324), so a copy does whatever the copied joker's branch
does unless that branch says `not context.blueprint`. score_hand ran every
`scored` hook for copies too, which is right for the pure effects and for
most of the ones that change state -- and wrong for the one growth that lives
in the per-card branch:

    if self.ability.name == 'Wee Joker' and
        context.other_card:get_id() == 2 and not context.blueprint then
            self.ability.extra.chips = self.ability.extra.chips + ...chip_mod

(card.lua:3083-3085). A Blueprint beside a Wee Joker grew it a second +8 for
every scoring 2, so the joker ran twice as fast as the game's.

The other way round in the before pass: Space Joker's level-up has no guard
(card.lua:3420-3426), and evaluate_play levels the hand for whichever joker
returned `level_up`, the copier included (state_events.lua:630-635). The
simulator ran Space Joker as an own-account `update`, so a copy never rolled.

The rest of the per-card and held-card branches were checked against the Lua
and left alone, since they already matched: Hiker's perma_bonus
(card.lua:3067-3069), 8 Ball (3106), Golden Ticket (3150), Business Card
(3175), Rough Gem (3224), Bloodstone (3247) and Reserved Parking (3302) have
no guard and a copy repeats them; Lucky Cat (3076) has one and is asked on its
own account only. Hiker is pinned here so the Wee Joker fix cannot take it
along.
"""

import pytest

from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import GameState
from jimbot_sim.hands import HandType, evaluate
from jimbot_sim.jokers import make
from jimbot_sim.scoring import score_hand

KEYS = {"Wee Joker": "j_wee", "Blueprint": "j_blueprint",
        "Brainstorm": "j_brainstorm", "Hanging Chad": "j_hanging_chad",
        "Space Joker": "j_space", "Hiker": "j_hiker"}
_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "7": Rank.SEVEN, "9": Rank.NINE, "K": Rank.KING}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}

TWOS = "S_2 H_2 D_2 C_5 H_7 S_9 D_3 C_4"      # play 1-3: Three of a Kind
KINGS = "S_K H_K D_2 C_5 H_7 S_9 D_3 C_4"     # play 1-2: Pair


def _cards(codes):
    return [Card(_RANKS[c[2]], _SUITS[c[0]]) for c in codes.split()]


def _score(row, codes, play, setup=None):
    cards = _cards(codes)
    played = [cards[i - 1] for i in play]
    held = [c for i, c in enumerate(cards, 1) if i not in play]
    game = GameState(seed=0)
    game.jokers = [make(name) for name in row]
    game.hand = played + held
    game.full_deck = list(game.hand)
    if setup is not None:
        setup(game)
    ctx = score_hand(game, evaluate(played), played, held)
    return game, played, ctx


def _named(game, name):
    return next(j for j in game.jokers if j.name == name)


def _space_fires(game):
    game.rng.chance = lambda key, numerator, denominator: key == "space"


WEE_ROWS = [("Wee Joker",), ("Blueprint", "Wee Joker"),
            ("Wee Joker", "Brainstorm")]


@pytest.mark.parametrize("row", WEE_ROWS, ids=" / ".join)
def test_a_copy_does_not_grow_wee_joker(row):
    """Three scoring 2s grow it by 24 whoever copies it, and each copy adds
    the grown chips in joker_main."""
    game, _, ctx = _score(row, TWOS, (1, 2, 3))
    assert _named(game, "Wee Joker").counter == 24, "\n".join(ctx.log)
    # Three of a Kind 30 x 3, three 2s, and 24 chips per joker that answers.
    assert ctx.score == (30 + 6 + 24 * len(row)) * 3, "\n".join(ctx.log)


def test_retriggers_grow_it_and_their_copies_do_not():
    """Hanging Chad repeats the first 2 twice (card.lua:3352-3358, no guard);
    each repeat is another individual pass, so another +8 -- once."""
    game, _, ctx = _score(("Hanging Chad", "Blueprint", "Wee Joker"),
                          TWOS, (1, 2, 3))
    assert _named(game, "Wee Joker").counter == 40, "\n".join(ctx.log)


@pytest.mark.parametrize("row", [("Hiker",), ("Blueprint", "Hiker"),
                                 ("Hiker", "Brainstorm")], ids=" / ".join)
def test_a_copy_of_hiker_still_adds_to_the_card(row):
    """No `not context.blueprint` on Hiker (card.lua:3067-3069): each copy
    adds its own +5 to the scoring card's perma_bonus."""
    _, played, ctx = _score(row, KINGS, (1, 2))
    assert [c.extra_chips for c in played] == [5 * len(row)] * 2, \
        "\n".join(ctx.log)


@pytest.mark.parametrize("row", [("Space Joker",), ("Blueprint", "Space Joker"),
                                 ("Space Joker", "Brainstorm")],
                         ids=" / ".join)
def test_a_copy_of_space_joker_levels_the_hand_again(row):
    """Each answer that returns level_up levels the hand (state_events.lua:
    634-635), and the base is read after the pass (640-641)."""
    game, _, ctx = _score(row, KINGS, (1, 2), _space_fires)
    level = 1 + len(row)
    assert game.hand_levels.levels[HandType.PAIR] == level, "\n".join(ctx.log)
    # Pair: 10 + 15 and 2 + 1 per level above the first, and two Kings.
    assert ctx.score == (10 + 15 * (level - 1) + 20) * (2 + (level - 1)), \
        "\n".join(ctx.log)


def test_a_preview_leaves_the_real_space_joker_level_alone():
    game = GameState(seed=0)
    game.jokers = [make("Blueprint"), make("Space Joker")]
    game.hand = _cards(KINGS)
    game.full_deck = list(game.hand)
    _space_fires(game)
    game.preview_play((0, 1))
    assert game.hand_levels.levels[HandType.PAIR] == 1


# -- measured on the engine ----------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    pytest.importorskip("lupa")
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
    if not engine_available():
        pytest.skip("no Balatro engine: need vendor/balatro_src")
    return HeadlessBalatro().boot()


def _read(engine, expr):
    return engine.eval("(function() return %s end)()" % expr)


@pytest.mark.slow
@pytest.mark.parametrize("row", WEE_ROWS + [("Hanging Chad", "Blueprint",
                                             "Wee Joker")], ids=" / ".join)
def test_wee_joker_growth_matches_the_engine(engine, row):
    from jimbot_sim.headless.scenario import Scenario

    scene = Scenario(engine).start().hand(TWOS)
    scene.jokers(" ".join(KEYS[name] for name in row))
    got = scene.play((1, 2, 3))
    chips = _read(engine, "G.jokers.cards[%d].ability.extra.chips"
                  % (row.index("Wee Joker") + 1))
    game, _, ctx = _score(row, TWOS, (1, 2, 3))
    assert _named(game, "Wee Joker").counter == chips, "\n".join(ctx.log)
    assert ctx.score == got, "\n".join(ctx.log)


@pytest.mark.slow
@pytest.mark.parametrize("row", [("Blueprint", "Hiker"),
                                 ("Hiker", "Brainstorm")], ids=" / ".join)
def test_hiker_copies_match_the_engine(engine, row):
    from jimbot_sim.headless.scenario import Scenario

    scene = Scenario(engine).start().hand(KINGS)
    scene.jokers(" ".join(KEYS[name] for name in row))
    got = scene.play((1, 2))
    perma = _read(engine, "(function() local s = 0 "
                  "for _, c in ipairs(G.playing_cards) do "
                  "s = s + (c.ability.perma_bonus or 0) end return s end)()")
    _, played, ctx = _score(row, KINGS, (1, 2))
    assert sum(c.extra_chips for c in played) == perma, "\n".join(ctx.log)
    assert ctx.score == got, "\n".join(ctx.log)


@pytest.mark.slow
@pytest.mark.parametrize("row", [("Blueprint", "Space Joker"),
                                 ("Space Joker", "Brainstorm")],
                         ids=" / ".join)
def test_space_joker_copies_match_the_engine(engine, row):
    """G.GAME.probabilities.normal at 4 makes `pseudorandom('space') <
    normal/4` certain for the joker and for its copy (card.lua:3420)."""
    from jimbot_sim.headless.scenario import Scenario

    scene = Scenario(engine).start().hand(KINGS)
    scene.jokers(" ".join(KEYS[name] for name in row))
    engine.execute("G.GAME.probabilities.normal = 4")
    try:
        got = scene.play((1, 2))
        level = int(_read(engine, 'G.GAME.hands["Pair"].level'))
    finally:
        engine.execute("G.GAME.probabilities.normal = 1")
    game, _, ctx = _score(row, KINGS, (1, 2), _space_fires)
    assert game.hand_levels.levels[HandType.PAIR] == level, "\n".join(ctx.log)
    assert ctx.score == got, "\n".join(ctx.log)
