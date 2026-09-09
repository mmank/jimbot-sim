"""A card's id is where it was *built*, and the shuffle reads it.

`Card:init` is the only place the game touches its card counter, and it runs
at construction:

    G.sort_id = (G.sort_id or 0) + 1
    self.sort_id = G.sort_id

Two consequences, and this file is one test for each.

A booster builds all of its cards in one loop the moment it opens
(card.lua:1740-1780), so slot one is older than slot four whichever the
player takes first. An id stamped when a card is *taken* says the opposite.

And `pseudoshuffle` sorts the list by id before it shuffles:

    if list[1] and list[1].sort_id then
      table.sort(list, function (a, b) return (a.sort_id or 1) < (b.sort_id or 2) end)
    end
    for i = #list, 2, -1 do ... end

so the shuffle never sees the order a deck happens to be lying in -- it sees
id order. That makes a wrong id a wrong *deal*, not a cosmetic mislabelling,
which is how recording 10 came to stop 160 actions in: a mega Standard pack
gave up two Queens of Diamonds, one blue-sealed in slot one and one
polychrome in slot four, they were taken four-then-one, and from the next
round on the simulator dealt the wrong one.
"""

from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import GameState


def test_a_pack_slot_keeps_its_build_order_when_taken_out_of_order():
    game = GameState(seed="TESTSEED")
    # Build the two the way _open_pack does: in slot order, before either is
    # picked. That is the whole claim -- slot one is older.
    slot_one = game._pack_card(
        {"set": "Playing", "rank": "Q", "suit": "D",
         "enhancement": None, "edition": "none", "seal": "Blue"})
    slot_four = game._pack_card(
        {"set": "Playing", "rank": "Q", "suit": "D",
         "enhancement": None, "edition": "polychrome", "seal": None})
    assert slot_one.uid < slot_four.uid

    # Now take them in the other order, as recording 10 does.
    game.add_card(slot_four)
    game.add_card(slot_one)
    assert slot_one.uid < slot_four.uid, (
        "add_card restamped the id and made it an acquisition order")


def test_the_round_shuffle_sorts_by_id_first():
    """A deck lying in a different order deals the same, as pseudoshuffle does."""
    one = GameState(seed="TESTSEED")
    two = GameState(seed="TESTSEED")
    # Same cards, one deck's list rotated. pseudoshuffle sorts before it
    # shuffles, so the deal must not notice.
    two.full_deck = two.full_deck[7:] + two.full_deck[:7]

    one._start_round()
    two._start_round()

    # Compared by face, not by id: two runs draw their ids from one global
    # counter, so the same card holds a different number in each.
    assert ([(c.rank, c.suit) for c in one.hand]
            == [(c.rank, c.suit) for c in two.hand])
