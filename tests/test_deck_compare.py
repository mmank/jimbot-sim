"""Two decks holding different cards are a difference.

FATBOY02 stopped 299 decisions into a live run with a 6D in the game's hand
and a red-sealed 6H in the shadow's -- a deck that had disagreed for some
time, and showed it only when a draw happened to deal the difference. Both
sides report the deck's make-up for the observation encoder; nothing
compared it.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from jimbot_sim.cards import Card, Enhancement, Rank, Seal, Suit  # noqa: E402
from jimbot_sim.compare import Names, deck_cards, differences  # noqa: E402
from jimbot_sim.game import GameState  # noqa: E402
from jimbot_sim.state import state_dict  # noqa: E402


def _run():
    return GameState(seed="AWEFRTUZ", deck="Red Deck", stake=1)


def _difference(game, shadow):
    return differences(state_dict(game), state_dict(shadow), Names())


def _field(lines, name):
    return [line for line in lines if line.startswith(name)]


def test_a_deck_of_the_same_cards_is_no_difference():
    assert not _difference(_run(), _run())


def test_the_card_that_is_not_the_same_card_is_named():
    game, shadow = _run(), _run()
    game.add_card_to_hand(Card(Rank.SIX, Suit.DIAMONDS))
    shadow.add_card_to_hand(Card(Rank.SIX, Suit.HEARTS, seal=Seal.RED))
    found = _field(_difference(game, shadow), "deck_cards")
    assert len(found) == 1, found
    # Only what differs: the suits that moved and the seal, not all 52.
    assert "'D': 14" in found[0] and "'H': 14" in found[0], found[0]
    assert "'seal red': 1" in found[0], found[0]
    assert "'S'" not in found[0], found[0]


def test_an_enhancement_on_one_side_only():
    game, shadow = _run(), _run()
    shadow.set_enhancement(shadow.full_deck[0], Enhancement.STEEL)
    found = _field(_difference(game, shadow), "deck_cards")
    assert len(found) == 1 and "steel" in found[0], found


def test_the_deck_is_read_by_name():
    game = _run()
    game.add_card_to_hand(Card(Rank.ACE, Suit.SPADES, seal=Seal.GOLD,
                               enhancement=Enhancement.GOLD))
    held = deck_cards(state_dict(game))
    assert held["A"] == 5 and held["S"] == 14
    assert held["seal gold"] == 1 and held["gold"] == 1
    assert held["plain"] == 52


def test_a_hand_in_flight_is_not_compared():
    """Mid-round the deck is in flight, and the live driver re-reads until
    every difference clears -- a tenth of a second at a time, after every
    action. Marcin: *"there is now like a 1 second hiccup before every play or
    discard."* A deck that has really diverged is still caught at the shop."""
    from jimbot_sim.game import Action, ActionType, Phase

    game, shadow = _run(), _run()
    for one in (game, shadow):
        one.step(Action(ActionType.SELECT_BLIND))
        assert one.phase is Phase.PLAYING
    shadow.add_card_to_hand(Card(Rank.SIX, Suit.HEARTS, seal=Seal.RED))
    assert not _field(_difference(game, shadow), "deck_cards")
