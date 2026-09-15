"""To Do List names its hand the moment the card is made, not a round later.

Card:set_ability (card.lua:311-322) runs from Card:init for every card built,
and for a To Do List it draws

    self.ability.to_do_poker_hand = pseudorandom_element(_poker_hands,
                                                         pseudoseed('to_do'))

from every visible hand. So a To Do List in a shop, a Buffoon pack or out of
Judgement already names a hand, and pays for it in the round it is bought.
The simulator only rolled the hand at the end of a round, which left a newly
made one on None -- it paid nothing until the next round -- and it never took
the creation draw, so every later end-of-round roll (card.lua:2975-2980, the
same 'to_do' stream) landed on a different hand from the game's. Every card
built counts, bought or not: the shop builds its shelf.

copy_card (functions/common_events.lua:2156-2166) goes through set_ability
too, so an Ankh copy spends a draw -- and then copies the ability table over
it, keeping the original's hand.

Found by the handcrafted policy on the headless engine: 1UBSYCCB Green Deck
stake 8 paid $4 for a High Card in the game and nothing here, and 3DZ2RTPJ,
9WJUQS3A, S7HCTJIW and O8X8PJ5H came apart on the same joker.
"""

from jimbot_sim import shop_pool
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

SEED = "TODOSEED"


def _pair():
    """Two identical runs: one to act on, one to read the stream from."""
    return GameState(seed=SEED, deck="Red Deck"), GameState(seed=SEED,
                                                             deck="Red Deck")


def _todo(game):
    return [j for j in game.jokers if j.name == "To Do List"]


def test_a_pack_to_do_list_names_a_hand_straight_away():
    game, twin = _pair()
    joker = game._pack_card({"set": "Joker", "key": "j_todo_list",
                             "edition": "none"})
    expected = twin.rng.random_element(twin.visible_hands, "to_do")
    assert joker.named_hand is not None
    assert joker.named_hand is expected


def test_the_round_end_roll_follows_the_creation_draw():
    """One stream: creation, then the reroll that excludes the current hand."""
    game, twin = _pair()
    game.gain_joker(game._pack_card({"set": "Joker", "key": "j_todo_list",
                                     "edition": "none"}))
    game._reroll_todo_hands()

    first = twin.rng.random_element(twin.visible_hands, "to_do")
    second = twin.rng.random_element(
        [h for h in twin.visible_hands if h is not first], "to_do")
    assert _todo(game)[0].named_hand is second


def test_a_shop_to_do_list_names_a_hand(monkeypatch):
    monkeypatch.setattr(shop_pool, "draw_shop_card",
                        lambda *a, **k: ("Joker", "j_todo_list"))
    game, twin = _pair()
    slot = game._roll_slot()
    assert slot.joker is not None and slot.joker.name == "To Do List"
    assert slot.joker.named_hand is twin.rng.random_element(
        twin.visible_hands, "to_do")


def test_a_judgement_to_do_list_names_a_hand(monkeypatch):
    monkeypatch.setattr(shop_pool, "draw_joker",
                        lambda *a, **k: "j_todo_list")
    game, twin = _pair()
    game.add_random_joker("Judgement", append="jud")
    assert _todo(game)[0].named_hand is twin.rng.random_element(
        twin.visible_hands, "to_do")


def test_an_ankh_copy_spends_a_draw_and_keeps_the_original_hand():
    game, twin = _pair()
    original = JokerInstance(JOKERS["To Do List"])
    original.named_hand = game.visible_hands[3]
    game.gain_joker(original)

    game.add_joker_copy(original, "Ankh")
    copy = _todo(game)[1]
    assert copy.named_hand is original.named_hand

    # The copy's set_ability draw is spent, so the next roll is the second
    # draw of the stream, not the first.
    twin.rng.random_element(twin.visible_hands, "to_do")
    pool = [h for h in twin.visible_hands if h is not original.named_hand]
    expected = twin.rng.random_element(pool, "to_do")
    game._reroll_todo_hands()
    assert _todo(game)[0].named_hand is expected


def test_other_jokers_take_no_to_do_draw():
    game, twin = _pair()
    game._pack_card({"set": "Joker", "key": "j_joker", "edition": "none"})
    game.gain_joker(game._pack_card({"set": "Joker", "key": "j_todo_list",
                                     "edition": "none"}))
    assert _todo(game)[0].named_hand is twin.rng.random_element(
        twin.visible_hands, "to_do")
