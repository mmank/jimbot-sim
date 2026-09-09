"""A joker needs a free slot. A Negative joker does not.

The rule is the game's, stated in two places and the same in both:
G.FUNCS.can_select_card (button_callbacks.lua:2112), which decides whether a
pack card is given a use_card button at all, and the shop's can_buy at 2396,
which adds one to the limit when the card is Negative.

`joker_slots` already counts the Negatives in the row, because add_to_deck
raises the limit as one arrives -- so the property answers "how many fit"
correctly for what is held, and says nothing about what is being offered. The
card on the shelf has not arrived. Asking only the property refused every
Negative into a full row, in the shop and out of a Buffoon pack, both of
which the game allows.

The mirror image of the same rule was live in the mod, where a joker was
reported takeable whatever the row held, and a policy played on from a run
with six jokers in five slots.
"""

import pytest

from jimbot_sim.cards import Edition
from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.jokers import REGISTRY, JokerInstance
from jimbot_sim.shop import ShopSlot


def _full_row(game):
    """Five jokers, none of them Negative: the row is exactly full."""
    while len(game.jokers) < game.joker_slots:
        game.gain_joker(JokerInstance(REGISTRY["Joker"]))
    assert len(game.jokers) == game.joker_slots == 5
    return game


def _game():
    return _full_row(GameState(seed="ROOMTEST", deck="Red Deck"))


def _joker(negative=False):
    return JokerInstance(REGISTRY["Blueprint"],
                         edition=Edition.NEGATIVE if negative else Edition.NONE)


# ------------------------------------------------------------------
# the rule itself
# ------------------------------------------------------------------

def test_a_full_row_has_no_room_for_an_ordinary_joker():
    assert not _game().room_for_joker(_joker())


def test_a_full_row_has_room_for_a_negative_joker():
    """It takes no slot -- add_to_deck raises the limit as it arrives."""
    assert _game().room_for_joker(_joker(negative=True))


def test_a_row_with_a_gap_has_room_for_either():
    game = _game()
    game.jokers.pop()
    assert game.room_for_joker(_joker())
    assert game.room_for_joker(_joker(negative=True))


# ------------------------------------------------------------------
# where it is asked: the pack
# ------------------------------------------------------------------

def _open_buffoon(game, negative=False):
    game.phase = Phase.PACK
    game.pack_options = [_joker(negative=negative)]
    game.pack_picks_left = 1
    return game


def test_a_buffoon_joker_is_not_offered_into_a_full_row():
    game = _open_buffoon(_game())
    assert not any(a.type is ActionType.PICK_PACK for a in game.legal_actions())
    assert not game.is_legal(Action(ActionType.PICK_PACK, index=0))


def test_a_negative_buffoon_joker_is_offered_into_a_full_row():
    """The one the simulator refused and the game allows."""
    game = _open_buffoon(_game(), negative=True)
    assert any(a.type is ActionType.PICK_PACK for a in game.legal_actions())
    assert game.is_legal(Action(ActionType.PICK_PACK, index=0))


# ------------------------------------------------------------------
# and the shop
# ------------------------------------------------------------------

def _shop_with(game, joker):
    game.phase = Phase.SHOP
    game._open_shop()
    game.shop.slots = [ShopSlot(kind="joker", base_cost=1, joker=joker)]
    # `money`, not `dollars`: the engine's name for it, and setting the one
    # the simulator does not have left these two testing room *and* an
    # accidental four dollars. A Negative joker's edition is five of
    # `extra_cost`, so the slot stopped being affordable the moment prices
    # were worked out from the base rather than stored.
    game.money = 50
    return game


def test_a_shop_joker_is_not_offered_into_a_full_row():
    game = _shop_with(_game(), _joker())
    assert not any(a.type is ActionType.BUY for a in game.legal_actions())
    assert not game.is_legal(Action(ActionType.BUY, index=0))


def test_a_negative_shop_joker_is_offered_into_a_full_row():
    game = _shop_with(_game(), _joker(negative=True))
    assert any(a.type is ActionType.BUY for a in game.legal_actions())
    assert game.is_legal(Action(ActionType.BUY, index=0))


# ------------------------------------------------------------------
# and where it is deliberately not asked
# ------------------------------------------------------------------

def test_creation_is_still_stopped_by_a_full_row():
    """A joker made rather than taken is checked before it exists.

    Riff-raff and a Judgement ask for room and then create (card.lua:2529,
    3967), so there is no edition to consult and a full row stops the
    creation whatever it would have rolled. Routing creation through
    room_for_joker would have quietly given the simulator free jokers.
    """
    game = _game()
    before = len(game.jokers)
    game.add_random_joker("test")
    assert len(game.jokers) == before


# ------------------------------------------------------------------
# and a consumable that makes a joker
# ------------------------------------------------------------------

def _open_arcana_with(game, name):
    """An Arcana pack holding one card, as the shop would open it."""
    from jimbot_sim.consumables import REGISTRY as CONSUMABLES

    game.phase = Phase.PACK
    game.pack_options = [CONSUMABLES[name]]
    game.pack_picks_left = 1
    return game


def test_a_judgement_is_not_offered_out_of_a_pack_into_a_full_row():
    """A pack consumable's button is can_use_consumeable, not the looser
    can_select_card -- UI_definitions.lua, use_and_sell_buttons, the branch
    for a consumable in G.pack_cards. Judgement needs a free slot there
    exactly as it does from the consumable row."""
    game = _open_arcana_with(_game(), "Judgement")
    assert not any(a.type is ActionType.PICK_PACK for a in game.legal_actions())
    assert not game.is_legal(Action(ActionType.PICK_PACK, index=0))


def test_a_judgement_is_offered_out_of_a_pack_into_a_gap():
    game = _game()
    game.jokers.pop()
    game = _open_arcana_with(game, "Judgement")
    assert game.is_legal(Action(ActionType.PICK_PACK, index=0))


def test_a_planet_is_still_offered_out_of_a_pack_with_a_full_row():
    """The row is full of jokers; a Planet needs none of them."""
    game = _open_arcana_with(_game(), "Mercury")
    assert game.is_legal(Action(ActionType.PICK_PACK, index=0))
