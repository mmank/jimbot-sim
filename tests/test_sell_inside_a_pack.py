"""A joker can be sold while a pack is open, and so can a consumable.

Card:can_sell_card (card.lua:1640-1653) refuses a sale only while cards are
in play, the controller is locked or STOP_USE is up, and asks of the card
only that it sits in a joker-type area and is not eternal. G.jokers and
G.consumeables are both built with `type = 'joker'` (game.lua:2235-2245).
Nothing there looks at G.STATE, so a booster being open changes nothing.

The simulator offered only skip and pick inside a pack, so a full row could
never take a Buffoon joker: four of ten traced runs bought one and took
nothing, and on KJH7TR2M the pack held a Mr. Bones worth two blinds. Marcin:
*"You can sell a joker while in the buffoon pack, and this is something you
would normally do."* The environment's mask already offered the sale; this
was the simulator's own list disagreeing with it.
"""

from jimbot_sim.consumables import REGISTRY as CONSUMABLES
from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance
from jimbot_sim.shop import PackKind, PackSpec


def _full_row_and_a_buffoon():
    game = GameState(seed="KJH7TR2M", deck="Red Deck")
    while len(game.jokers) < game.joker_slots:
        game.gain_joker(JokerInstance(JOKERS["Joker"]))
    game.consumables.append(game.hold_consumable(CONSUMABLES["The Fool"]))
    game.phase = Phase.PACK
    game.pack = PackSpec(kind=PackKind.BUFFOON, size="normal", options=2,
                         picks=1, cost=4)
    game.pack_options = [JokerInstance(JOKERS["Mr. Bones"])]
    game.pack_picks_left = 1
    return game


def _of(game, kind):
    return [a.index for a in game.legal_actions() if a.type is kind]


def test_every_joker_in_a_full_row_can_be_sold_inside_a_pack():
    game = _full_row_and_a_buffoon()
    assert _of(game, ActionType.SELL_JOKER) == list(range(game.joker_slots))
    assert all(game.is_legal(Action(ActionType.SELL_JOKER, index=i))
               for i in range(game.joker_slots))


def test_an_eternal_joker_still_cannot_be_sold():
    game = _full_row_and_a_buffoon()
    game.jokers[0].eternal = True
    assert 0 not in _of(game, ActionType.SELL_JOKER)
    assert not game.is_legal(Action(ActionType.SELL_JOKER, index=0))


def test_a_consumable_can_be_sold_and_the_row_moved_inside_a_pack():
    game = _full_row_and_a_buffoon()
    assert _of(game, ActionType.SELL_CONSUMABLE) == [0]
    assert game.is_legal(Action(ActionType.SELL_CONSUMABLE, index=0))
    assert _of(game, ActionType.SWAP_JOKER_LEFT) == list(
        range(1, game.joker_slots))


def test_selling_makes_room_for_the_pack_joker_and_the_pack_stays_open():
    game = _full_row_and_a_buffoon()
    assert ActionType.PICK_PACK not in {a.type for a in game.legal_actions()}
    money = game.money
    game.step(Action(ActionType.SELL_JOKER, index=0))
    assert game.phase is Phase.PACK
    assert game.money > money
    pick = Action(ActionType.PICK_PACK, index=0)
    assert game.is_legal(pick)
    game.step(pick)
    assert "Mr. Bones" in [j.name for j in game.jokers]


def test_the_list_and_the_membership_test_agree_inside_a_pack():
    game = _full_row_and_a_buffoon()
    assert all(game.is_legal(a) for a in game.legal_actions())
