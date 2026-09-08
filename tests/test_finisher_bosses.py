"""The bosses that do something to the run rather than to a card.

Nine bosses carried no mechanical modifier at all. For four of them that is
right: The House, The Wheel, The Fish and The Mark draw cards face down, and
an engine with full information has nothing to hide. The other five were
simply not built, and they are not small -- one of them switches a joker off
every hand, one debuffs the whole deck, one shuffles the joker row, which
decides the order effects resolve in.

These check the mechanics rather than the numbers, because the numbers depend
on what the run is carrying.
"""

import pytest

from jimbot_sim.blinds import BOSSES, FINISHER_BOSSES, BlindKind, make_blind
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

BY_NAME = {b.name: b for b in BOSSES + FINISHER_BOSSES}


def _under(boss_name, jokers=()):
    """A run standing in a round against one named boss."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in jokers:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game.ante_boss = ""
    game.blind = make_blind(BlindKind.BOSS, game.ante, BY_NAME[boss_name])
    game._start_round()
    return game


def test_the_face_down_bosses_are_the_only_blank_ones():
    """A blank boss has to be blank for a reason, and the reason is stated."""
    cosmetic = {"The House", "The Wheel", "The Fish", "The Mark"}
    blank = {b.name for b in BOSSES + FINISHER_BOSSES
             if b.chip_mult == 2.0 and not any(
                 getattr(b, f.name) for f in b.__dataclass_fields__.values()
                 if f.name not in ("name", "text", "chip_mult", "is_finisher"))}
    assert blank == cosmetic


def test_the_serpent_deals_three_however_much_room_there_is():
    game = _under("The Serpent")
    dealt = len(game.hand)
    game.step(Action(ActionType.DISCARD, cards=(0, 1)))
    # Two gone and three back: the hand ends up larger than it started, which
    # is the whole shape of the blind.
    assert len(game.hand) == dealt - 2 + 3


def test_amber_acorn_shuffles_the_joker_row():
    """Order decides the order effects resolve in, so this is not cosmetic."""
    names = ["Joker", "Greedy Joker", "Lusty Joker", "Wrathful Joker",
             "Gluttonous Joker"]
    game = _under("Amber Acorn", jokers=names)
    assert [j.name for j in game.jokers] != names


def test_crimson_heart_switches_one_joker_off_each_hand():
    game = _under("Crimson Heart", jokers=["Joker", "Greedy Joker"])
    assert not any(j.debuffed for j in game.jokers)
    game.step(Action(ActionType.PLAY, cards=(0,)))
    assert sum(1 for j in game.jokers if j.debuffed) == 1


def test_verdant_leaf_debuffs_the_deck_until_a_joker_is_sold():
    game = _under("Verdant Leaf", jokers=["Joker"])
    assert all(c.debuffed for c in game.hand), "the whole deck should be off"
    game.step(Action(ActionType.SELL_JOKER, index=0))
    game._apply_debuffs()
    assert not any(c.debuffed for c in game.hand), "selling should lift it"


def test_cerulean_bell_forces_a_card_into_every_hand():
    game = _under("Cerulean Bell")
    forced = game.forced_card
    assert forced is not None and forced in game.hand

    index = game.hand.index(forced)
    others = tuple(i for i in range(len(game.hand)) if i != index)[:2]
    assert not game.is_legal(Action(ActionType.PLAY, cards=others))
    with_forced = tuple(sorted((index,) + others[:1]))
    assert game.is_legal(Action(ActionType.PLAY, cards=with_forced))


def test_dragging_does_not_shake_the_forced_card_off():
    """It follows the card, not the position.

    The game sets ability.forced_selection on the card itself and keeps it
    highlighted -- you cannot deselect it, and moving it around the hand does
    not change that. So the constraint has to be about identity: a simulator
    that remembered an index would let a player drag their way out of the
    blind, and the replay harness reorders hands to follow recorded drags,
    which would trip it every time.
    """
    game = _under("Cerulean Bell")
    forced = game.forced_card
    game.hand.reverse()                       # the player drags it about

    assert game.forced_card is forced
    index = game.hand.index(forced)
    others = tuple(i for i in range(len(game.hand)) if i != index)[:2]
    assert not game.is_legal(Action(ActionType.PLAY, cards=others))
    assert game.is_legal(Action(ActionType.PLAY,
                                cards=tuple(sorted((index,) + others[:1]))))


def test_a_forced_card_is_replaced_once_it_is_gone():
    game = _under("Cerulean Bell")
    first = game.forced_card
    game.step(Action(ActionType.DISCARD,
                     cards=(game.hand.index(first),)))
    assert game.forced_card is not first
    assert game.forced_card in game.hand
