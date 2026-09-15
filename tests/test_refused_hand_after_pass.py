"""A hand the boss refuses still runs the after-hand pass, and nothing else.

G.FUNCS.evaluate_play splits on `if not G.GAME.blind:debuff_hand(...)`
(state_events.lua:614). Everything inside the first branch is skipped for a
refused hand: the `before` joker pass (628-638), the cards, the jokers'
main effects, and the destroying pass with its glass roll (950-996). The
refused branch asks every joker `debuffed_hand` (1015-1027) -- and then,
outside the `if`, for every hand, comes the `after` pass (1068-1075).

Two jokers answer `after`: Ice Cream (card.lua:3571) and Seltzer
(card.lua:3601). Everything else that grows on a played hand is `before`
(card.lua:3411-3569): Spare Trousers, Space Joker, Square Joker, Runner,
Midas Mask, Vampire, To Do List, DNA, Ride the Bus, Obelisk, Green Joker.
Sixth Sense answers `destroying_card` (card.lua:2603-2604), inside the block.

The simulator ran its after-hand updates only inside score_hand, which a
refused hand never reaches. Seed VJPW2C6Z, Anaglyph Deck, stake 8 on the
headless engine at decision 46: a Three of a Kind The Mouth zeroed took the
game's Ice Cream from 40 to 35 and left the simulator's on 40. And it ran
DNA and Sixth Sense, and rolled glass, before asking whether the hand was
refused at all.
"""

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.game import Action, ActionType, GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

MOUTH = next(b for b in BOSSES if b.name == "The Mouth")
PSYCHIC = next(b for b in BOSSES if b.name == "The Psychic")

PAIR = ((Rank.NINE, Suit.HEARTS), (Rank.NINE, Suit.DIAMONDS),
        (Rank.FIVE, Suit.HEARTS), (Rank.FOUR, Suit.CLUBS),
        (Rank.TWO, Suit.CLUBS))
TRIPS = ((Rank.SEVEN, Suit.HEARTS), (Rank.SEVEN, Suit.CLUBS),
         (Rank.SEVEN, Suit.DIAMONDS), (Rank.FIVE, Suit.DIAMONDS),
         (Rank.FOUR, Suit.SPADES))


def _boss(boss, *names):
    game = GameState(seed="VJPW2C6Z", deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game.ante_boss = ""
    game.blind = make_blind(BlindKind.BOSS, game.ante, boss)
    game._start_round()
    game.blind.target = 10 ** 12       # never cleared, so the round goes on
    game.hands_left = 10
    return game


def _play(game, specs):
    """Chips the play adds to the round."""
    game.hand[:] = [Card(rank, suit) for rank, suit in specs]
    before = game.chips_scored
    game.step(Action(ActionType.PLAY, cards=tuple(range(len(specs)))))
    return game.chips_scored - before


def _counter(game, name):
    return next(j.counter for j in game.jokers if j.name == name)


def test_ice_cream_melts_on_a_refused_hand():
    """card.lua:3571-3594 under context.after (state_events.lua:1068-1070)."""
    game = _boss(MOUTH, "Ice Cream")
    assert _play(game, PAIR) > 0
    assert _counter(game, "Ice Cream") == 95
    assert _play(game, TRIPS) == 0
    assert _counter(game, "Ice Cream") == 90


def test_seltzer_counts_a_refused_hand():
    """card.lua:3601-3628, the same `after` pass."""
    game = _boss(MOUTH, "Seltzer")
    _play(game, PAIR)
    assert _counter(game, "Seltzer") == 9
    assert _play(game, TRIPS) == 0
    assert _counter(game, "Seltzer") == 8


def test_ice_cream_melts_away_on_a_refused_hand():
    """`extra.chips - chip_mod <= 0` eats it, refused hand or not."""
    game = _boss(MOUTH, "Ice Cream")
    _play(game, PAIR)
    game.jokers[0].counter = 5
    _play(game, TRIPS)
    assert [j.name for j in game.jokers] == []


def test_a_copier_does_not_melt_it_twice():
    """Both branches are `not context.blueprint`."""
    game = _boss(MOUTH, "Blueprint", "Ice Cream")
    _play(game, PAIR)
    _play(game, TRIPS)
    assert _counter(game, "Ice Cream") == 90


def test_a_debuffed_ice_cream_does_not_melt():
    """calculate_joker returns nil for a debuffed joker (card.lua:2291-2292)."""
    game = _boss(MOUTH, "Ice Cream")
    _play(game, PAIR)
    game.set_joker_debuff(game.jokers[0], True)
    _play(game, TRIPS)
    assert _counter(game, "Ice Cream") == 95


def test_before_jokers_do_not_move_on_a_refused_hand():
    """Green Joker (card.lua:3563) and Ride the Bus (3525) are `before`,
    skipped with the rest of the block (state_events.lua:614, 628-638)."""
    game = _boss(MOUTH, "Green Joker", "Ride the Bus")
    _play(game, PAIR)
    assert _counter(game, "Green Joker") == 1
    assert _counter(game, "Ride the Bus") == 1
    _play(game, TRIPS)
    assert _counter(game, "Green Joker") == 1
    assert _counter(game, "Ride the Bus") == 1


def _one_card(game, rank, enhancement=Enhancement.NONE):
    """Play one card of `rank`, taken from the deck so it belongs to the run."""
    card = next(c for c in game.draw_pile + game.hand if c.rank is rank)
    if card in game.draw_pile:
        game.draw_pile.remove(card)
        game.hand.insert(0, card)
    card.enhancement = enhancement
    index = game.hand.index(card)
    before = game.chips_scored
    game.step(Action(ActionType.PLAY, cards=(index,)))
    return card, game.chips_scored - before


def test_dna_copies_nothing_the_psychic_refuses():
    """DNA is `context.before` (card.lua:3501-3524): one card into The
    Psychic is refused, and the deck does not grow."""
    game = _boss(PSYCHIC, "DNA")
    deck = len(game.full_deck)
    _, scored = _one_card(game, Rank.KING)
    assert scored == 0
    assert len(game.full_deck) == deck


def test_dna_still_copies_an_allowed_card():
    game = _boss(PSYCHIC, "DNA")
    game.blind.disabled = True
    deck = len(game.full_deck)
    _one_card(game, Rank.KING)
    assert len(game.full_deck) == deck + 1


def test_sixth_sense_takes_nothing_the_psychic_refuses():
    """Sixth Sense is `context.destroying_card` (card.lua:2603-2604), asked
    at state_events.lua:957 inside the block."""
    game = _boss(PSYCHIC, "Sixth Sense")
    deck = len(game.full_deck)
    card, scored = _one_card(game, Rank.SIX)
    assert scored == 0
    assert card in game.full_deck and len(game.full_deck) == deck
    assert game.consumables == []
    assert "sixth" not in game.rng.pools


def test_a_refused_glass_card_does_not_roll():
    """The glass roll (state_events.lua:961) is inside the block too, so a
    refused hand leaves the 'glass' stream where it was."""
    game = _boss(PSYCHIC)
    deck = len(game.full_deck)
    for _ in range(3):
        _one_card(game, Rank.ACE, Enhancement.GLASS)
    assert "glass" not in game.rng.pools
    assert len(game.full_deck) == deck
