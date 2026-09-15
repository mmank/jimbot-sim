"""Invisible Joker, sold after two rounds, copies a random other joker.

The simulator counted its rounds and did nothing when it was sold. The game
answers selling_self (card.lua:2371-2390):

    if self.ability.name == 'Invisible Joker'
       and (self.ability.invis_rounds >= self.ability.extra)   -- extra = 2
       and not context.blueprint then
        jokers = every card in G.jokers.cards other than self
        if #jokers > 0 then
            if #G.jokers.cards <= G.jokers.config.card_limit then
                chosen_joker = pseudorandom_element(jokers, pseudoseed('invisible'))
                card = copy_card(chosen_joker, nil, nil, nil,
                                 chosen_joker.edition and chosen_joker.edition.negative)
                if card.ability.invis_rounds then card.ability.invis_rounds = 0 end
                card:add_to_deck()
                G.jokers:emplace(card)

Card:sell_card fires that before the card dissolves (card.lua:1599), so the
room check counts the Invisible Joker as still in the row. A debuffed joker
answers no context at all (card.lua:2292). Seed 71AAZBQV, Painted Deck, stake
1: an Invisible Joker at four rounds sold beside two jokers left the game with
three and the simulator with two.
"""

import copy

from jimbot_sim.cards import Edition
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance


def _row(*names, invisible_rounds=2):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in names:
        joker = JokerInstance(JOKERS[name])
        if name == "Invisible Joker":
            joker.counter = invisible_rounds
        game.gain_joker(joker)
    return game


def _sell_invisible(game):
    index = next(i for i, j in enumerate(game.jokers)
                 if j.name == "Invisible Joker")
    game.step(Action(ActionType.SELL_JOKER, index=index))


def test_a_charged_invisible_joker_leaves_a_copy_behind():
    """pseudorandom_element sorts by sort_id before it draws
    (misc_functions.lua:253-268), so the row is dragged out of age order
    here: a draw over the row as it stands picks the other joker."""
    game = _row("Joker", "Greedy Joker", "Invisible Joker")
    joker, greedy, invisible = game.jokers
    game.jokers[:] = [greedy, invisible, joker]
    expected = copy.deepcopy(game).rng.choice("invisible", [joker, greedy])

    _sell_invisible(game)

    assert [j.name for j in game.jokers[:2]] == ["Greedy Joker", "Joker"]
    assert len(game.jokers) == 3, "the sale should leave a copy in the row"
    clone = game.jokers[2]
    assert clone.name == expected.name
    assert clone is not joker and clone is not greedy
    assert clone.uid > max(joker.uid, greedy.uid)


def test_one_round_is_not_enough():
    game = _row("Joker", "Invisible Joker", invisible_rounds=1)
    _sell_invisible(game)
    assert [j.name for j in game.jokers] == ["Joker"]


def test_a_debuffed_invisible_joker_copies_nothing():
    """calculate_joker opens with `if self.debuff then return nil end`."""
    game = _row("Joker", "Invisible Joker")
    game.jokers[1].debuffed = True
    _sell_invisible(game)
    assert [j.name for j in game.jokers] == ["Joker"]


def test_alone_it_copies_nothing():
    game = _row("Invisible Joker")
    _sell_invisible(game)
    assert game.jokers == []


def test_the_copy_of_a_negative_is_not_negative():
    """copy_card's strip_edition skips set_edition entirely
    (common_events.lua:2169-2171), and the caller passes it for a Negative."""
    game = _row("Joker", "Invisible Joker")
    game.jokers[0].edition = Edition.NEGATIVE
    _sell_invisible(game)
    assert [j.edition for j in game.jokers] == [Edition.NEGATIVE, Edition.NONE]


def test_the_copy_keeps_everything_else_the_original_has():
    """The loop over other.ability (common_events.lua:2161-2167) carries the
    counter, the stickers and hands_played_at_create -- set_ability stamps
    the new card's own (card.lua:337) and the loop writes over it."""
    game = _row("Loyalty Card", "Invisible Joker")
    loyalty = game.jokers[0]
    loyalty.edition = Edition.FOIL
    loyalty.perishable, loyalty.perish_tally = True, 3
    game.hands_played = 7
    _sell_invisible(game)
    clone = game.jokers[1]
    assert clone.name == "Loyalty Card"
    assert clone.edition is Edition.FOIL
    assert (clone.perishable, clone.perish_tally) == (True, 3)
    assert clone.hands_at_create == loyalty.hands_at_create == 0


def test_a_copied_invisible_joker_starts_counting_again():
    game = _row("Invisible Joker", "Invisible Joker", invisible_rounds=3)
    game.step(Action(ActionType.SELL_JOKER, index=0))
    assert [j.name for j in game.jokers] == ["Invisible Joker"] * 2
    assert [j.counter for j in game.jokers] == [3, 0]


def test_a_full_row_still_gets_its_copy():
    """#G.jokers.cards <= card_limit is read with the sold card still held."""
    game = _row("Joker", "Greedy Joker", "Lusty Joker", "Wrathful Joker",
                "Invisible Joker")
    assert len(game.jokers) == game.joker_slots
    _sell_invisible(game)
    assert len(game.jokers) == 5


def test_a_negative_invisible_joker_still_counts_its_own_slot():
    """Six cards against a limit of six while it is held, so the copy is made
    -- and the row ends one over once remove_from_deck takes the slot back."""
    game = _row("Joker", "Greedy Joker", "Lusty Joker", "Wrathful Joker",
                "Gluttonous Joker", "Invisible Joker")
    game.jokers[5].edition = Edition.NEGATIVE
    assert len(game.jokers) == game.joker_slots == 6
    _sell_invisible(game)
    assert len(game.jokers) == 6
    assert game.joker_slots == 5
