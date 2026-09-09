"""The skip tags, audited the way the jokers, consumables and vouchers were.

The roster was already right -- 24 tags, in the game's pool order, with the
right min_ante and requires -- and the pool drew from all of them. The
mapping from a drawn key to an effect covered only 17.

That gap is worse than a missing field. The pool still drew the tag, the
player still gave up a blind for it, and TAG_BY_KEY.get returned None, so the
reward evaporated with nothing to show it had. Seven tags: Handy, Garbage,
Speed, Top-up, Orbital, Voucher and D6.

Throwback failed the same way from the other end. It reads blinds_skipped,
which was declared on the run and never incremented, so the joker sat at X1
for every run however many blinds were skipped.

Three of the immediate tags read run totals rather than anything about the
round just skipped, which is easy to get wrong in the plausible direction:
Handy counts every hand played this run, Garbage every discard left unspent
at the end of a round, and Speed every blind skipped -- including the one
being skipped now, since skip_blind counts it before handing the tag over.
"""

import pytest

from jimbot_sim.game import (IMMEDIATE_TAGS, TAG_BY_KEY, Action, ActionType,
                          GameState, Phase, Tag)
from jimbot_sim.hands import HandType
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance
from jimbot_sim.tag_data import TAG_DATA


def _run(*tags):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.tags.extend(tags)
    return game


# ------------------------------------------------------------------
# every tag the pool can draw has somewhere to land
# ------------------------------------------------------------------

def test_every_tag_in_the_pool_maps_to_an_effect():
    """The pool draws all twenty-four; a key with no entry is a lost reward."""
    unmapped = [key for key, *_ in TAG_DATA if key not in TAG_BY_KEY]
    assert not unmapped, "drawn by the pool and dropped on the floor: %s" % unmapped


def test_the_mapping_names_no_tag_the_game_does_not_have():
    keys = {key for key, *_ in TAG_DATA}
    assert set(TAG_BY_KEY) <= keys


# ------------------------------------------------------------------
# skipping counts
# ------------------------------------------------------------------

def _skip(game):
    game._next_blind()
    game.phase = Phase.BLIND_SELECT
    game.step(Action(ActionType.SKIP_BLIND))


def test_skipping_a_blind_is_counted():
    """Nothing incremented this, so Throwback read zero all run."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    assert game.blinds_skipped == 0
    _skip(game)
    assert game.blinds_skipped == 1


def test_throwback_scores_more_after_a_skip():
    """X0.25 a skip, read off the run counter every time it scores.

    The counter was never written, so this joker scored X1 for whole runs.
    """
    from jimbot_sim.cards import Card, Rank, Suit

    def scored(skips):
        game = GameState(seed="TESTSEED", deck="Red Deck")
        game.gain_joker(JokerInstance(JOKERS["Throwback"]))
        game.blinds_skipped = skips
        game.hand[:] = [Card(Rank.TWO, Suit.CLUBS)]
        return game.preview_score((0,))

    assert scored(2) > scored(0)
    assert scored(4) > scored(2)

    # and the counter the joker reads is the one a real skip moves
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Throwback"]))
    game.hand[:] = [Card(Rank.TWO, Suit.CLUBS)]
    flat = game.preview_score((0,))
    _skip(game)
    _skip(game)
    game.hand[:] = [Card(Rank.TWO, Suit.CLUBS)]
    assert game.preview_score((0,)) > flat


# ------------------------------------------------------------------
# the tags that pay the instant the blind is skipped
# ------------------------------------------------------------------

def test_handy_pays_a_dollar_for_every_hand_played_this_run():
    game = _run(Tag.HANDY)
    game.hands_played = 7
    before = game.money
    game._fire_immediate_tags()
    assert game.money == before + 7
    assert Tag.HANDY not in game.tags


def test_garbage_pays_for_discards_banked_over_the_whole_run():
    """G.GAME.unused_discards, not this round's leftovers."""
    game = _run(Tag.GARBAGE)
    game.unused_discards = 9
    before = game.money
    game._fire_immediate_tags()
    assert game.money == before + 9


def test_the_discard_meter_banks_at_the_end_of_every_round():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._start_round()
    game.chips_scored = game.blind.target
    left = game.discards_left
    game._beat_blind()
    assert game.unused_discards == left


def test_speed_pays_five_a_skip_and_counts_its_own():
    """skip_blind increments before it hands the tag over."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.blinds_skipped = 3
    game.tags.append(Tag.SPEED)
    before = game.money
    game._fire_immediate_tags()
    assert game.money == before + 15


def test_top_up_makes_two_common_jokers():
    game = _run(Tag.TOP_UP)
    game._fire_immediate_tags()
    assert len(game.jokers) == 2


def test_top_up_makes_only_as_many_as_there_is_room_for():
    """The room is re-checked before each, not once for the pair."""
    game = _run(Tag.TOP_UP)
    while len(game.jokers) < game.joker_slots - 1:
        game.gain_joker(JokerInstance(JOKERS["Joker"]))
    before = len(game.jokers)
    game._fire_immediate_tags()
    assert len(game.jokers) == before + 1


def test_orbital_levels_one_hand_by_three():
    game = _run(Tag.ORBITAL)
    game._fire_immediate_tags()
    levelled = [h for h in HandType if game.hand_levels.levels[h] != 1]
    assert len(levelled) == 1
    assert game.hand_levels.levels[levelled[0]] == 4


def test_orbital_never_names_a_hand_the_run_has_not_seen():
    """Same visible-hands pool as To Do List: nine until a secret hand lands."""
    from jimbot_sim.hands import SECRET_HANDS

    seen = set()
    for seed in ("A", "B", "C", "D", "E", "F", "G", "H"):
        game = GameState(seed=seed, deck="Red Deck")
        game.tags.append(Tag.ORBITAL)
        game._fire_immediate_tags()
        named = next(h for h in HandType if game.hand_levels.levels[h] != 1)
        assert named not in SECRET_HANDS
        seen.add(named)
    assert len(seen) > 1, "eight seeds and it named one hand every time"


def test_a_double_tag_levels_the_same_hand_twice():
    """G.orbital_hand is handed to the copy, so both name one hand.

    Remembering the choice per ante and blind is what reproduces that.
    """
    game = _run(Tag.ORBITAL, Tag.ORBITAL)
    game._fire_immediate_tags()
    levelled = [h for h in HandType if game.hand_levels.levels[h] != 1]
    assert len(levelled) == 1
    assert game.hand_levels.levels[levelled[0]] == 7


# ------------------------------------------------------------------
# the two that wait for the shop
# ------------------------------------------------------------------

def test_a_voucher_tag_puts_a_second_voucher_in_the_shop():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._roll_voucher()
    game.tags.append(Tag.VOUCHER)
    game._open_shop()
    offered = game.shop.vouchers_on_offer()
    assert len(offered) == 2
    assert offered[0].key != offered[1].key, "the shop must not repeat one"


def test_both_shop_vouchers_can_be_bought():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._roll_voucher()
    game.tags.append(Tag.VOUCHER)
    game._open_shop()
    game.money = 100
    first, second = game.shop.vouchers_on_offer()

    game.step(Action(ActionType.BUY_VOUCHER, index=0))
    assert first in game.vouchers
    assert game.shop.vouchers_on_offer() == [second]

    game.step(Action(ActionType.BUY_VOUCHER, index=0))
    assert second in game.vouchers
    assert game.shop.vouchers_on_offer() == []


def test_a_d6_tag_makes_the_first_reroll_free_and_the_next_cheap():
    """temp_reroll_cost = 0: the price starts at nothing and climbs as usual.

    Not the same as Chaos the Clown, which is one spare reroll that does not
    move the price at all.
    """
    plain = GameState(seed="TESTSEED", deck="Red Deck")
    plain._open_shop()
    assert plain.shop.reroll_cost() == 5

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.tags.append(Tag.D_SIX)
    game._open_shop()
    assert game.shop.reroll_cost() == 0
    game.shop.rerolls += 1
    assert game.shop.reroll_cost() == 1


def test_the_shop_tags_are_spent_when_they_fire():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._roll_voucher()
    game.tags.extend([Tag.VOUCHER, Tag.D_SIX])
    game._open_shop()
    assert Tag.VOUCHER not in game.tags
    assert Tag.D_SIX not in game.tags


def test_the_immediate_list_holds_only_tags_that_pay_on_the_skip():
    assert set(IMMEDIATE_TAGS) == {Tag.HANDY, Tag.GARBAGE, Tag.SPEED,
                                   Tag.TOP_UP, Tag.ORBITAL}


# ------------------------------------------------------------------
# stacking, which is not a corner case
# ------------------------------------------------------------------

def test_every_double_tag_copies_the_next_one_not_just_the_first():
    """add_tag walks the whole list firing tag_add and never breaks.

    Marcin's route to a stack: sell a pile of Diet Colas, each of which
    leaves a free Double Tag behind. The Anaglyph Deck gets there without
    any jokers at all -- a Double every time a boss falls.
    """
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.tags.extend([Tag.DOUBLE] * 4)
    game.add_tag_by_key("tag_voucher")
    assert game.tags.count(Tag.VOUCHER) == 5
    assert Tag.DOUBLE not in game.tags, "all four are spent, not one"


def test_a_double_tag_does_not_copy_another_double_tag():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.tags.append(Tag.DOUBLE)
    game.add_tag_by_key("tag_double")
    assert game.tags.count(Tag.DOUBLE) == 2


def test_the_anaglyph_deck_hands_over_a_double_tag_after_each_boss():
    game = GameState(seed="TESTSEED", deck="Anaglyph Deck")
    game.blind_index = 2
    game._next_blind()
    game._start_round()
    game.chips_scored = game.blind.target
    game._beat_blind()
    assert Tag.DOUBLE in game.tags


def test_a_shop_can_hold_an_arbitrary_number_of_vouchers():
    """One row, one card limit, raised by one for each Voucher Tag held."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._roll_voucher()
    game.tags.extend([Tag.VOUCHER] * 5)
    game._open_shop()

    offered = game.shop.vouchers_on_offer()
    assert len(offered) == 6, "the round's own plus one per tag"
    keys = [v.key for v in offered]
    assert len(set(keys)) == len(keys), "each draw withholds the row so far"
    assert Tag.VOUCHER not in game.tags


def test_buying_from_a_long_voucher_row_takes_the_right_one():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._roll_voucher()
    game.tags.extend([Tag.VOUCHER] * 3)
    game._open_shop()
    game.money = 100

    wanted = game.shop.vouchers_on_offer()[2]
    game.step(Action(ActionType.BUY_VOUCHER, index=2))
    assert wanted in game.vouchers
    assert wanted not in game.shop.vouchers_on_offer()
    assert len(game.shop.vouchers_on_offer()) == 3


def test_juggle_tags_stack_and_last_one_round():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    plain = game.hand_size
    game.tags.extend([Tag.JUGGLE] * 3)
    game._next_blind()
    game._start_round()
    assert game.hand_size == plain + 9, "three tags, nine cards"

    game.chips_scored = game.blind.target
    game._beat_blind()
    assert game.hand_size == plain, "and they are handed back at round end"


def test_every_investment_tag_pays():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.blind_index = 2
    game._next_blind()
    game._start_round()
    game.tags.extend([Tag.INVESTMENT] * 3)
    game.chips_scored = game.blind.target
    before = game.money
    game._beat_blind()
    # A cash-out row, not money the moment the boss falls.
    assert game.money == before
    assert game.pending_payout >= 75


def test_uncommon_tags_stack_across_the_shop_slots():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._roll_voucher()
    game.tags.extend([Tag.UNCOMMON] * 2)
    game._open_shop()
    # Free by coupon rather than by price: set_cost zeroes a couponed
    # card after working the price out, and the tag's joker is couponed.
    free = [s for s in game.shop.slots
            if s.kind == "joker" and game.slot_price(s) == 0]
    assert len(free) == 2
    assert Tag.UNCOMMON not in game.tags


# ------------------------------------------------------------------
# the Anaglyph + Negative Tag engine
# ------------------------------------------------------------------

def test_a_negative_tag_makes_a_shop_joker_negative_and_free():
    from jimbot_sim.cards import Edition

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.tags.append(Tag.NEGATIVE)
    game._roll_voucher()
    game._open_shop()

    marked = [s for s in game.shop.slots
              if s.joker is not None and s.joker.edition is Edition.NEGATIVE]
    assert len(marked) == 1
    assert game.slot_price(marked[0]) == 0, "the tag coupons it as well"


def test_a_negative_joker_does_not_take_a_slot():
    from jimbot_sim.cards import Edition

    game = GameState(seed="TESTSEED", deck="Red Deck")
    base = game.joker_slots
    game.gain_joker(JokerInstance(JOKERS["Joker"], edition=Edition.NEGATIVE))
    assert game.joker_slots == base + 1
    assert len(game.jokers) == 1


def test_a_debuffed_negative_joker_keeps_its_slot():
    """remove_from_deck(from_debuff) sets queue_negative_removal instead.

    The negative block is the one thing a debuff deliberately does not undo,
    which is why joker_slots counts every joker rather than the active ones.
    """
    from jimbot_sim.cards import Edition

    game = GameState(seed="TESTSEED", deck="Red Deck")
    base = game.joker_slots
    joker = JokerInstance(JOKERS["Joker"], edition=Edition.NEGATIVE)
    game.gain_joker(joker)
    joker.debuffed = True
    assert game.joker_slots == base + 1


def test_the_anaglyph_negative_loop_compounds():
    """Marcin: people end up with an absurd number of jokers this way.

    A boss falls, Anaglyph leaves a Double Tag, the next Negative Tag is
    doubled, each copy marks a shop joker negative and free, and each
    negative joker bought raises the joker limit -- so the row grows without
    ever spending a slot. Tags that find no joker in the shop wait for the
    next one rather than being lost.
    """
    from jimbot_sim.cards import Edition

    game = GameState(seed="TESTSEED", deck="Anaglyph Deck")
    game.money = 500
    base = game.joker_slots

    for _ in range(5):
        game.blind_index = 2
        game._next_blind()
        game._start_round()
        game.chips_scored = game.blind.target
        game._beat_blind()
        game.add_tag_by_key("tag_negative")
        game._roll_voucher()
        game._open_shop()
        for i in range(len(game.shop.slots) - 1, -1, -1):
            slot = game.shop.slots[i]
            if (slot.joker is not None
                    and slot.joker.edition is Edition.NEGATIVE
                    and len(game.jokers) < game.joker_slots):
                game.step(Action(ActionType.BUY, index=i))
        game.shop = None

    assert game.joker_slots > base, "the limit never moved"
    assert len(game.jokers) == game.joker_slots - base, (
        "every joker bought was negative, so none of them cost a slot")
    assert game.tags.count(Tag.NEGATIVE) > 0, (
        "tags with no joker to mark should wait, not evaporate")


def test_perkeos_copy_does_not_take_a_consumable_slot():
    """The same mechanism as the negative joker.

    add_to_deck raises G.consumeables' card limit for a negative consumable
    exactly as it raises the joker limit for a negative joker, and Perkeo's
    whole point is that its copy is free. The row used to hold shared
    registry entries, so there was nowhere to record the edition and the copy
    took a slot like any other card.
    """
    from jimbot_sim.cards import Edition
    from jimbot_sim.consumables import REGISTRY as CONSUMABLES

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Perkeo"]))
    game.consumables.append(game.hold_consumable(CONSUMABLES["The Fool"]))
    base = game.consumable_slots

    game._leave_shop()
    assert len(game.consumables) == 2
    assert game.consumable_slots == base + 1
    assert [c.edition for c in game.consumables] == [Edition.NONE,
                                                     Edition.NEGATIVE]


def test_perkeo_copies_into_a_row_that_is_already_full():
    """A full row is exactly when the copy being free matters."""
    from jimbot_sim.consumables import REGISTRY as CONSUMABLES

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Perkeo"]))
    while len(game.consumables) < game.consumable_slots:
        game.consumables.append(game.hold_consumable(CONSUMABLES["The Fool"]))
    held = len(game.consumables)

    game._leave_shop()
    assert len(game.consumables) == held + 1


def test_using_the_negative_copy_gives_the_slot_back():
    """remove_from_deck lowers the limit again, so the credit is not permanent."""
    from jimbot_sim.consumables import REGISTRY as CONSUMABLES

    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Perkeo"]))
    game.consumables.append(game.hold_consumable(CONSUMABLES["The Fool"]))
    base = game.consumable_slots
    game._leave_shop()
    assert game.consumable_slots == base + 1

    negative = next(i for i, c in enumerate(game.consumables)
                    if c.edition.value == "negative")
    game.consumables.pop(negative)
    assert game.consumable_slots == base
