"""The Hook's two cards are gone before the hand scores.

`Blind:press_play` is where The Hook lives, and the game runs it between
moving the played cards out of the hand and scoring anything:

    draw_card(G.hand, G.play, ..., G.hand.highlighted[i])   -- once per card
    ...
    if G.GAME.blind:press_play() then

So the two cards it takes are already in the discard by the time the held
pass runs, and a Steel card among them pays nothing. Scoring first -- which
the simulator did -- gave the round one extra held trigger per hand.

Recording 11 stopped on exactly that: 75 chips x 193.5 against the game's
75 x 186, one Steel card's x1.5, and it beat a 40000 blind on 40409 that the
game failed at 39847.

And a Hook discard is a real discard. The game has one function with a flag,
and everything the flag turns off sits at the end of it:

    if not hook then
        ... ease_discard(-1)
        G.GAME.current_round.discards_used = ... + 1

-- so the seals fire and the jokers fire, and only the discard count, the
cost and the redraw are skipped. A purple-sealed card taken by The Hook
makes its Tarot, which is the second thing recording 11 stopped on.
"""

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Enhancement, Seal
from jimbot_sim.game import GameState

HOOK = next(b for b in BOSSES if b.name == "The Hook")


def _on_the_hook(cards: int = 4, seed: str = "TESTSEED") -> GameState:
    """A run standing on The Hook, holding `cards` of a real dealt hand."""
    game = GameState(seed=seed)
    game._start_round()
    game.blind = make_blind(BlindKind.BOSS, 1, HOOK)
    game.hand[:] = game.hand[:cards]
    return game


def test_a_steel_card_the_hook_takes_does_not_score():
    # Two cards to play and the rest of the hand Steel, so whatever The Hook
    # takes it takes Steel. Both remaining cards are taken, so the held pass
    # must find none and the score must match a plain hand's.
    game = _on_the_hook()
    for card in game.hand[2:]:
        card.enhancement = Enhancement.STEEL
    on_the_hook = _score(game)

    plain = _on_the_hook()
    plain.blind = make_blind(BlindKind.BOSS, 1, None)
    for card in plain.hand[2:]:
        card.enhancement = Enhancement.STEEL
    with_steel = _score(plain)

    bare = _on_the_hook()
    bare.blind = make_blind(BlindKind.BOSS, 1, None)
    without_steel = _score(bare)

    assert with_steel > without_steel, "the Steel cards were never scoring"
    assert on_the_hook == without_steel, (
        "The Hook took both Steel cards and they still paid: %d, against %d "
        "held and %d not" % (on_the_hook, with_steel, without_steel))


def _score(game) -> int:
    before = game.chips_scored
    game._play((0, 1))
    return game.chips_scored - before


def test_a_purple_seal_the_hook_takes_makes_its_tarot():
    game = _on_the_hook()
    for card in game.hand[2:]:
        card.seal = Seal.PURPLE
    game.consumables.clear()

    game._play((0, 1))

    assert game.consumables, (
        "The Hook discarded two purple-sealed cards and neither made a Tarot")


def test_a_hook_discard_costs_no_discard():
    game = _on_the_hook()
    discards, used = game.discards_left, game.discards_used

    game._play((0, 1))

    assert (game.discards_left, game.discards_used) == (discards, used)
