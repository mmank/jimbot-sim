"""Matador pays only when the boss's ability actually went off.

card.lua:3719-3729 (joker_main) and card.lua:2735-2745 (debuffed_hand) both
read `G.GAME.blind.triggered`, and nothing else. The simulator paid $8 for
every hand played into any boss, so a Flush of Spades into The Head -- which
debuffs Hearts and has nothing to object to in that hand -- paid eight
dollars a hand.

What the flag is, hand by hand:

  * play_cards_from_highlighted clears it before anything else
    (state_events.lua:455).
  * Blind:press_play (blind.lua:464-507) sets it for The Hook, The Tooth and
    Crimson Heart -- and then evaluate_play calls Blind:debuff_hand
    (state_events.lua:614), which begins `if self.debuff then self.triggered
    = false` (blind.lua:521-522). set_blind makes `self.debuff = blind.debuff
    or {}` (blind.lua:85), and an empty table is true in Lua, so the reset
    runs for every blind that is not disabled. Those three pay only by the
    last rule below.
  * Blind:debuff_hand sets it when the hand is refused -- The Psychic's five
    cards, The Eye's repeat, The Mouth's other hand (blind.lua:527-547) --
    and for The Arm when the hand is above level 1 and The Ox when it is the
    most played hand (blind.lua:549-566).
  * Blind:modify_hand sets it for The Flint (blind.lua:512), which runs only
    for a hand that was not refused.
  * And the scoring loop sets it for every debuffed card in the scoring hand
    (state_events.lua:655-656): a Flush of Clubs into The Club pays.

A refused hand still pays, through the debuffed_hand context -- the simulator
skipped every joker on a refused hand.

Found by the handcrafted policy on the headless engine: 1KM7D44K (The Head)
and PP2UWFOC (The Club) paid $8 for hands with no debuffed scoring card here
and nothing in the game, and SXOBUAJ5 paid $8 for the one hand it played into
The Mouth. The recorded engine answer for "The Club debuffs clubs" (K and Q
of Clubs, a High Card) is $8, and test_differential checks it.
"""

import pytest

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Card, Rank, Suit
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.hands import HandType
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

BY_NAME = {b.name: b for b in BOSSES}


def flush(suit=Suit.SPADES):
    """Five fresh cards, no face card among them: nothing a Plant or a
    Pillar could hold against them, and no state left over from a test that
    played them before."""
    return [Card(Rank.TWO, suit), Card(Rank.FIVE, suit),
            Card(Rank.SEVEN, suit), Card(Rank.NINE, suit),
            Card(Rank.TEN, suit)]


def _boss(name, *jokers):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for joker in jokers:
        game.gain_joker(JokerInstance(JOKERS[joker]))
    game.ante_boss = ""
    game.blind = make_blind(BlindKind.BOSS, game.ante, BY_NAME[name])
    game._start_round()
    game.blind.target = 10 ** 12          # never beaten: stay in the round
    return game


def _play(game, cards):
    """Play exactly these cards; what the hand paid or cost in dollars."""
    game.hand[:] = list(cards) + [Card(Rank.THREE, Suit.DIAMONDS),
                                  Card(Rank.FOUR, Suit.DIAMONDS),
                                  Card(Rank.SIX, Suit.DIAMONDS)]
    # The boss debuffs what is in the deck (_apply_debuffs walks full_deck),
    # so cards made up for the test have to be put there.
    game.full_deck.extend(c for c in game.hand
                          if not any(c is d for d in game.full_deck))
    before = game.money
    game.step(Action(ActionType.PLAY, cards=tuple(range(len(cards)))))
    return game.money - before


@pytest.mark.parametrize("boss", ["The Head", "The Club", "The Window",
                                  "The Plant", "The Wall", "The Needle",
                                  "The Manacle", "The Water"])
def test_nothing_triggered_pays_nothing(boss):
    """A Flush of plain Spades, no face card: nothing for the boss to do."""
    assert _play(_boss(boss, "Matador"), flush()) == 0


@pytest.mark.parametrize("boss, suit", [("The Club", Suit.CLUBS),
                                        ("The Goad", Suit.SPADES),
                                        ("The Head", Suit.HEARTS),
                                        ("The Window", Suit.DIAMONDS)])
def test_a_debuffed_scoring_card_triggers_the_boss(boss, suit):
    """state_events.lua:655-656."""
    assert _play(_boss(boss, "Matador"), flush(suit)) == 8


def test_a_debuffed_high_card_triggers_the_boss():
    """The engine's own case: K and Q of Clubs into The Club pays $8."""
    game = _boss("The Club", "Matador")
    assert _play(game, [Card(Rank.KING, Suit.CLUBS),
                        Card(Rank.QUEEN, Suit.CLUBS)]) == 8


def test_a_face_card_triggers_the_plant():
    game = _boss("The Plant", "Matador")
    assert _play(game, [Card(Rank.KING, Suit.SPADES)]) == 8


def test_a_debuffed_card_that_does_not_score_does_not_trigger():
    """High Card scores only its best card; the debuffed Club rides along."""
    game = _boss("The Club", "Matador")
    hand = [Card(Rank.ACE, Suit.SPADES), Card(Rank.NINE, Suit.CLUBS),
            Card(Rank.SEVEN, Suit.HEARTS), Card(Rank.FIVE, Suit.SPADES),
            Card(Rank.TWO, Suit.HEARTS)]
    assert _play(game, hand) == 0


def test_the_flint_pays():
    assert _play(_boss("The Flint", "Matador"), flush()) == 8


def test_the_hook_does_not_pay_for_its_discard():
    """press_play's flag is wiped by debuff_hand before any joker reads it."""
    assert _play(_boss("The Hook", "Matador"), flush()) == 0


def test_the_tooth_takes_its_dollars_and_matador_pays_nothing():
    assert _play(_boss("The Tooth", "Matador"), flush()) == -5


def test_a_refused_hand_pays_through_the_debuffed_hand_context():
    """The Psychic refuses four cards, and that is the trigger."""
    game = _boss("The Psychic", "Matador")
    assert _play(game, flush()[:4]) == 8
    assert game.chips_scored == 0


def test_the_psychic_does_not_pay_for_five_cards():
    assert _play(_boss("The Psychic", "Matador"), flush()) == 0


def test_the_mouth_pays_only_for_the_hand_it_refuses():
    game = _boss("The Mouth", "Matador")
    assert _play(game, flush()) == 0                    # sets the hand
    pair = [Card(Rank.ACE, Suit.HEARTS), Card(Rank.ACE, Suit.CLUBS)]
    assert _play(game, pair) == 8                       # refused


def test_the_arm_pays_only_above_level_one():
    game = _boss("The Arm", "Matador")
    assert _play(game, flush()) == 0
    game.hand_levels.levels[HandType.FLUSH] = 3
    assert _play(game, flush()) == 8


def test_the_flag_does_not_outlive_its_hand():
    """Cleared at the start of every play, disabled blind or not."""
    game = _boss("The Flint", "Matador")
    assert _play(game, flush()) == 8
    game.blind.disabled = True
    assert _play(game, flush()) == 0


def test_a_debuffed_matador_pays_nothing():
    game = _boss("The Flint", "Matador")
    game.jokers[0].debuffed = True
    assert _play(game, flush()) == 0


def test_a_blueprint_copy_pays_on_a_refused_hand():
    """Blueprint hands the debuffed_hand context straight to the copy
    (card.lua:2305-2317)."""
    game = _boss("The Psychic", "Blueprint", "Matador")
    assert _play(game, flush()[:4]) == 16
