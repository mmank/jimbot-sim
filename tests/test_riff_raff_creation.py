"""Riff-Raff's jokers come from Riff-Raff's own streams, edition included.

The game (card.lua:2529-2543) makes them with

    create_card('Joker', G.jokers, nil, 0, nil, nil, nil, 'rif')

and create_card (functions/common_events.lua:2082) does three things with
that call that the simulator did not:

* The key_append 'rif' names the pool. get_current_pool builds the key as
  'Joker'..rarity..'rif'..ante (common_events.lua:1971, 2052), so an ante-one
  Riff-Raff draws from "Joker1rif1". The simulator passed no append at all
  and drew from "Joker11" -- a real stream with plausible commons in it, so
  every Riff-Raff made two believable jokers and nearly always the wrong two.
  The forced `_rarity` of 0 is still no rarity roll: Lua's 0 is truthy, and
  0 is below both thresholds, so it comes out Common (1969-1970).

* A joker made into G.jokers polls an edition,
  poll_edition('edi'..key_append..ante) (common_events.lua:2149), under the
  run's edition rate. add_random_joker never polled one, so the Polychrome Red
  Card the game handed seed S85SICBL came out plain -- and the same was true
  of Judgement ("edijud"), The Soul ("edisou"), Wraith ("ediwra") and the
  Top-up Tag ("editop"), which all create through the same function.

* How many it makes is decided when the blind is selected, not as each card
  arrives: jokers_to_create = min(2, card_limit - (#jokers + joker_buffer))
  (card.lua:2530). A Negative first joker raises the limit when it is added,
  but the count is already fixed, so one free slot is one joker whatever the
  first one rolled.

The expected rows are the game's own, read off the headless smoke test at the
first blind selected after buying Riff-Raff.
"""

import pytest

from jimbot_sim import shop_pool
from jimbot_sim.cards import Edition
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY, JokerInstance


def _row(seed, deck, stake, keys):
    game = GameState(seed=seed, deck=deck, stake=stake)
    for key in keys:
        game.gain_joker(JokerInstance(REGISTRY[shop_pool.NAME_BY_JOKER_KEY[key]]))
    return game


def _select_blind(game):
    """Only Riff-Raff's hook: the rows below hold nothing else that reacts."""
    for joker in list(game.jokers):
        if joker.name == "Riff-Raff":
            joker.spec.on_blind_select(joker, game)


def _keys(game):
    return [shop_pool.KEY_BY_JOKER_NAME[j.name] for j in game.jokers]


# (seed, deck, stake, the row when the blind was selected, what the game made)
GAME_ROWS = [
    ("S85SICBL", "Magic Deck", 5, ["j_riff_raff"],
     [("j_droll", Edition.NONE), ("j_red_card", Edition.POLYCHROME)]),
    ("IGS6H949", "Checkered Deck", 1, ["j_banner", "j_riff_raff"],
     [("j_juggler", Edition.NONE), ("j_chaos", Edition.NONE)]),
    ("WJ09CGHG", "Yellow Deck", 8, ["j_ice_cream", "j_riff_raff"],
     [("j_sly", Edition.NONE), ("j_scary_face", Edition.NONE)]),
    ("U2EBFAQ2", "Painted Deck", 5, ["j_riff_raff"],
     [("j_business", Edition.NONE), ("j_reserved_parking", Edition.NONE)]),
    ("08F809NE", "Red Deck", 7, ["j_riff_raff"],
     [("j_gluttenous_joker", Edition.NONE), ("j_popcorn", Edition.NONE)]),
    ("LC4JWH61", "Nebula Deck", 7, ["j_gluttenous_joker", "j_riff_raff"],
     [("j_mad", Edition.NONE), ("j_faceless", Edition.NONE)]),
]


@pytest.mark.parametrize("seed,deck,stake,row,made", GAME_ROWS,
                         ids=[r[0] for r in GAME_ROWS])
def test_riff_raff_makes_the_jokers_the_game_makes(seed, deck, stake, row,
                                                   made):
    game = _row(seed, deck, stake, row)
    _select_blind(game)
    assert _keys(game) == row + [key for key, _ in made]
    assert [j.edition for j in game.jokers[len(row):]] == [e for _, e in made]


def test_riff_raff_count_is_fixed_before_a_negative_arrives(monkeypatch):
    """One free slot, a Negative first joker: still one joker (card.lua:2530)."""
    game = _row("RIFFNEG1", "Red Deck", 1,
                ["j_riff_raff", "j_joker", "j_banner", "j_juggler"])
    assert game.joker_slots - len(game.jokers) == 1
    monkeypatch.setattr(shop_pool, "poll_edition",
                        lambda rng, key, **kw: "negative")
    _select_blind(game)
    assert len(game.jokers) == 5
    assert game.jokers[-1].edition is Edition.NEGATIVE


@pytest.mark.parametrize("append", ["rif", "jud", "sou", "wra", "top"])
def test_a_created_joker_polls_its_edition_under_its_append(monkeypatch,
                                                            append):
    """poll_edition('edi'..key_append..ante), common_events.lua:2149."""
    game = GameState(seed="EDIPOLL1", deck="Red Deck")
    game.ante = 3
    polled = []

    def record(rng, key, **kw):
        polled.append((key, kw.get("edition_rate", 1.0)))
        return "none"

    monkeypatch.setattr(shop_pool, "poll_edition", record)
    game.add_random_joker("test", append=append, legendary=(append == "sou"))
    assert polled == [("edi%s3" % append, game.edition_rate)]
