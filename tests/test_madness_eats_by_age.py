"""Madness eats a joker picked by age, and a joker's age is when it was built.

card.lua:2503-2509, on setting_blind and not on a boss:

    for i = 1, #G.jokers.cards do
        if G.jokers.cards[i] ~= self and not G.jokers.cards[i].ability.eternal
        and not G.jokers.cards[i].getting_sliced then
            destructable_jokers[#destructable_jokers+1] = G.jokers.cards[i] end
    end
    local joker_to_destroy = #destructable_jokers > 0
        and pseudorandom_element(destructable_jokers, pseudoseed('madness'))

pseudorandom_element (misc_functions.lua:260-261) sorts that table by sort_id
before it indexes, so the row order the list was built in does not matter.
And sort_id is stamped in Card:init (card.lua:24-25) -- at construction. A
shop builds its jokers in slot order (game.lua:3111-3113), and buying one
moves that same Card into the row (button_callbacks.lua:2417-2435), so a
joker bought second out of slot one is *older* than one bought first out of
slot two. The recordings' joker_ids agree: recording 6 step 11 buys Misprint
(53) with Devious (58) already held; recording 8 step 144 buys Astronomer
(235) after Hanging Chad (236).

The simulator handed Madness the row unsorted, and stamped a joker's age when
it joined the row. Smoke run N1OA90W1 (Abandoned Deck, stake 1) stopped at
decision 109 on it: the shop shelved Popcorn in slot one and Mystic Summit in
slot two, the policy bought Mystic Summit and then Popcorn, and at the next
Small Blind the game's Madness ate Mystic Summit where the simulator's ate
Popcorn.
"""

from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.jokers import REGISTRY, JokerInstance
from jimbot_sim.shop import ShopSlot


def _survivors(game):
    return [j.name for j in game.jokers]


def test_madness_draws_by_age_not_by_row_position():
    """Same jokers, same ages, same seed -- only the row order differs."""
    rows = []
    for order in ((0, 1, 2), (0, 2, 1)):
        game = GameState(seed="MADNESS1", deck="Red Deck")
        built = [JokerInstance(REGISTRY[name])
                 for name in ("Madness", "Popcorn", "Mystic Summit")]
        for i in order:
            game.gain_joker(built[i])
        game.step(Action(ActionType.SELECT_BLIND))
        assert game.jokers[0].name == "Madness"
        assert len(game.jokers) == 2, "Madness ate nothing"
        rows.append(sorted(_survivors(game)))
    assert rows[0] == rows[1], (
        "Madness picked its victim by where the jokers sit in the row")


def _shop_then_blind(buys):
    """Madness held; Popcorn shelved in slot one, Mystic Summit in slot two."""
    game = GameState(seed="N1OA90W1", deck="Red Deck")
    game.gain_joker(JokerInstance(REGISTRY["Madness"]))
    game.phase = Phase.SHOP
    game._open_shop()
    # Built in slot order, as the shop builds them.
    popcorn = JokerInstance(REGISTRY["Popcorn"])
    summit = JokerInstance(REGISTRY["Mystic Summit"])
    game.shop.slots = [ShopSlot(kind="joker", base_cost=1, joker=popcorn),
                       ShopSlot(kind="joker", base_cost=1, joker=summit)]
    game.money = 50
    for index in buys:
        game.step(Action(ActionType.BUY, index=index))
    game.step(Action(ActionType.LEAVE_SHOP))
    game.step(Action(ActionType.SELECT_BLIND))
    return game, popcorn, summit


def test_a_joker_bought_later_from_an_earlier_slot_is_older():
    """The N1OA90W1 shop: slot two bought first, then slot one."""
    game, popcorn, summit = _shop_then_blind(buys=(1, 0))
    assert popcorn.uid < summit.uid, (
        "the age was stamped on purchase, not where the shop built the joker")
    # The control buys the same shelf in slot order, so row order and age
    # agree; the draw off 'madness' is the same in both runs.
    control, _, _ = _shop_then_blind(buys=(0, 0))
    assert len(game.jokers) == 2 and len(control.jokers) == 2
    assert _survivors(game) == _survivors(control)
