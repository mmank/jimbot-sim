"""Where the simulator parted from the engine, walked side by side.

The training repository's backend parity test walks the engine and the
simulator in lockstep with blinds cut to one chip, so random play reaches the
shops, the packs and the later antes. Each difference it found is pinned here
without an engine, from the game's own Lua.
"""

from jimbot_sim import shop as shop_mod
from jimbot_sim.game import Action, ActionType, GameState, Phase, Tag
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance
from jimbot_sim.run import SimRun
from jimbot_sim.state import state_dict


def _in_a_shop(seed: str = "AGREE001") -> GameState:
    game = GameState(seed=seed, deck="Red Deck")
    game.step(Action(ActionType.SELECT_BLIND))
    game.blind.target = 1
    game.step(Action(ActionType.PLAY, cards=(0,)))
    game.step(Action(ActionType.CASH_OUT))
    assert game.phase is Phase.SHOP
    return game


def test_a_standard_pack_holds_playing_cards_not_jokers():
    # SET_IDS in bot_api.lua: Default 7, Enhanced 8. This said Joker (1).
    game = _in_a_shop()
    game._open_pack(shop_mod.pack_from_key("p_standard_normal_1"))
    sets = {row["set"] for row in state_dict(game)["pack"]}
    assert sets and sets <= {7, 8}


def test_chaos_the_clown_moves_the_reroll_price_between_shops():
    # calculate_reroll_cost: free while current_round.free_rerolls > 0, and
    # add_to_deck / remove_from_deck move that count in or out of a shop.
    game = _in_a_shop()
    game.step(Action(ActionType.LEAVE_SHOP))
    assert game.reroll_cost_carried == 5
    chaos = JokerInstance(JOKERS["Chaos the Clown"])
    game.gain_joker(chaos)
    assert game.reroll_cost_carried == 0
    game.step(Action(ActionType.SELL_JOKER, index=game.jokers.index(chaos)))
    assert game.reroll_cost_carried == 5


def test_the_d6_tag_prices_the_next_round_too():
    # round_resets.temp_reroll_cost stands until end_round
    # (state_events.lua:271), so the round after the shop reads 0.
    game = GameState(seed="AGREE002", deck="Red Deck")
    game.temp_reroll_cost = True
    game.step(Action(ActionType.SELECT_BLIND))
    assert game.reroll_cost_carried == 0
    game.blind.target = 1
    game.step(Action(ActionType.PLAY, cards=(0,)))
    assert not game.temp_reroll_cost and game.reroll_cost_carried == 5


def test_two_pack_tags_open_one_pack_each_in_turn():
    # One new_blind_choice firing opens one pack (tag.lua breaks the loop);
    # the second opens when the first closes (button_callbacks.lua:2618).
    game = GameState(seed="AGREE003", deck="Red Deck")
    game.tags = [Tag.ETHEREAL, Tag.ETHEREAL]
    game._apply_blind_select_tags()
    assert game.phase is Phase.PACK and game.tags == [Tag.ETHEREAL]
    first = list(game.pack_options)
    game.step(Action(ActionType.SKIP_PACK))
    assert game.phase is Phase.PACK and game.tags == []
    assert game.pack_options != first


def test_any_voucher_bought_takes_the_antes_off_the_shelves():
    # Card:redeem clears current_round.voucher whichever voucher it was.
    game = _in_a_shop("AGREE004")
    game.money = 100
    if not game.shop.vouchers:
        return
    game.step(Action(ActionType.BUY_VOUCHER, index=0))
    assert game.round_voucher == ""


def test_the_cerulean_bells_card_is_selected_and_stays_so():
    run = SimRun()
    run.start("AGREE005", "Red Deck", 1)
    run.step(Action(ActionType.SELECT_BLIND))
    run.game.forced_card = run.game.hand[3]
    boss = type("Bell", (), {"forces_a_card": True})()
    real = type(run.game).boss
    try:
        type(run.game).boss = property(lambda self: boss)
        assert run.selection() == (3,)
        run.toggle(3)                              # a click cannot free it
        assert run.selection() == (3,)
        run.clear()
        assert run.selection() == (3,)
        for i in (0, 1, 2, 4, 5):
            run.toggle(i)
        assert len(run.selection()) == 5           # it counts towards five
    finally:
        type(run.game).boss = real


def test_a_planet_leaves_the_highlight_a_tarot_that_converts_takes_it():
    # card.lua:1150 and 1190 unhighlight; nothing else does.
    from jimbot_sim import consumables as cons

    run = SimRun()
    run.start("AGREE006", "Red Deck", 1)
    run.step(Action(ActionType.SELECT_BLIND))
    run.game.consumables = []
    run.game.add_consumables([cons.REGISTRY["Pluto"],
                              cons.REGISTRY["The Lovers"]])
    run.toggle(0)
    run.step(Action(ActionType.USE_CONSUMABLE, index=0, cards=run.selection()))
    assert run.selection() == (0,)
    assert run.state()["toggles_used"] == 1        # the same cards held
    run.step(Action(ActionType.USE_CONSUMABLE, index=0, cards=run.selection()))
    assert run.selection() == ()
