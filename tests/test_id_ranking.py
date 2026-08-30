"""Card ids are compared by order, not by value.

A card's id is its place in the run's card counter, and that counter moves for
things a recording cannot contain -- opening the deck collection screen builds
fifty-two Card objects behind the deck art, and looking at a menu is not a
game action. So the same card ends up under a different number.

What survives is the order. Both counters only ever go up, and the extra cards
appear at one moment on one side, so a card made earlier keeps a lower id than
one made later on both sides. Ranking each list against itself drops the
offset and keeps that.

The point of these tests is that ranking is not the same as ignoring: a card
genuinely in the wrong place still moves a rank and is still caught.
"""

import sys

sys.path.insert(0, "scripts")

from record_replay import _ranked, differences


def test_a_constant_offset_disappears():
    assert _ranked([68, 53, 54, 136]) == _ranked([68, 53, 54, 83])


def test_the_order_is_what_is_kept():
    assert _ranked([68, 53, 54, 136]) == [2, 0, 1, 3]


def test_a_card_out_of_place_is_still_caught():
    """The same ids in a different order must not compare equal."""
    assert _ranked([53, 54, 68]) != _ranked([54, 53, 68])


def test_a_missing_card_is_still_caught():
    assert _ranked([53, 54, 68, 83]) != _ranked([53, 54, 68])


def test_a_swapped_pair_of_new_cards_is_still_caught():
    """Two cards created during the run are not interchangeable.

    Blanking drifted ids to a placeholder lost this: both became "new" and a
    hand holding them the other way round compared equal.
    """
    assert _ranked([4, 17, 171, 172]) != _ranked([4, 17, 119, 118])


def test_non_numeric_lists_are_left_alone():
    assert _ranked(["j_joker", "j_banner"]) == ["j_joker", "j_banner"]
    assert _ranked(None) is None


def test_differences_uses_the_ranking():
    drifted = {"phase": "SHOP", "joker_ids": [68, 53, 54, 136]}
    engine = {"phase": "SHOP", "joker_ids": [68, 53, 54, 83]}
    assert differences(drifted, engine) == []

    reordered = {"phase": "SHOP", "joker_ids": [53, 68, 54, 136]}
    assert differences(reordered, engine), "a reordering slipped through"
