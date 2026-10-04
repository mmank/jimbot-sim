"""Two rules the Rust port (rumbot-sim) settled first and this one took back.

A negative stake is its positive twin with every sticker on: -n plays stake
n's chips, discards and Small Blind reward, and rolls eternal, perishable and
rental as Gold does. The bot mod reads the same convention (bot_api.lua), so a
run set up at -3 is one run on both. There is no -8: Gold already rolls every
sticker, so it would be stake 8 under a second name.

Erosion counts the cards below the deck the run started with --
G.GAME.starting_deck_size -- not below 52. DM46XNV1 / Abandoned Deck / stake 8
bought Erosion in ante 5, and the Rust shadow scored +48 Mult for twelve face
cards the deck never had.
"""

import pytest

from jimbot_sim.game import GameState
from jimbot_sim.hands import evaluate
from jimbot_sim.jokers import make
from jimbot_sim.scoring import score_hand


def test_minus_n_is_n_with_every_sticker():
    plain = GameState(seed="NEGSTAKE", deck="Red Deck", stake=3)
    assert plain.stake == 3 and not plain.all_stickers
    assert not any(plain.sticker_rules.values())

    stickered = GameState(seed="NEGSTAKE", deck="Red Deck", stake=-3)
    assert stickered.stake == 3 and stickered.all_stickers
    assert all(stickered.sticker_rules.values())


def test_everything_but_the_stickers_reads_the_positive_stake():
    a = GameState(seed="NEGSTAKE", deck="Red Deck", stake=5)
    b = GameState(seed="NEGSTAKE", deck="Red Deck", stake=-5)
    assert a.blind_scaling == b.blind_scaling
    assert a.discards_left == b.discards_left
    assert a.blind.target == b.blind.target


def test_minus_eight_is_refused():
    with pytest.raises(ValueError, match="-8 is stake 8"):
        GameState(seed="NEGSTAKE", deck="Red Deck", stake=-8)


@pytest.mark.parametrize("deck,size", [("Red Deck", 52),
                                       ("Abandoned Deck", 40)])
def test_the_starting_deck_size_is_the_deck_dealt(deck, size):
    assert GameState(seed="EROSION1", deck=deck).starting_deck_size == size


def _erosion_mult(game):
    """The Mult a lone card scores beside Erosion, less what it scores alone."""
    card = game.full_deck[-1]
    with_it = score_hand(game, evaluate([card]), [card], []).mult
    game.jokers = []
    alone = score_hand(game, evaluate([card]), [card], []).mult
    return with_it - alone


def test_erosion_pays_nothing_on_a_whole_abandoned_deck():
    game = GameState(seed="EROSION1", deck="Abandoned Deck")
    game.jokers = [make("Erosion")]
    assert _erosion_mult(game) == 0


def test_erosion_counts_below_the_starting_deck():
    game = GameState(seed="EROSION1", deck="Abandoned Deck")
    game.jokers = [make("Erosion")]
    del game.full_deck[:3]
    assert _erosion_mult(game) == 12
