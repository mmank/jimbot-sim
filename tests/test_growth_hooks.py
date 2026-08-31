"""The jokers that grow on something other than a hand being played.

Five of these had a counter that nothing ever moved -- Canio, Yorick, Glass
Joker, Hit the Road and Perkeo -- so each sat at its starting value for whole
runs while its text promised otherwise. They were found by walking the game's
own `context.*` branches and asking which joker names appear in each, rather
than one at a time.

Glass Joker is the one worth care. Reading the card -- "per Glass card
destroyed" -- gets it wrong, and so does reading the first branch of the
game's own handler, which counts `context.glass_shattered`: nothing in the
game ever fires that context, so it is dead code. The live branch counts
destroyed cards carrying `.shattered`, and that flag is written at different
times by different destroyers:

    scoring, discarding   set inline, before the jokers are told -- it pays
    the tarots            queued as an animation, after -- it pays nothing

So a Familiar or an Immolate eating a glass card gives Glass Joker no mult at
all, which is an accident of animation order rather than a rule. The Hanged
Man is the exception, and only because the game patched around it with a
second handler on `using_consumeable`.

The numbers below were taken from the engine before they were written down.
"""

import pytest

from balatro.cards import Card, Enhancement, Rank, Suit
from balatro.consumables import REGISTRY as CONSUMABLES
from balatro.game import Action, ActionType, GameState
from balatro.jokers import REGISTRY as JOKERS, JokerInstance


def _run(*names):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    return game


def _counter(game, name):
    return next(j.counter for j in game.jokers if j.name == name)


def test_canio_counts_a_face_card_destroyed_by_a_tarot():
    game = _run("Canio")
    face = next(c for c in game.hand if c.rank in (Rank.JACK, Rank.QUEEN,
                                                   Rank.KING))
    before = _counter(game, "Canio")
    game.remove_card(face)
    assert _counter(game, "Canio") == before + 1


def test_canio_ignores_a_number_card():
    game = _run("Canio")
    plain = next(c for c in game.hand if c.rank not in (Rank.JACK, Rank.QUEEN,
                                                        Rank.KING))
    before = _counter(game, "Canio")
    game.remove_card(plain)
    assert _counter(game, "Canio") == before


def test_glass_joker_is_paid_when_a_glass_card_shatters_while_scoring():
    game = _run("Glass Joker")
    card = game.hand[0]
    card.enhancement = Enhancement.GLASS
    before = _counter(game, "Glass Joker")
    game.remove_card(card, shattered=True)
    assert _counter(game, "Glass Joker") == before + 0.75


@pytest.mark.parametrize("tarot", ["Familiar", "Grim", "Incantation",
                                   "Immolate"])
def test_a_tarot_eating_a_glass_card_pays_glass_joker_nothing(tarot):
    """Engine, uniform glass hand: X1.00 before and X1.00 after, each one."""
    game = _run("Glass Joker")
    card = game.hand[0]
    card.enhancement = Enhancement.GLASS
    before = _counter(game, "Glass Joker")
    game.remove_card(card)
    assert _counter(game, "Glass Joker") == before


def test_the_hanged_man_pays_glass_joker_by_its_own_side_door():
    """Engine: two glass cards selected moved it X1.00 -> X1.75."""
    game = _run("Glass Joker")
    glass = game.hand[:2]
    for card in glass:
        card.enhancement = Enhancement.GLASS
    before = _counter(game, "Glass Joker")
    CONSUMABLES["The Hanged Man"].apply(game, list(glass))
    assert _counter(game, "Glass Joker") == before + 1.5


def test_the_hanged_man_pays_nothing_for_plain_cards():
    """Engine: two kings selected, glass +0.00, canio +2.00."""
    game = _run("Glass Joker", "Canio")
    plain = game.hand[:2]
    for card in plain:
        card.rank = Rank.KING
    before_glass = _counter(game, "Glass Joker")
    before_canio = _counter(game, "Canio")
    CONSUMABLES["The Hanged Man"].apply(game, list(plain))
    assert _counter(game, "Glass Joker") == before_glass
    assert _counter(game, "Canio") == before_canio + 2


def test_canio_eats_a_tarot_victim_even_though_glass_joker_does_not():
    """The halves of the same destruction go to different places."""
    game = _run("Glass Joker", "Canio")
    card = next(c for c in game.hand if c.rank in (Rank.JACK, Rank.QUEEN,
                                                   Rank.KING))
    card.enhancement = Enhancement.GLASS
    glass_before = _counter(game, "Glass Joker")
    canio_before = _counter(game, "Canio")
    game.remove_card(card)
    assert _counter(game, "Glass Joker") == glass_before
    assert _counter(game, "Canio") == canio_before + 1


def test_trading_card_shatters_the_glass_card_it_eats():
    """Discard destruction sets the flag inline, so this one does pay."""
    game = _run("Glass Joker", "Trading Card")
    card = game.hand[0]
    card.enhancement = Enhancement.GLASS
    before = _counter(game, "Glass Joker")
    game.step(Action(ActionType.DISCARD, cards=(0,)))
    assert _counter(game, "Glass Joker") == before + 0.75


def test_hit_the_road_counts_discarded_jacks():
    game = _run("Hit the Road")
    game.hand[:] = [Card(Rank.JACK, Suit.SPADES), Card(Rank.TWO, Suit.CLUBS),
                    Card(Rank.JACK, Suit.HEARTS)]
    before = _counter(game, "Hit the Road")
    game.step(Action(ActionType.DISCARD, cards=(0, 1, 2)))
    assert _counter(game, "Hit the Road") == before + 1.0   # two jacks


def test_yorick_counts_down_to_its_next_multiplier():
    """Twenty-three cards, counted down on the joker rather than run-wide."""
    game = _run("Yorick")
    joker = game.jokers[0]
    assert joker.secondary == 23
    game.hand[:] = [Card(Rank.TWO, Suit.CLUBS) for _ in range(5)]
    game.step(Action(ActionType.DISCARD, cards=(0, 1, 2, 3, 4)))
    assert joker.secondary == 18
    assert joker.counter == 1.0, "it should not have paid out yet"

    joker.secondary = 1
    game.hand[:] = [Card(Rank.TWO, Suit.CLUBS)]
    game.discards_left = 1
    game.step(Action(ActionType.DISCARD, cards=(0,)))
    assert joker.counter == 2.0
    assert joker.secondary == 23, "and it starts counting again"


def test_perkeo_copies_a_consumable_when_the_shop_closes():
    game = _run("Perkeo")
    game.consumables.append(CONSUMABLES["The Fool"])
    game.phase = game.phase          # leave the shop from wherever we are
    game.shop = None
    game._leave_shop()
    assert [c.name for c in game.consumables] == ["The Fool", "The Fool"]


def test_perkeo_copies_nothing_from_an_empty_row():
    game = _run("Perkeo")
    game.shop = None
    game._leave_shop()
    assert game.consumables == []
