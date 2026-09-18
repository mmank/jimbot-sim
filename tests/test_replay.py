"""The one replayer: a recorded action is the same move on every backend.

ops/sim_replay.py and scripts/record_replay.py each translated a recording on
their own -- one into simulator Actions, one into client calls -- with their
own merge of the buy-and-use click, their own comparison and their own waits.
`jimbot_sim.replay` is both now; tests/test_recordings.py replays every
recording through it on the simulator, and these pin its parts.
"""

import pathlib

from jimbot_sim.game import Action, ActionType
from jimbot_sim.replay import (Move, Recording, differences, merge_buy_and_use,
                               replay, to_move)
from jimbot_sim.run import SimRun

RECORDINGS = pathlib.Path(__file__).resolve().parents[1] / "recordings"


def _entry(action, **params):
    return {"action": action, "params": params}


def _key(row):
    return row["key"]


# -- the merge ----------------------------------------------------------------

def test_a_buy_and_its_use_are_one_press():
    actions = [_entry("buy_from_shop", area="shop_jokers", index=1,
                      key="c_hermit"),
               _entry("use_card", area="?", key="c_hermit"),
               _entry("toggle_shop")]
    merged = merge_buy_and_use(actions)
    assert [a["action"] for a in merged] == ["buy_from_shop", "toggle_shop"]
    assert merged[0]["params"]["buy_and_use"] == 1


def test_a_newer_recordings_buy_and_use_reads_the_same():
    merged = merge_buy_and_use([_entry("buy_and_use", area="shop_jokers",
                                       index=2, key="c_hermit")])
    assert merged[0]["action"] == "buy_from_shop"
    assert merged[0]["params"]["buy_and_use"] == 1


# -- a recorded action as a move ----------------------------------------------

def test_positions_become_indices():
    move = to_move(_entry("play_cards_from_highlighted", cards=[1, 3, 4]),
                   {}, _key)
    assert move == Move(Action(ActionType.PLAY, cards=(0, 2, 3)))
    assert to_move(_entry("sell_card", area="consumeables", index=2),
                   {}, _key) == Move(Action(ActionType.SELL_CONSUMABLE,
                                            index=1))


def test_a_sort_is_a_button_not_an_action():
    assert to_move(_entry("sort_hand_suit"), {}, _key) == Move(sort="suit")


def test_the_card_bought_is_checked_before_it_is_bought():
    shop = {"shop": [{"area": "shop_jokers", "index": 1, "key": "j_joker"},
                     {"area": "shop_jokers", "index": 2, "key": "card"}]}
    wrong = to_move(_entry("buy_from_shop", area="shop_jokers", index=1,
                           key="j_banner"), shop, _key)
    assert isinstance(wrong, str) and "j_banner" in wrong
    # A playing card is named by its centre in the recording.
    move = to_move(_entry("buy_from_shop", area="shop_jokers", index=2,
                          key="m_glass"), shop, _key)
    assert move == Move(Action(ActionType.BUY, index=1))


def test_a_voucher_is_found_by_what_was_redeemed():
    """A Voucher Tag puts a second voucher beside the round's own."""
    shop = {"shop": [{"area": "shop_vouchers", "index": 1, "key": "v_hone"},
                     {"area": "shop_vouchers", "index": 2,
                      "key": "v_overstock_norm"}]}
    move = to_move(_entry("use_card", area="shop_vouchers", index=1,
                          key="v_overstock_norm"), shop, _key)
    assert move == Move(Action(ActionType.BUY_VOUCHER, index=1))


def test_the_state_is_read_only_for_a_purchase():
    def state():
        raise AssertionError("read for a play")
    to_move(_entry("discard_cards_from_highlighted", cards=[2]), state, _key)


# -- the comparison -------------------------------------------------------------

def test_chips_are_not_compared_mid_animation():
    recorded = {"phase": "SHOP", "chips": 450, "dollars": 7}
    assert differences(recorded, {"chips": 0, "dollars": 7}) == []
    playing = dict(recorded, phase="SELECTING_HAND")
    assert differences(playing, {"chips": 0, "dollars": 7})


def test_a_field_the_backend_cannot_report_is_not_a_difference():
    """The simulator numbers its own cards, so its snapshot has no ids."""
    assert differences({"hand_ids": [3, 1, 2], "dollars": 4},
                       {"dollars": 4}) == []


def test_ids_are_compared_by_their_order():
    drifted = {"phase": "SHOP", "joker_ids": [68, 53, 54, 136]}
    assert differences(drifted, {"joker_ids": [68, 53, 54, 83]}) == []
    assert differences(drifted, {"joker_ids": [53, 68, 54, 83]})


# -- the replay -------------------------------------------------------------

def test_a_replay_can_stop_part_way_without_comparing():
    recording = Recording.load(RECORDINGS / "1.json")
    run = SimRun(endless=True)
    result = replay(run, recording, check=False, stop_at=20)
    assert result.problem is None and result.reached == 20
    assert len(recording.actions) > 20
    assert run.game.round_number >= 1
