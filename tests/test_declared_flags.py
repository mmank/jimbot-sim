"""The flags JokerSpec declares, checked against what the game does with them.

test_registry_honesty proves each of these has a reader. That is a weaker
claim than it sounds, and this file is the audit that followed: the name
appearing somewhere says nothing about the reader being right. Four of the
thirteen were wrong in a way that changed play.

The recurring one is *when* an effect lands. A joker's contribution to hand
size, hands, discards, free rerolls, interest and the debt floor is written by
Card:add_to_deck when it is bought and unwritten by remove_from_deck when it
goes -- these are run-level counters, not per-round sums, and set_debuff calls
remove_from_deck, so a debuffed joker's counter comes off with it. Measured on
the engine, every one of ten counters read the same debuffed as with no joker
at all.

Burglar is the exception that gives the rule its teeth: it is *not* in
add_to_deck. It is ease_hands_played(+3) on setting_blind, which runs after
the blind has decided the allowance, so it stacks on top of a boss that
dictates one.
"""

import pytest

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.hands import HANDLIST, SECRET_HANDS, HandType
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

BY_NAME = {b.name: b for b in BOSSES}


def _run(*names, boss=None):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    if boss is not None:
        game.ante_boss = ""
        game.blind = make_blind(BlindKind.BOSS, game.ante, BY_NAME[boss])
    game._start_round()
    return game


# ------------------------------------------------------------------
# Burglar: after the boss, not before
# ------------------------------------------------------------------

def test_burglar_gives_three_hands_and_takes_every_discard():
    """Engine: 4 hands and 4 discards becomes 7 and 0."""
    game = _run("Burglar")
    assert (game.hands_left, game.discards_left) == (7, 0)


def test_two_burglars_stack():
    """Engine: 10."""
    assert _run("Burglar", "Burglar").hands_left == 10


def test_burglar_beats_a_boss_that_dictates_the_allowance():
    """The whole point of the timing. Engine: The Needle plus a Burglar is 4.

    The Needle allows one hand. Burglar adds its three afterwards, on
    setting_blind, so the round has four. Folding the +3 into the base
    allowance let The Needle overwrite the lot and the run played one.
    """
    assert _run(boss="The Needle").hands_left == 1
    assert _run("Burglar", boss="The Needle").hands_left == 4


def test_burglar_still_empties_the_discards_a_drunkard_added():
    """Engine: Burglar with a Drunkard is 7 hands and 0 discards."""
    game = _run("Burglar", "Drunkard")
    assert (game.hands_left, game.discards_left) == (7, 0)


# ------------------------------------------------------------------
# the counters that come off with a debuff
# ------------------------------------------------------------------

@pytest.mark.parametrize("name,attr,delta", [
    ("Juggler", "hand_size", 1),
    ("Merry Andy", "hand_size", -1),
    ("Stuntman", "hand_size", -2),
    ("Turtle Bean", "hand_size", 5),
    ("Credit Card", "spendable", 20),
])
def test_a_debuffed_joker_contributes_nothing(name, attr, delta):
    """Ten of these were measured; each read the same debuffed as absent."""
    plain = getattr(GameState(seed="TESTSEED", deck="Red Deck"), attr)

    held = _run(name)
    assert getattr(held, attr) == plain + delta

    held.jokers[0].debuffed = True
    assert getattr(held, attr) == plain


def test_a_debuffed_drunkard_gives_no_discard():
    assert _run("Drunkard").discards_left == 5
    game = _run("Drunkard")
    game.jokers[0].debuffed = True
    game.hands_left, game.discards_left = game._round_allowance()
    assert game.discards_left == 4


# ------------------------------------------------------------------
# Credit Card
# ------------------------------------------------------------------

def test_credit_card_buys_what_the_run_cannot_pay_for():
    """Engine at $0: one card allows a $20 buy and refuses $21."""
    game = _run("Credit Card")
    game.money = 0
    assert game.affords(20)
    assert not game.affords(21)


def test_credit_cards_stack():
    """Engine at $0: two allow $40 and refuse $41."""
    game = _run("Credit Card", "Credit Card")
    game.money = 0
    assert game.affords(40)
    assert not game.affords(41)


def test_a_run_with_no_credit_may_not_go_into_debt():
    game = _run()
    game.money = 4
    assert game.affords(4)
    assert not game.affords(5)


def test_something_free_is_always_takeable():
    """`(cost > dollars - bankrupt_at) and (cost > 0)` -- the second half."""
    game = _run()
    game.money = 0
    assert game.affords(0)


# ------------------------------------------------------------------
# To Do List
# ------------------------------------------------------------------

def test_two_to_do_lists_name_two_hands():
    """ability.to_do_poker_hand is on the joker, not on the run.

    Holding it on the run made the second copy overwrite the first, so a pair
    of them paid out together or not at all.
    """
    game = _run("To Do List", "To Do List")
    seen = set()
    for _ in range(20):
        game._reroll_todo_hands()
        seen.add(tuple(j.named_hand for j in game.jokers))
    assert any(a is not b for a, b in seen), "they never disagreed once"


def test_a_to_do_list_never_names_the_same_hand_twice_running():
    game = _run("To Do List")
    joker = game.jokers[0]
    previous = joker.named_hand
    for _ in range(30):
        game._reroll_todo_hands()
        assert joker.named_hand is not previous
        previous = joker.named_hand


def test_the_secret_hands_are_not_in_the_pool_until_they_are_played():
    game = _run("To Do List")
    assert set(game.visible_hands) == set(HandType) - SECRET_HANDS
    assert len(game.visible_hands) == 9

    game.hand_levels.plays[HandType.FLUSH_HOUSE] += 1
    assert HandType.FLUSH_HOUSE in game.visible_hands
    assert len(game.visible_hands) == 10


def test_the_pool_is_in_the_game_s_stated_order():
    """HANDLIST, strongest first -- see the note in hands.py on why."""
    game = _run()
    assert game.visible_hands == [h for h in HANDLIST if h not in SECRET_HANDS]


# ------------------------------------------------------------------
# Telescope's tie-break
# ------------------------------------------------------------------

def test_telescope_breaks_a_tie_towards_the_stronger_hand():
    """ipairs(G.handlist) with a strict >, and handlist runs strongest first.

    Python's max over a dict keyed in enum order gave the weakest instead, so
    a run that had played one Pair and one Two Pair had its Celestial pack
    forced to the wrong planet.
    """
    game = _run()
    game.hand_levels.plays[HandType.PAIR] = 1
    game.hand_levels.plays[HandType.TWO_PAIR] = 1
    assert game._most_played_planet() == "c_uranus"       # Two Pair


def test_telescope_still_prefers_the_hand_actually_played_most():
    game = _run()
    game.hand_levels.plays[HandType.PAIR] = 5
    game.hand_levels.plays[HandType.TWO_PAIR] = 1
    assert game._most_played_planet() == "c_mercury"      # Pair
