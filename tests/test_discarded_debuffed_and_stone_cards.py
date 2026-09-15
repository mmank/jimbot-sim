"""What a discarded card that is debuffed, or Stone, sets off -- and what not.

A discard walks the highlighted cards one at a time
(state_events.lua:399-404):

    for i=1, highlighted_count do
        G.hand.highlighted[i]:calculate_seal({discard = true})
        ...
            eval = G.jokers.cards[j]:calculate_joker({discard = true,
                other_card = G.hand.highlighted[i], full_hand = G.hand.highlighted})

and each effect decides for itself whether a debuffed card counts:

  * the seal refuses outright -- Card:calculate_seal opens
    `if self.debuff then return nil end` (card.lua:2242-2243), so a debuffed
    Purple Seal makes no Tarot (card.lua:2253-2254);
  * Castle, Mail-In Rebate and Hit the Road each test
    `not context.other_card.debuff` (card.lua:2815, 2826, 2836);
  * Faceless Joker asks `v:is_face()` (card.lua:2861), and is_face returns
    nothing for a debuffed card (card.lua:965);
  * Ramen, Yorick, Trading Card and Green Joker (card.lua:2757, 2788, 2802,
    2846) and Burnt Joker's pre_discard (card.lua:2749) never look.

A Stone card is its own question. Mail-In Rebate and Hit the Road compare
`other_card:get_id()`, and get_id answers a Stone card with
`-math.random(100, 1000000)` (card.lua:958-960), which is no rank at all. The
same draw feeds is_face (card.lua:966-967), so a Stone King is not a face card
-- unless Pareidolia is held, which makes *every* card one, Stone included.

Found by the smoke test on fresh seeds, each one $5 or a Tarot out right after
a discard: NXE7XRN1 (Zodiac, stake 3) and XGC81J77 (Red, stake 8) threw a Jack
and a Queen The Pillar had debuffed, UBDY5AUG (Abandoned, stake 5) a Three of
Clubs under The Club -- each the round's Mail-In rank; W3D6TLM1 (Green, stake 3)
threw a Purple Seal Seven under Verdant Leaf; and WA1RMJNV (Painted, stake 1),
with no boss at all, threw a Stone Ace in a round whose Mail-In rank was Ace.
"""

import pytest

from jimbot_sim.cards import Enhancement, Rank, Seal
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance


def _run(*names):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    return game


def _joker(game, name):
    return next(j for j in game.jokers if j.name == name)


def _cards(game, n, rank=None, debuffed=False, stone=False):
    """The first n cards of the hand, made into what the test needs."""
    for card in game.hand[:n]:
        if rank is not None:
            card.rank = rank
        card.debuffed = debuffed
        if stone:
            card.enhancement = Enhancement.STONE
    return tuple(range(n))


# -- Mail-In Rebate ---------------------------------------------------------

@pytest.mark.parametrize("debuffed,stone,paid", [
    (False, False, 5),
    (True, False, 0),     # card.lua:2826  not context.other_card.debuff
    (False, True, 0),     # card.lua:2827  get_id(), card.lua:958-960
])
def test_mail_in_rebate_pays_only_for_a_live_card_of_its_rank(debuffed, stone,
                                                              paid):
    """card.lua:2825-2828: `not context.other_card.debuff and
    context.other_card:get_id() == G.GAME.current_round.mail_card.id`."""
    game = _run("Mail-In Rebate")
    game.mail_rank = Rank.KING
    indices = _cards(game, 1, Rank.KING, debuffed=debuffed, stone=stone)
    money = game.money
    game._discard(indices)
    assert game.money - money == paid


def test_mail_in_rebate_counts_the_live_ones_beside_a_debuffed_one():
    """Per card: the check is inside the loop at state_events.lua:399-404."""
    game = _run("Mail-In Rebate")
    game.mail_rank = Rank.KING
    _cards(game, 3, Rank.KING)
    game.hand[0].debuffed = True
    game.hand[1].enhancement = Enhancement.STONE
    money = game.money
    game._discard((0, 1, 2))
    assert game.money - money == 5


# -- Hit the Road -----------------------------------------------------------

@pytest.mark.parametrize("debuffed,stone,gain", [
    (False, False, 0.5),
    (True, False, 0.0),   # card.lua:2836
    (False, True, 0.0),   # card.lua:2837  get_id() == 11
])
def test_hit_the_road_grows_only_on_a_live_jack(debuffed, stone, gain):
    """card.lua:2835-2838."""
    game = _run("Hit the Road")
    joker = _joker(game, "Hit the Road")
    indices = _cards(game, 1, Rank.JACK, debuffed=debuffed, stone=stone)
    before = joker.counter
    game._discard(indices)
    assert joker.counter - before == pytest.approx(gain)


# -- Faceless Joker ---------------------------------------------------------

def _faceless(row, rank, debuffed=(), stone=()):
    game = _run("Faceless Joker", *row)
    indices = _cards(game, 3, rank)
    for i in debuffed:
        game.hand[i].debuffed = True
    for i in stone:
        game.hand[i].enhancement = Enhancement.STONE
    money = game.money
    game._discard(indices)
    return game.money - money


def test_faceless_joker_pays_for_three_live_face_cards():
    assert _faceless((), Rank.KING) == 5


def test_faceless_joker_does_not_count_a_debuffed_face_card():
    """card.lua:2861 `v:is_face()`, and card.lua:965
    `if self.debuff and not from_boss then return end`."""
    assert _faceless((), Rank.KING, debuffed=(0,)) == 0


def test_faceless_joker_does_not_count_a_stone_king():
    """card.lua:966-967: a Stone card's get_id is negative (card.lua:958)."""
    assert _faceless((), Rank.KING, stone=(0,)) == 0


def test_faceless_joker_with_pareidolia_counts_any_card():
    """card.lua:967 `... or next(find_joker("Pareidolia"))` -- a Two is a face
    card, and so is a Stone card, whatever get_id drew."""
    assert _faceless(("Pareidolia",), Rank.TWO, stone=(1,)) == 5


def test_pareidolia_does_not_lift_the_debuff():
    """card.lua:965 returns before card.lua:967 is reached."""
    assert _faceless(("Pareidolia",), Rank.TWO, debuffed=(2,)) == 0


# -- Purple Seal ------------------------------------------------------------

@pytest.mark.parametrize("debuffed,made", [(False, 1), (True, 0)])
def test_a_purple_seal_makes_a_tarot_only_when_the_card_is_live(debuffed,
                                                                made):
    """card.lua:2242-2243 `if self.debuff then return nil end` comes before the
    Purple Seal's branch at card.lua:2253-2254."""
    game = _run()
    game.consumables.clear()
    indices = _cards(game, 1, debuffed=debuffed)
    game.hand[0].seal = Seal.PURPLE
    game._discard(indices)
    assert len(game.consumables) == made


# -- the ones that never look -----------------------------------------------

def _ramen(game):
    return _joker(game, "Ramen").counter


def _yorick(game):
    return _joker(game, "Yorick").secondary


def _green(game):
    return _joker(game, "Green Joker").counter


def _trading(game):
    return game.money, len(game.full_deck)


def _burnt(game):
    return dict(game.hand_levels.levels)


BLIND_TO_DEBUFF = [
    # name, how many cards go, rank, read
    ("Ramen", 3, None, _ramen),               # card.lua:2757
    ("Yorick", 3, None, _yorick),             # card.lua:2788
    ("Trading Card", 1, None, _trading),      # card.lua:2802
    ("Green Joker", 3, None, _green),         # card.lua:2846
    ("Burnt Joker", 2, Rank.NINE, _burnt),    # card.lua:2749 pre_discard
]


@pytest.mark.parametrize("name,n,rank,read", BLIND_TO_DEBUFF,
                         ids=[b[0] for b in BLIND_TO_DEBUFF])
def test_these_count_a_debuffed_card_all_the_same(name, n, rank, read):
    """None of these branches tests other_card.debuff, so a hand of debuffed
    cards moves them exactly as a live one does."""
    outcomes = []
    for debuffed in (False, True):
        game = _run(name)
        if name == "Green Joker":
            _joker(game, name).counter = 3.0
        indices = _cards(game, n, rank, debuffed=debuffed)
        before = read(game)
        game._discard(indices)
        outcomes.append((before, read(game)))
    assert outcomes[0][0] != outcomes[0][1]
    assert outcomes[1] == outcomes[0]


def test_castle_skips_a_debuffed_card_of_its_suit():
    """card.lua:2815 (and is_suit's own debuff test, card.lua:4077)."""
    game = _run("Castle")
    joker = _joker(game, "Castle")
    indices = _cards(game, 2)
    for card in game.hand[:2]:
        card.suit = game.castle_suit
    game.hand[0].debuffed = True
    before = joker.counter
    game._discard(indices)
    assert joker.counter - before == 3.0
