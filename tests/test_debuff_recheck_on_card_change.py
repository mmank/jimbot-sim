"""A card changed mid-round is asked Blind:debuff_card again, then and there.

Every write to a playing card's identity ends by re-running the boss's debuff
test on that one card:

  * Card:set_ability -- The Lovers, The Chariot, Justice, The Devil, The
    Tower, Midas Mask, Vampire -- ends `if not initial then
    G.GAME.blind:debuff_card(self) end` (card.lua:365);
  * Card:set_base -- Strength, Sigil, Ouija -- ends with the same line
    (card.lua:143);
  * Card:change_suit -- The Star, The Moon, The Sun, The World -- calls it
    unconditionally (card.lua:561);
  * copy_card -- Death -- writes `new_card.debuff = other.debuff`
    (common_events.lua:2178), so the left card takes the right card's flag.

Blind:debuff_card asks `card:is_suit(self.debuff.suit, true)` (blind.lua:626),
and that is true of any Wild card (card.lua:4081) -- so The Lovers under The
Window debuffs the card it makes, at once, and a card turned out of Diamonds
is released at once. The simulator only re-evaluated debuffs at the start of
the round and before a play, so a discard in between saw the old flags.

Found by the handcrafted policy on the headless engine: 5LPYZ3QU, Magic Deck,
stake 1, ante 6, The Window. The Lovers made the Eight of Clubs Wild and the
policy discarded it with two other Clubs; Castle (Clubs that round) went to
+6 in the game, which skipped the debuffed Wild, and +9 here.
"""

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.consumables import REGISTRY as CONSUMABLES
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

BY_NAME = {b.name: b for b in BOSSES}


def _round(boss, hand, *jokers):
    """A boss round holding exactly `hand`, debuffed as the boss would."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in jokers:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game.ante_boss = ""
    game.blind = make_blind(BlindKind.BOSS, game.ante, BY_NAME[boss])
    game._start_round()
    game.blind.target = 10 ** 12          # never beaten: stay in the round
    game.hand[:] = list(hand)
    game.full_deck.extend(hand)
    game._apply_debuffs()
    return game


def _castle(game):
    return next(j.counter for j in game.jokers if j.name == "Castle")


def _discard(game, cards):
    game._discard(tuple(game.hand.index(c) for c in cards))


def test_the_lovers_under_the_window_debuffs_the_wild_card_it_makes():
    """card.lua:1143 set_ability -> card.lua:365 -> blind.lua:626, and
    card.lua:4081 makes a Wild card a Diamond. Castle skips it
    (card.lua:2815). The 5LPYZ3QU stop, reduced."""
    eight, six, five = (Card(Rank.EIGHT, Suit.CLUBS), Card(Rank.SIX, Suit.CLUBS),
                        Card(Rank.FIVE, Suit.CLUBS))
    game = _round("The Window", [Card(Rank.TEN, Suit.HEARTS), eight, six, five,
                                 Card(Rank.TWO, Suit.SPADES)], "Castle")
    game.castle_suit = Suit.CLUBS
    assert not eight.debuffed
    game.use_consumable(CONSUMABLES["The Lovers"], [eight])
    assert eight.enhancement is Enhancement.WILD
    assert eight.debuffed
    _discard(game, [game.hand[0], eight, six, five])
    assert _castle(game) == 6.0


def test_the_star_debuffs_a_card_it_turns_into_a_diamond():
    """card.lua:561: change_suit re-runs debuff_card."""
    club = Card(Rank.NINE, Suit.CLUBS)
    game = _round("The Window", [club, Card(Rank.TWO, Suit.SPADES)], "Castle")
    game.castle_suit = Suit.DIAMONDS
    game.use_consumable(CONSUMABLES["The Star"], [club])
    assert club.suit is Suit.DIAMONDS and club.debuffed
    _discard(game, [club])
    assert _castle(game) == 0.0


def test_the_sun_releases_a_diamond_it_turns_into_a_heart():
    """card.lua:561, the other way: blind.lua:653 set_debuff(false)."""
    diamond = Card(Rank.NINE, Suit.DIAMONDS)
    game = _round("The Window", [diamond, Card(Rank.TWO, Suit.SPADES)], "Castle")
    game.castle_suit = Suit.HEARTS
    assert diamond.debuffed
    game.use_consumable(CONSUMABLES["The Sun"], [diamond])
    assert diamond.suit is Suit.HEARTS and not diamond.debuffed
    _discard(game, [diamond])
    assert _castle(game) == 3.0


def test_the_tower_releases_a_diamond_it_turns_to_stone():
    """card.lua:365, then blind.lua:626 -- a Stone card is no suit
    (card.lua:4078) -- so the card falls through to set_debuff(false)."""
    diamond = Card(Rank.NINE, Suit.DIAMONDS)
    game = _round("The Window", [diamond, Card(Rank.TWO, Suit.SPADES)])
    assert diamond.debuffed
    game.use_consumable(CONSUMABLES["The Tower"], [diamond])
    assert not diamond.debuffed


def test_strength_under_the_plant_debuffs_a_ten_it_makes_a_jack():
    """card.lua:1128 set_base -> card.lua:143 -> blind.lua:630."""
    ten, king = Card(Rank.TEN, Suit.SPADES), Card(Rank.KING, Suit.HEARTS)
    game = _round("The Plant", [ten, king, Card(Rank.TWO, Suit.CLUBS)])
    assert not ten.debuffed and king.debuffed
    game.use_consumable(CONSUMABLES["Strength"], [ten])
    assert ten.rank is Rank.JACK and ten.debuffed
    game.use_consumable(CONSUMABLES["Strength"], [king])
    assert king.rank is Rank.ACE and not king.debuffed


def test_sigil_rechecks_every_card_in_hand():
    """card.lua:1242 set_base on every held card, whichever suit is drawn."""
    hand = [Card(Rank.NINE, Suit.DIAMONDS), Card(Rank.FOUR, Suit.CLUBS),
            Card(Rank.SIX, Suit.HEARTS)]
    game = _round("The Window", hand)
    game.use_consumable(CONSUMABLES["Sigil"], [])
    suit = hand[0].suit
    assert all(c.suit is suit for c in hand)
    assert [c.debuffed for c in hand] == [suit is Suit.DIAMONDS] * 3


def test_ouija_rechecks_every_card_in_hand():
    """card.lua:1256 set_base on every held card, whichever rank is drawn."""
    hand = [Card(Rank.KING, Suit.DIAMONDS), Card(Rank.FOUR, Suit.CLUBS)]
    game = _round("The Plant", hand)
    game.use_consumable(CONSUMABLES["Ouija"], [])
    rank = hand[0].rank
    assert all(c.rank is rank for c in hand)
    assert [c.debuffed for c in hand] == [rank.is_face] * 2


def test_death_copies_the_right_cards_debuff():
    """common_events.lua:2178, `new_card.debuff = other.debuff`."""
    left, right = Card(Rank.FIVE, Suit.CLUBS), Card(Rank.NINE, Suit.DIAMONDS)
    game = _round("The Window", [left, right])
    assert right.debuffed and not left.debuffed
    game.use_consumable(CONSUMABLES["Death"], [left, right])
    assert left.suit is Suit.DIAMONDS and left.debuffed

    left, right = Card(Rank.NINE, Suit.DIAMONDS), Card(Rank.FIVE, Suit.CLUBS)
    game = _round("The Window", [left, right])
    game.use_consumable(CONSUMABLES["Death"], [left, right])
    assert left.suit is Suit.CLUBS and not left.debuffed


def test_no_boss_no_debuff():
    """Out of a boss round the blind has no debuff table (blind.lua:85), and
    debuff_card ends set_debuff(false) (blind.lua:653)."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._start_round()
    card = game.hand[0]
    card.suit = Suit.DIAMONDS
    game.use_consumable(CONSUMABLES["The Lovers"], [card])
    assert card.enhancement is Enhancement.WILD and not card.debuffed
