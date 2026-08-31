"""Scoring checks against values a Balatro player can verify by hand."""

from balatro.cards import Card, Edition, Enhancement, Rank, Seal, Suit
from balatro.game import GameState
from balatro.hands import HandType, evaluate
from balatro.jokers import make
from balatro.scoring import effective_specs, score_hand

S, H, D, C = Suit.SPADES, Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS


def play(cards, jokers=(), held=(), seed=0):
    game = GameState(seed=seed)
    game.jokers = list(jokers)
    game.hand = list(cards) + list(held)
    result = evaluate(list(cards))
    return score_hand(game, result, list(cards), list(held))


def test_pair_of_kings():
    # Pair: 10 chips x 2 mult, plus two 10-chip Kings -> 30 x 2 = 60
    ctx = play([Card(Rank.KING, S), Card(Rank.KING, H)])
    assert (ctx.chips, ctx.mult) == (30, 2)
    assert ctx.score == 60


def test_flush_of_low_hearts():
    # Flush: 35 x 4, cards 2+4+6+8+10 = 30 -> 65 x 4 = 260
    cards = [Card(r, H) for r in (Rank.TWO, Rank.FOUR, Rank.SIX, Rank.EIGHT, Rank.TEN)]
    ctx = play(cards)
    assert ctx.score == 260


def test_plain_joker_adds_four_mult():
    ctx = play([Card(Rank.KING, S), Card(Rank.KING, H)], jokers=[make("Joker")])
    assert ctx.score == 30 * 6


def test_xmult_applies_after_plus_mult_in_slot_order():
    cards = [Card(Rank.KING, S), Card(Rank.KING, H)]
    plus_then_x = play(cards, jokers=[make("Joker"), make("The Duo")])
    x_then_plus = play(cards, jokers=[make("The Duo"), make("Joker")])
    assert plus_then_x.score == 30 * ((2 + 4) * 2)   # 360
    assert x_then_plus.score == 30 * ((2 * 2) + 4)   # 240


def test_steel_card_held_in_hand():
    ctx = play([Card(Rank.KING, S), Card(Rank.KING, H)],
               held=[Card(Rank.TWO, D, enhancement=Enhancement.STEEL)])
    assert ctx.score == int(30 * 2 * 1.5)


def test_bonus_and_mult_enhancements():
    ctx = play([Card(Rank.KING, S, enhancement=Enhancement.BONUS),
                Card(Rank.KING, H, enhancement=Enhancement.MULT)])
    assert (ctx.chips, ctx.mult) == (30 + 30, 2 + 4)


def test_editions_on_cards():
    ctx = play([Card(Rank.KING, S, edition=Edition.FOIL),
                Card(Rank.KING, H, edition=Edition.HOLOGRAPHIC)])
    assert (ctx.chips, ctx.mult) == (30 + 50, 2 + 10)


def test_red_seal_retriggers_the_card():
    plain = play([Card(Rank.KING, S), Card(Rank.KING, H)])
    sealed = play([Card(Rank.KING, S, seal=Seal.RED), Card(Rank.KING, H)])
    assert sealed.chips == plain.chips + 10


def test_scored_card_hook_only_sees_scoring_cards():
    # The 3 does not score in a pair, so Odd Todd must not count it.
    cards = [Card(Rank.KING, S), Card(Rank.KING, H), Card(Rank.THREE, D)]
    ctx = play(cards, jokers=[make("Odd Todd")])
    assert ctx.chips == 30


def test_blueprint_copies_the_joker_to_its_right():
    jokers = [make("Blueprint"), make("The Duo")]
    # effective_specs answers both halves: which ability runs, and whose state
    # it runs on. The Blueprint runs The Duo's ability against The Duo itself,
    # which is what the game does -- other_joker:calculate_joker(context).
    resolved = effective_specs(jokers)
    assert [spec.name for spec, _ in resolved] == ["The Duo", "The Duo"]
    assert [source is jokers[1] for _, source in resolved] == [True, True]
    ctx = play([Card(Rank.KING, S), Card(Rank.KING, H)], jokers=jokers)
    assert ctx.score == 30 * 2 * 2 * 2


def test_blueprint_with_nothing_to_the_right_does_nothing():
    jokers = [make("The Duo"), make("Blueprint")]
    ctx = play([Card(Rank.KING, S), Card(Rank.KING, H)], jokers=jokers)
    assert ctx.score == 30 * 2 * 2


def test_brainstorm_copies_the_leftmost_joker():
    jokers = [make("The Duo"), make("Joker"), make("Brainstorm")]
    resolved = effective_specs(jokers)
    assert [spec.name for spec, _ in resolved] == ["The Duo", "Joker", "The Duo"]
    # And the Brainstorm reads the leftmost joker's state, not its own.
    assert resolved[2][1] is jokers[0]


def test_copier_cycle_terminates():
    jokers = [make("Brainstorm"), make("Blueprint")]
    effective_specs(jokers)  # must not hang


def test_baron_multiplies_per_held_king():
    ctx = play([Card(Rank.TWO, S), Card(Rank.TWO, H)],
               jokers=[make("Baron")],
               held=[Card(Rank.KING, D), Card(Rank.KING, C)])
    assert ctx.mult == 2 * 1.5 * 1.5


def test_sock_and_buskin_retriggers_faces():
    ctx = play([Card(Rank.KING, S), Card(Rank.KING, H)],
               jokers=[make("Sock and Buskin"), make("Scary Face")])
    # base 10, then each King triggers twice for 10 card chips + 30 from Scary Face
    assert ctx.chips == 10 + 2 * 2 * (10 + 30)


def test_hand_level_feeds_the_base_values():
    game = GameState(seed=0)
    cards = [Card(Rank.KING, S), Card(Rank.KING, H)]
    game.hand_levels.level_up(HandType.PAIR)
    ctx = score_hand(game, evaluate(cards), cards, [])
    assert (ctx.chips, ctx.mult) == (25 + 20, 3)
