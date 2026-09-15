"""A hand size that grows during a round is dealt into at once.

CardArea:change_size (cardarea.lua:94-111) does not just move the limit. Its
event raises card_limit and then, on cardarea.lua:100-105,

    if delta > 0 and self.config.real_card_limit > 1 and self == G.hand
       and self.cards[1] and (G.STATE == G.STATES.DRAW_TO_HAND
                              or G.STATE == G.STATES.SELECTING_HAND) then
        for i=1, math.abs(delta) do
            draw_card(G.deck, G.hand, ...)             -- the deck's top card
            G.E_MANAGER:add_event(... self:sort() ...)
        end
    end

so |delta| cards come off the top of the deck (CardArea:remove_card takes
the last card of a deck, cardarea.lua:76-77) and the hand is re-sorted. It is
|delta|, not "up to the limit": a hand already over its limit still gets them.
A decrease only lowers the limit (cardarea.lua:98-99); nothing is discarded.

Every joker with a hand size goes through it: Card:add_to_deck calls
change_size for h_size (Juggler, Merry Andy), Turtle Bean, Troubadour and
Stuntman (card.lua:587, 606, 624, 628), and remove_from_deck the reverse
(card.lua:649, 663, 681, 685). The simulator derived hand_size from the row
and only used it for the next refill, so nothing was dealt until a card was
played or discarded.

Seen on U2EBFAQ2, Painted Deck, stake 5, decision 186: the policy sold
Stuntman while selecting a hand, and the game's hand went from 8 cards to 10
(KC and 9S) while the simulator's stayed at 8.

A consumable is different. G.FUNCS.use_card sets G.STATE to PLAY_TAROT
(button_callbacks.lua:2178-2185) and restores it only in an event queued
behind the card's own effect (button_callbacks.lua:2258-2263), so the
change_size event a Judgement or Hex causes runs while the state is
PLAY_TAROT and deals nothing.
"""

from jimbot_sim import shop_pool
from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Edition
from jimbot_sim.consumables import REGISTRY as CONSUMABLES
from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance


def _round(*names, deck="Red Deck"):
    """A run standing in a freshly dealt round, holding these jokers."""
    game = GameState(seed="TESTSEED", deck=deck)
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    assert game.phase is Phase.PLAYING
    return game


def _ids(cards):
    return [id(c) for c in cards]


def _assert_sorted(game):
    before = _ids(game.hand)
    game._sort_hand()
    assert _ids(game.hand) == before, "the hand is re-sorted after the deal"


def test_selling_stuntman_mid_round_deals_two_from_the_top():
    """U2EBFAQ2 decision 186: Painted Deck, 8 cards held, Stuntman sold."""
    game = _round("Stuntman", deck="Painted Deck")
    assert game.hand_size == 8 and len(game.hand) == 8
    top = game.draw_pile[-2:]
    pile = len(game.draw_pile)

    game.step(Action(ActionType.SELL_JOKER, index=0))

    assert game.hand_size == 10
    assert len(game.hand) == 10
    assert len(game.draw_pile) == pile - 2
    assert set(_ids(top)) <= set(_ids(game.hand))
    _assert_sorted(game)


def test_selling_merry_andy_mid_round_deals_one():
    game = _round("Merry Andy")
    assert len(game.hand) == 7
    top = game.draw_pile[-1]

    game.step(Action(ActionType.SELL_JOKER, index=0))

    assert len(game.hand) == 8
    assert any(c is top for c in game.hand)
    _assert_sorted(game)


def test_a_juggler_joining_mid_round_deals_one():
    game = _round()
    top = game.draw_pile[-1]

    game.gain_joker(JokerInstance(JOKERS["Juggler"]))

    assert game.hand_size == 9 and len(game.hand) == 9
    assert any(c is top for c in game.hand)


def test_a_decrease_discards_nothing():
    """cardarea.lua:98-99 only lowers the limit."""
    game = _round()
    game.gain_joker(JokerInstance(JOKERS["Stuntman"]))
    assert game.hand_size == 6
    assert len(game.hand) == 8


def test_the_deal_is_the_delta_not_a_top_up():
    """Over the limit already, the sale still deals both cards."""
    game = _round()
    game.gain_joker(JokerInstance(JOKERS["Stuntman"]))    # limit 6, 8 held
    game.step(Action(ActionType.SELL_JOKER, index=0))    # limit 8
    assert len(game.hand) == 10


def test_a_limit_still_at_one_or_less_deals_nothing():
    """`self.config.real_card_limit > 1` is checked after the change."""
    game = _round()
    for _ in range(4):
        game.gain_joker(JokerInstance(JOKERS["Stuntman"]))
    assert game.hand_size == 0 and len(game.hand) == 8
    game.gain_joker(JokerInstance(JOKERS["Juggler"]))
    assert game.hand_size == 1
    assert len(game.hand) == 8


def test_a_debuffed_joker_leaves_without_dealing():
    """remove_from_deck is guarded by added_to_deck, which a debuff cleared."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    stuntman = JokerInstance(JOKERS["Stuntman"])
    game.gain_joker(stuntman)
    stuntman.debuffed = True
    game._start_round()
    assert len(game.hand) == 8

    game.step(Action(ActionType.SELL_JOKER, index=0))

    assert len(game.hand) == 8


def test_nothing_is_dealt_outside_a_round():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Stuntman"]))
    assert game.phase is Phase.BLIND_SELECT and not game.hand
    game.step(Action(ActionType.SELL_JOKER, index=0))
    assert not game.hand


def test_hex_destroying_stuntman_deals_nothing():
    """Engine, same position: limit 6 -> 8, the hand stays at 8 cards."""
    game = _round()
    game.gain_joker(JokerInstance(JOKERS["Joker"]))
    game.gain_joker(JokerInstance(JOKERS["Stuntman"], edition=Edition.FOIL))
    assert game.hand_size == 6 and len(game.hand) == 8

    game.use_consumable(CONSUMABLES["Hex"], [])

    assert [j.name for j in game.jokers] == ["Joker"]
    assert game.hand_size == 8
    assert len(game.hand) == 8


def test_judgement_making_a_juggler_deals_nothing(monkeypatch):
    """Engine, same position: limit 8 -> 9, the hand stays at 8 cards."""
    monkeypatch.setattr(shop_pool, "draw_joker", lambda *a, **k: "j_juggler")
    game = _round()

    game.use_consumable(CONSUMABLES["Judgement"], [])

    assert [j.name for j in game.jokers] == ["Juggler"]
    assert game.hand_size == 9
    assert len(game.hand) == 8


def test_luchador_against_the_manacle_deals_two_even_over_the_limit():
    """Engine: 8 held under a limit of 7, Luchador sold, 10 held.

    The top-up this used to do for change_size's card gave 9."""
    manacle = next(b for b in BOSSES if b.name == "The Manacle")
    game = _round()
    game.gain_joker(JokerInstance(JOKERS["Luchador"]))
    game.blind = make_blind(BlindKind.BOSS, game.ante, manacle)
    assert game.hand_size == 7 and len(game.hand) == 8

    game.step(Action(ActionType.SELL_JOKER, index=0))

    assert game.hand_size == 8
    assert len(game.hand) == 10


def test_luchador_against_the_manacle_still_deals_two():
    """Blind:disable: change_size(1) deals one, draw_from_deck_to_hand(1)
    another (blind.lua:386-389), so the hand ends one over its limit."""
    manacle = next(b for b in BOSSES if b.name == "The Manacle")
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Luchador"]))
    game.ante_boss = ""
    game.blind = make_blind(BlindKind.BOSS, game.ante, manacle)
    game._start_round()
    assert game.hand_size == 7 and len(game.hand) == 7

    game.step(Action(ActionType.SELL_JOKER, index=0))

    assert game.hand_size == 8
    assert len(game.hand) == 9
