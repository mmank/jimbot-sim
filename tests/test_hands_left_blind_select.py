"""The hands and discards on the blind select screen are the ones cash-out set.

The game sets G.GAME.current_round.hands_left in exactly three places:

  game.lua:2382              G:start_run      = round_resets.hands
  button_callbacks.lua:2930  G.FUNCS.cash_out = max(1, round_resets.hands + next_hands)
  state_events.lua:297       new_round        = max(1, round_resets.hands + next_hands)

and otherwise only moves it by ease_hands_played (common_events.lua:163).
Leaving the shop and skipping a blind touch neither. Troubadour's -1 hand is
written to round_resets.hands alone (card.lua:625 on arrival, card.lua:682 on
leaving), so one bought in the shop leaves the counter where cash-out put it
until the next blind is taken.

The simulator recomputed the allowance in _next_blind, so it read 3 on the
blind select screen where the game reads 4. Smoke runs XHHF8XMY (Erratic,
stake 3) and SH8ZYY6Y (Checkered, stake 5) both stopped there, one decision
after buying a Troubadour. Measured on the headless engine: 4 after cash-out,
4 with Troubadour added, 4 after leaving the shop, 4 after skipping, 3 once
the blind is selected.
"""

from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.jokers import REGISTRY, JokerInstance
from jimbot_sim.shop import ShopSlot


def _shop(game, joker=None):
    game.phase = Phase.SHOP
    game._open_shop()
    game.shop.slots = ([ShopSlot(kind="joker", base_cost=1, joker=joker)]
                       if joker is not None else [])
    game.money = 50
    return game


def _troubadour():
    return JokerInstance(REGISTRY["Troubadour"])


def test_the_run_starts_with_the_allowance_on_the_counter():
    """game.lua:2382 -- the first blind select already shows the hands."""
    game = GameState(seed="HANDSLFT", deck="Red Deck")
    assert game.phase is Phase.BLIND_SELECT
    assert game.hands_left == 4


def test_a_troubadour_bought_in_the_shop_waits_for_the_blind():
    """card.lua:625 moves round_resets.hands only; new_round applies it."""
    game = _shop(GameState(seed="HANDSLFT", deck="Red Deck"), _troubadour())
    assert game.hands_left == 4
    game.step(Action(ActionType.BUY, index=0))
    assert game.hands_left == 4
    game.step(Action(ActionType.LEAVE_SHOP))
    assert game.phase is Phase.BLIND_SELECT
    assert game.hands_left == 4
    game.step(Action(ActionType.SELECT_BLIND))
    assert game.hands_left == 3


def test_skipping_a_blind_does_not_reset_the_counter_either():
    game = _shop(GameState(seed="HANDSLFT", deck="Red Deck"), _troubadour())
    game.step(Action(ActionType.BUY, index=0))
    game.step(Action(ActionType.LEAVE_SHOP))
    game.step(Action(ActionType.SKIP_BLIND))
    assert game.phase is Phase.BLIND_SELECT
    assert game.hands_left == 4
    game.step(Action(ActionType.SELECT_BLIND))
    assert game.hands_left == 3


def test_a_troubadour_sold_in_the_shop_gives_its_hand_back_next_round():
    """card.lua:682 -- the mirror image: 3 on the screen, 4 once selected."""
    game = GameState(seed="HANDSLFT", deck="Red Deck")
    game.gain_joker(_troubadour())
    # What cash-out would have left with the Troubadour held.
    game.hands_left, game.discards_left = game._round_allowance()
    assert game.hands_left == 3
    _shop(game)
    game.step(Action(ActionType.SELL_JOKER, index=0))
    game.step(Action(ActionType.LEAVE_SHOP))
    assert game.hands_left == 3
    game.step(Action(ActionType.SELECT_BLIND))
    assert game.hands_left == 4


def test_a_merry_andy_bought_in_the_shop_still_pays_its_discards_at_once():
    """card.lua:589-591 -- d_size is eased immediately, so it must survive
    leaving the shop now that nothing recomputes the counter there."""
    game = _shop(GameState(seed="HANDSLFT", deck="Red Deck"),
                 JokerInstance(REGISTRY["Merry Andy"]))
    before = game.discards_left
    game.step(Action(ActionType.BUY, index=0))
    game.step(Action(ActionType.LEAVE_SHOP))
    assert game.discards_left == before + 3
