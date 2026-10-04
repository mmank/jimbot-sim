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


# -- a second round, across every deck and stake --------------------------

def _boss(name):
    from jimbot_sim.blinds import BlindKind, make_blind
    from jimbot_sim.game import _BOSS_BY_NAME

    return lambda game: make_blind(BlindKind.BOSS, game.ante,
                                   _BOSS_BY_NAME[name])


def _on_deck(name):
    """The boss offered on the blind select screen, not yet set."""
    def build(game):
        blind = _boss(name)(game)
        blind.on_deck = True
        return blind
    return build


def test_checkered_rolls_its_held_cards_before_it_converts():
    # start_run rolls The Idol's card and Castle's suit synchronously; the
    # Checkered Deck's conversion is an event after it (back.lua:239).
    for seed in ("CHECK001", "CHECK002", "CHECK003", "CHECK004"):
        red = GameState(seed=seed, deck="Red Deck")
        checkered = GameState(seed=seed, deck="Checkered Deck")
        assert checkered.castle_suit == red.castle_suit
        assert checkered.idol_suit == red.idol_suit


def test_a_draw_of_nothing_leaves_the_hand_as_dragged():
    game = GameState(seed="AGREE010", deck="Red Deck")
    game.step(Action(ActionType.SELECT_BLIND))
    game.swap_card_left(4)
    order = list(game.hand)
    game._draw_to_hand_size()                     # the hand is already full
    assert all(a is b for a, b in zip(game.hand, order))


def test_the_d6_tags_price_leaves_the_vouchers_out():
    # (temp_reroll_cost or round_resets.reroll_cost) + increase; a reroll
    # voucher bought there cuts the current price until the next reroll.
    shop = shop_mod.Shop(free_reroll_cost=True, rerolls=1)
    assert shop.reroll_price(2) == 1
    shop.cut_until_reroll = 2
    assert shop.reroll_price(2) == 0


def test_every_antes_orbital_hands_are_rolled_at_its_blind_select():
    # create_UIBox_blind_choice rolls Small, Big and Boss in turn.
    game = GameState(seed="AGREE011", deck="Red Deck")
    assert {(1, 0), (1, 1), (1, 2)} <= set(game.orbital_choices)


def test_the_boss_on_deck_is_not_in_force_until_the_round_sets_it():
    # On the blind select screen G.GAME.blind is the empty blind the last
    # round left (blind.lua:336); the boss on offer acts from set_blind.
    game = _in_a_shop("AGREE012")
    game.step(Action(ActionType.LEAVE_SHOP))
    assert game.phase is Phase.BLIND_SELECT and game.blind.on_deck
    game.blind = _on_deck("The Manacle")(game)
    size = game.hand_size
    assert game.boss is None
    game.step(Action(ActionType.SELECT_BLIND))
    assert not game.blind.on_deck
    assert game.boss is not None and game.hand_size == size - 1


def test_a_boss_built_off_the_blind_select_is_in_force():
    # What a policy's fork does to price the boss to come from a shop.
    game = _in_a_shop("AGREE018")
    game.blind = _boss("The Manacle")(game)
    assert game.boss is not None


def test_a_pack_at_the_blind_select_does_not_spend_the_bells_draw():
    game = GameState(seed="AGREE013", deck="Red Deck")
    game.blind = _on_deck("Cerulean Bell")(game)
    game._open_pack(shop_mod.pack_from_key("p_arcana_normal_1"))
    assert game.hand and game.forced_card is None
    assert not any(key.startswith("cerulean_bell") for key in game.rng.pools)


def test_a_sale_before_the_verdant_leaf_leaves_it_standing():
    game = GameState(seed="AGREE014", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Joker"]))
    game.blind = _on_deck("Verdant Leaf")(game)
    game.step(Action(ActionType.SELL_JOKER, index=0))
    assert not game.blind.disabled


def test_a_coupon_tag_taken_mid_shop_waits_for_the_next_shop():
    game = _in_a_shop("AGREE015")
    game.money = 100
    game.tags.append(Tag.COUPON)
    game.step(Action(ActionType.REROLL))
    assert Tag.COUPON in game.tags
    assert all(not slot.couponed for slot in game.shop.slots)


def test_ankh_draws_its_joker_oldest_first():
    # pseudorandom_element sorts by sort_id (card.lua:1434); a drag must not
    # change which joker is copied.
    from jimbot_sim import consumables as cons

    picks = []
    for order in ((0, 1), (1, 0)):
        game = GameState(seed="AGREE016", deck="Red Deck")
        made = [JokerInstance(JOKERS[n]) for n in ("Joker", "Jolly Joker")]
        for i in order:
            game.gain_joker(made[i])
        game.use_consumable(cons.REGISTRY["Ankh"], [])
        picks.append(sorted(j.name for j in game.jokers))
    assert picks[0] == picks[1]


def test_a_joker_changing_hands_asks_every_card_its_debuff_again():
    # add_to_deck and remove_from_deck end with set_blind(nil, true).
    from jimbot_sim.cards import Suit

    game = GameState(seed="AGREE017", deck="Red Deck")
    game.blind = _boss("The Window")(game)       # Diamonds
    game.step(Action(ActionType.SELECT_BLIND))
    game.blind = _boss("The Window")(game)
    game.phase = Phase.PLAYING
    smeared = JokerInstance(JOKERS["Smeared Joker"])
    game.gain_joker(smeared)
    hearts = [c for c in game.full_deck if c.suit is Suit.HEARTS]
    assert all(c.debuffed for c in hearts)
    game.step(Action(ActionType.SELL_JOKER,
                     index=game.jokers.index(smeared)))
    assert not any(c.debuffed for c in hearts)


# -- the headless engine ---------------------------------------------------

def _engine():
    import pytest

    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available

    if not engine_available():
        pytest.skip("no Balatro engine")
    return HeadlessBalatro().boot()


def test_the_engine_walks_the_hands_in_the_games_own_order():
    # pairs(G.GAME.hands) under lupa's LuaJIT 2.1 is hash order seeded per
    # process; To Do List and the Orbital Tag index into it. Headless walks
    # the shipped game's own order instead (LuaJIT 2.0.5, the same in every
    # process): hands.GAME_PAIRS_ORDER.
    from jimbot_sim.hands import GAME_PAIRS_ORDER

    game = _engine()
    game.execute('BOT.start_run({"ORDER001","Red_Deck"}); api.pump(300)')
    walked = game.eval("(function() local t = {} for k in pairs(G.GAME.hands)"
                       " do t[#t+1] = k end return table.concat(t, ',') end)()")
    assert walked == ",".join(h.label for h in GAME_PAIRS_ORDER)


def test_a_skipped_orbital_tag_levels_its_hand():
    # Built with the blind it was offered on, as the select screen builds it;
    # without, its hand was a placeholder and applying it raised.
    game = _engine()
    game.execute('BOT.start_run({"ORBIT001","Red_Deck"}); api.pump(300)')
    game.execute("G.GAME.round_resets.blind_tags.Small = 'tag_orbital'")
    before = game.eval("(function() local n = 0 for _, h in pairs(G.GAME.hands)"
                       " do n = n + h.level end return n end)()")
    game.execute("api.skip_blind()")
    after = game.eval("(function() local n = 0 for _, h in pairs(G.GAME.hands)"
                      " do n = n + h.level end return n end)()")
    assert after == before + 3


def test_marbles_stone_card_is_made_after_the_verdant_leaf_is_set():
    # set_blind asks every card its debuff before new_round runs the
    # setting_blind context (state_events.lua:333-337), and Card() builds
    # the Stone card `initial`, never asking (card.lua:43-44).
    from jimbot_sim.cards import Enhancement

    game = GameState(seed="AGREE019", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Marble Joker"]))
    game.blind = _on_deck("Verdant Leaf")(game)
    game.step(Action(ActionType.SELECT_BLIND))
    stones = [c for c in game.full_deck if c.enhancement is Enhancement.STONE]
    assert len(stones) == 1 and not stones[0].debuffed
    assert all(c.debuffed for c in game.full_deck if c is not stones[0])


def test_perkeo_draws_its_consumable_oldest_first():
    # pseudorandom_element sorts by sort_id (misc_functions.lua:260).
    from jimbot_sim import consumables as cons

    picks = []
    for order in ((0, 1), (1, 0)):
        game = GameState(seed="AGREE020", deck="Red Deck")
        made = [game.hold_consumable(cons.REGISTRY[n])
                for n in ("The Fool", "Strength")]
        game.consumables = [made[i] for i in order]
        perkeo = JokerInstance(JOKERS["Perkeo"])
        perkeo.spec.on_shop_end(perkeo, game)
        picks.append(game.consumables[-1].name)
    assert picks[0] == picks[1]


def test_a_consumable_bought_is_as_old_as_the_shop_that_stocked_it():
    from jimbot_sim import consumables as cons

    game = _in_a_shop("AGREE021")
    game.money = 100
    game.shop.slots.insert(0, shop_mod.ShopSlot(
        "consumable", 3, consumable=cons.REGISTRY["The Fool"]))
    later = game.hold_consumable(cons.REGISTRY["Strength"])
    game.consumables.append(later)
    game.step(Action(ActionType.BUY, index=0))
    fool = next(c for c in game.consumables if c.name == "The Fool")
    assert fool.uid < later.uid
