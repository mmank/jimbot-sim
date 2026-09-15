"""A card held in hand is retriggered only when its first pass did something.

The held-card loop of evaluate_play (state_events.lua:784-826) asks for
repetitions once, after the first pass, and only for a card that pass had an
effect for:

    --From Red seal
    local eval = eval_card(G.hand.cards[i], {repetition_only = true, ...})
    if next(eval) and (next(effects[1]) or #effects > 1) then       -- 814

and Mime puts the same question to context.card_effects (card.lua:2879-2880):

    if self.ability.name == 'Mime' and
    (next(context.card_effects[1]) or #context.card_effects > 1) then

effects[1] is the card's own eval_card -- h_mult, and a Steel card's x_mult
(common_events.lua:624-638) -- and every joker that answered for the card adds
one more. Reserved Parking answers only when its roll comes up
(card.lua:3302-3318), so a face card whose first roll misses has nothing to
repeat: no second roll, and the draw it would have taken stays on the
'parking' stream for the next face card. A roll that pays is repeated, and
repeated as a fresh roll. Played cards are not gated like this (669-683).

The simulator added Mime's and a red seal's retriggers to every held card,
effect or not. OH4OWIIZ, Ghost Deck, stake 8, Mime and Reserved Parking: at
decision 12 the game rolled a held Queen twice (0.034 paid, 0.551 missed) and
a held Jack once (0.710 missed), and this rolled the Jack a second time as
well, taking 0.883. Money agreed until decision 21, where the game's held
Jack of Diamonds drew that 0.883 and missed, and this drew the next two,
0.177 and 0.375, and paid $2: game $1, simulator $3.
"""

import pytest

from jimbot_sim.cards import Card, Enhancement, Rank, Seal, Suit
from jimbot_sim.game import GameState
from jimbot_sim.jokers import make
from jimbot_sim.scoring import score_hand


def _game(jokers, cards, seed=0):
    game = GameState(seed=seed)
    game.jokers = [make(name) for name in jokers]
    game.hand = list(cards)
    game.full_deck = list(cards)
    game._apply_debuffs()
    return game


def _script(game, draws):
    """Answer each pseudorandom call from `draws`, recording the keys."""
    keys = []
    draws = iter(draws)

    def pseudorandom(key, low=None, high=None):
        keys.append(key)
        return next(draws)

    game.rng.pseudorandom = pseudorandom
    return keys


def _play(jokers, held, draws):
    played = [Card(Rank.TWO, Suit.SPADES)]
    game = _game(jokers, played + list(held))
    keys = _script(game, draws)
    result = game.evaluate_selection(played)
    ctx = score_hand(game, result, played, list(held))
    return ctx, keys


def test_mime_gives_a_missed_parking_roll_no_second_roll():
    """state_events.lua:814 and card.lua:2879-2880: nothing answered for the
    Jack, so there is nothing to repeat."""
    ctx, keys = _play(("Mime", "Reserved Parking"),
                      [Card(Rank.JACK, Suit.DIAMONDS)], [0.9, 0.1, 0.1])
    assert keys == ["parking"]
    assert ctx.money_gained == 0


def test_mime_repeats_a_paying_parking_roll_as_a_fresh_roll():
    """Parking returned dollars (card.lua:3312-3317), so Mime repeats it, and
    the repeat calls pseudorandom('parking') again."""
    ctx, keys = _play(("Mime", "Reserved Parking"),
                      [Card(Rank.JACK, Suit.DIAMONDS)], [0.1, 0.9, 0.1])
    assert keys == ["parking", "parking"]
    assert ctx.money_gained == 1


def test_the_oh4owiiz_queen_and_jack():
    """Decision 12 of OH4OWIIZ: the Queen paid and was rolled again, the Jack
    missed and was not -- three draws, $1, and the fourth left for later."""
    held = [Card(Rank.QUEEN, Suit.CLUBS), Card(Rank.JACK, Suit.DIAMONDS)]
    ctx, keys = _play(("Mime", "Reserved Parking"), held,
                      [0.03404750361783426, 0.5507368508250778,
                       0.7104680752217878, 0.8826796029025226])
    assert keys == ["parking"] * 3
    assert ctx.money_gained == 1


def test_a_red_seal_is_gated_the_same_way():
    """The red seal's own repetition, state_events.lua:813-817."""
    jack = Card(Rank.JACK, Suit.DIAMONDS)
    jack.seal = Seal.RED
    ctx, keys = _play(("Reserved Parking",), [jack], [0.9, 0.1])
    assert keys == ["parking"]
    assert ctx.money_gained == 0

    jack = Card(Rank.JACK, Suit.DIAMONDS)
    jack.seal = Seal.RED
    ctx, keys = _play(("Reserved Parking",), [jack], [0.1, 0.1])
    assert keys == ["parking", "parking"]
    assert ctx.money_gained == 2


def test_a_steel_face_card_is_its_own_effect():
    """A Steel card's x_mult fills effects[1] (common_events.lua:630-633), so
    Mime repeats it even when the parking roll missed -- and rolls again."""
    king = Card(Rank.KING, Suit.CLUBS)
    king.enhancement = Enhancement.STEEL
    ctx, keys = _play(("Mime", "Reserved Parking"), [king], [0.9, 0.1])
    assert keys == ["parking", "parking"]
    assert ctx.money_gained == 1
    assert ctx.mult == pytest.approx(1 * 1.5 * 1.5)


# -- the engine ----------------------------------------------------------------

HAND = "S_2 H_K D_Q C_J S_K H_Q D_J C_K"
_RANKS = {"2": Rank.TWO, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}
_JOKERS = {"j_mime": "Mime", "j_reserved_parking": "Reserved Parking"}

ENGINE_CASES = [
    # name, jokers, {hand index: (enhancement, seal)}
    ("mime, seven faces held", "j_mime j_reserved_parking", {}),
    ("red seals, no mime", "j_reserved_parking",
     {i: (None, "Red") for i in range(2, 9)}),
    ("mime, two steel faces", "j_mime j_reserved_parking",
     {2: ("m_steel", None), 5: ("m_steel", None)}),
]


@pytest.fixture(scope="module")
def engine():
    pytest.importorskip("lupa")
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
    if not engine_available():
        pytest.skip("no Balatro engine: need vendor/balatro_src")
    return HeadlessBalatro().boot()


def _engine_answer(engine, jokers, marks):
    from jimbot_sim.headless.scenario import Scenario
    scene = Scenario(engine).start().jokers(jokers).hand(HAND)
    for index, (enhancement, seal) in marks.items():
        scene.enhance(index, enhancement=enhancement, seal=seal)
    before = int(float(engine.eval("G.GAME.dollars")))
    score = scene.play((1,))
    dollars = int(float(engine.eval("G.GAME.dollars"))) - before
    stream = engine.eval("G.GAME.pseudorandom.parking")
    return score, dollars, None if stream is None else round(float(stream), 12)


def _simulator_answer(jokers, marks):
    cards = [Card(_RANKS[code[2]], _SUITS[code[0]]) for code in HAND.split()]
    for index, (enhancement, seal) in marks.items():
        if enhancement == "m_steel":
            cards[index - 1].enhancement = Enhancement.STEEL
        if seal == "Red":
            cards[index - 1].seal = Seal.RED
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.jokers = [make(_JOKERS[key]) for key in jokers.split()]
    game.hand = list(cards)
    game.full_deck = list(cards)
    played, held = cards[:1], cards[1:]
    ctx = score_hand(game, game.evaluate_selection(played), played, held)
    stream = game.rng.pools.get("parking")
    return (ctx.score, ctx.money_gained,
            None if stream is None else round(stream, 12))


@pytest.mark.slow
@pytest.mark.parametrize("name, jokers, marks", ENGINE_CASES,
                         ids=[c[0] for c in ENGINE_CASES])
def test_held_parking_rolls_match_the_engine(engine, name, jokers, marks):
    """(score, dollars, the 'parking' stream afterwards) on both sides."""
    expected = _engine_answer(engine, jokers, marks)
    assert _simulator_answer(jokers, marks) == expected, (
        "%s: engine %s" % (name, expected))
