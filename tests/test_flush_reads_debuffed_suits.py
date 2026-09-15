"""A debuffed card still counts towards a flush by its printed suit.

The game asks its suit question two ways (card.lua:4064). The ordinary one
refuses a debuffed card outright; the one a flush is judged on,
`is_suit(suit, nil, true)`, reads the printed suit anyway, and only a Wild
card loses its every-suit to a debuff:

    if flush_calc then
        if self.ability.effect == 'Stone Card' then return false end
        if self.ability.name == "Wild Card" and not self.debuff then return true end
        if next(find_joker('Smeared Joker')) and ... then return true end
        return self.base.suit == suit

Hand detection used the ordinary question, so a hand of debuffed cards was
never a flush. Seed QWERTYUI, Blue Deck, stake 1 found it on the headless
engine at decision 101: The Club debuffs Clubs, Smeared Joker makes Spades
Clubs, and A-Q-Q-9-6 of Spades and Clubs was a level-3 Flush in the game --
135 x 26 = 3510 -- and a level-2 Pair here, 95 x 21 = 1995. The run's
chips parted by exactly the difference, 18148 against 16633.
"""

from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.hands import HandType, evaluate


def _cards(*specs, debuffed=True):
    out = []
    for rank, suit in specs:
        card = Card(rank, suit)
        card.debuffed = debuffed
        out.append(card)
    return out


QWERTYUI = ((Rank.ACE, Suit.SPADES), (Rank.QUEEN, Suit.SPADES),
            (Rank.QUEEN, Suit.CLUBS), (Rank.NINE, Suit.SPADES),
            (Rank.SIX, Suit.SPADES))


def test_the_qwertyui_hand_is_a_flush_under_smeared_joker():
    assert evaluate(_cards(*QWERTYUI), smeared=True).hand is HandType.FLUSH


def test_without_smeared_joker_the_club_breaks_it():
    assert evaluate(_cards(*QWERTYUI), smeared=False).hand is HandType.PAIR


def test_five_debuffed_spades_are_a_flush():
    spades = ((Rank.ACE, Suit.SPADES), (Rank.JACK, Suit.SPADES),
              (Rank.NINE, Suit.SPADES), (Rank.SIX, Suit.SPADES),
              (Rank.THREE, Suit.SPADES))
    assert evaluate(_cards(*spades)).hand is HandType.FLUSH


def test_a_debuffed_wild_card_loses_its_every_suit():
    cards = _cards((Rank.ACE, Suit.SPADES), (Rank.JACK, Suit.SPADES),
                   (Rank.NINE, Suit.SPADES), (Rank.SIX, Suit.SPADES),
                   (Rank.THREE, Suit.HEARTS))
    cards[4].enhancement = Enhancement.WILD
    assert evaluate(cards).hand is not HandType.FLUSH, (
        "a debuffed Wild card is only its printed suit")
    cards[4].debuffed = False
    assert evaluate(cards).hand is HandType.FLUSH


def test_a_stone_card_is_never_a_suit():
    cards = _cards((Rank.ACE, Suit.SPADES), (Rank.JACK, Suit.SPADES),
                   (Rank.NINE, Suit.SPADES), (Rank.SIX, Suit.SPADES),
                   (Rank.THREE, Suit.SPADES), debuffed=False)
    cards[4].enhancement = Enhancement.STONE
    assert evaluate(cards).hand is not HandType.FLUSH
