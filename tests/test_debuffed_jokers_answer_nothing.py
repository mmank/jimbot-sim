"""A debuffed joker answers no calculate_joker context at all.

Card:calculate_joker opens (card.lua:2291-2292)

    function Card:calculate_joker(context)
        if self.debuff then return nil end

and says it again for jokers at card.lua:2303. Every joker context the game
fires goes through that door: pre_discard and discard (state_events.lua:395,
404), end_of_round (state_events.lua:101), first_hand_drawn (game.lua:3229),
selling_self (card.lua:1599), selling_card (button_callbacks.lua:2323),
using_consumeable (button_callbacks.lua:2220), playing_card_added
(misc_functions.lua:1582), remove_playing_cards (card.lua:1370,
state_events.lua:426, 975), reroll_shop (button_callbacks.lua:2901),
skipping_booster (2560), ending_shop (2486), open_booster (card.lua:1797), and
before / joker_main / debuffed_hand while a hand scores. A Blueprint or a
Brainstorm copies by calling `other_joker:calculate_joker(context)`
(card.lua:2304-2330), so a copy of a debuffed joker is nothing as well --
and the joker it copies is the one beside it in the whole row, debuffed or not.

Two more doors shut the same way. find_joker skips a debuffed joker unless it
is asked not to (misc_functions.lua:903-907), and that is how Four Fingers
(misc_functions.lua:524), Shortcut (567), Splash (state_events.lua:583),
Pareidolia (card.lua:967) and Smeared Joker (card.lua:4072) are read. Oops! All
6s and Chicot live in add_to_deck / remove_from_deck (card.lua:596, 608, 665),
which set_debuff runs (card.lua:526-538).

What is *not* behind the guard, and so still happens to a debuffed joker:
calculate_rental and calculate_perishable (state_events.lua:108-109,
card.lua:2271-2289), Gift Card's walk down the row (card.lua:2993-2994), and a
Negative's slot, which remove_from_deck(true) only queues for removal
(card.lua:687-689). calculate_dollar_bonus has a guard of its own
(card.lua:1656), read while the round's rows are built (state_events.lua:1176)
-- before the defeated blind's set_blind(nil), queued as an event
(state_events.lua:1150-1153, blind.lua:333-337), gives a Crimson Heart joker
back.

RRT5KY7W (Yellow Deck, stake 1) stopped on it at decision 188: a discard in an
ante-8 Crimson Heart round with Ramen debuffed, X1.85 in the game and X1.8 in
the shadow.
"""

import pytest

from jimbot_sim.blinds import FINISHER_BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Edition, Enhancement, Rank, Suit
from jimbot_sim.consumables import REGISTRY as CONSUMABLES
from jimbot_sim.game import Action, ActionType, GameState, Tag
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance
from jimbot_sim.shop import BY_KEY

HEART = next(b for b in FINISHER_BOSSES if b.name == "Crimson Heart")


def _run(*names, seed="TESTSEED", deck="Red Deck"):
    game = GameState(seed=seed, deck=deck)
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    return game


def _joker(game, name):
    return next(j for j in game.jokers if j.name == name)


def _debuff(game, name):
    joker = _joker(game, name)
    game.set_joker_debuff(joker, True)
    return joker


# -- the stop itself --------------------------------------------------------

def test_a_debuffed_ramen_keeps_its_x_mult_through_a_discard():
    """card.lua:2292 before card.lua:2757 (`context.discard`, Ramen)."""
    game = _run("Ramen")
    ramen = _debuff(game, "Ramen")
    ramen.counter = 1.85
    game._discard((0, 1, 2, 3, 4))
    assert ramen.counter == 1.85


# -- discard, pre_discard ---------------------------------------------------

def _discard_state(game, name):
    joker = _joker(game, name)
    return (joker.counter, joker.secondary, game.money, len(game.full_deck),
            dict(game.hand_levels.levels))


def _five_jacks(game, name):
    for card in game.hand[:5]:
        card.rank = (game.mail_rank if name == "Mail-In Rebate"
                     and game.mail_rank is not None else Rank.JACK)
        card.suit = game.castle_suit or Suit.SPADES
    return (0, 1, 2, 3, 4)


DISCARDERS = [
    # name, which cards go
    ("Ramen", _five_jacks),            # card.lua:2757  discard
    ("Yorick", _five_jacks),           # card.lua:2788  discard
    ("Castle", _five_jacks),           # card.lua:2814  discard
    ("Mail-In Rebate", _five_jacks),   # card.lua:2825  discard
    ("Hit the Road", _five_jacks),     # card.lua:2835  discard
    ("Faceless Joker", _five_jacks),   # card.lua:2858  discard
    ("Burnt Joker", _five_jacks),      # card.lua:2749  pre_discard
    ("Trading Card", lambda game, name: (0,)),   # card.lua:2802  discard
]


@pytest.mark.parametrize("name,pick", DISCARDERS, ids=[d[0] for d in DISCARDERS])
def test_a_debuffed_joker_takes_nothing_from_a_discard(name, pick):
    """card.lua:2292: the discard and pre_discard contexts
    (state_events.lua:395, 404) reach a debuffed joker and get nil back."""
    game = _run(name)
    _debuff(game, name)
    indices = pick(game, name)
    before = _discard_state(game, name)
    game._discard(indices)
    assert _discard_state(game, name) == before


@pytest.mark.parametrize("name,pick", DISCARDERS, ids=[d[0] for d in DISCARDERS])
def test_the_same_discard_moves_a_live_one(name, pick):
    """The control: without the debuff each of these does move."""
    game = _run(name)
    indices = pick(game, name)
    before = _discard_state(game, name)
    game._discard(indices)
    assert _discard_state(game, name) != before


# -- end_of_round -----------------------------------------------------------

def _round_state(game):
    state = game.rng.state()
    return ([(j.name, j.counter, j.secondary, j.extra_sell_value, j.named_hand)
             for j in game.jokers],
            state.get("gros_michel"), state.get("to_do"))


ROUND_ENDERS = [
    # name, row, blind kind, setup
    ("Popcorn", ("Popcorn",), BlindKind.SMALL, None),             # 2945
    ("Gros Michel", ("Gros Michel",), BlindKind.SMALL, None),     # 3019
    ("Invisible Joker", ("Invisible Joker",), BlindKind.SMALL, None),  # 2934
    ("Egg", ("Egg",), BlindKind.SMALL, None),                     # 2985
    ("Gift Card", ("Gift Card", "Joker"), BlindKind.SMALL, None),  # 2993
    ("To Do List", ("To Do List",), BlindKind.SMALL, None),       # 2975
    ("Rocket", ("Rocket",), BlindKind.BOSS, None),                # 2896
    ("Campfire", ("Campfire",), BlindKind.BOSS,
     lambda j: setattr(j, "counter", 2.0)),                       # 2889
]


def _beat(name, row, kind, setup, debuffed):
    game = _run(*row)
    joker = _joker(game, name)
    if setup is not None:
        setup(joker)
    if debuffed:
        game.set_joker_debuff(joker, True)
    game.blind = make_blind(kind, game.ante)
    before = _round_state(game)
    game._beat_blind()
    return before, _round_state(game)


@pytest.mark.parametrize("name,row,kind,setup", ROUND_ENDERS,
                         ids=[r[0] for r in ROUND_ENDERS])
def test_a_debuffed_joker_does_nothing_when_the_round_ends(name, row, kind,
                                                           setup):
    """card.lua:2292 before the end_of_round branches (state_events.lua:101).
    No decay, no growth, no roll -- Gros Michel's and To Do List's streams
    are left where they were."""
    before, after = _beat(name, row, kind, setup, debuffed=True)
    assert after == before


@pytest.mark.parametrize("name,row,kind,setup", ROUND_ENDERS,
                         ids=[r[0] for r in ROUND_ENDERS])
def test_a_live_one_does(name, row, kind, setup):
    before, after = _beat(name, row, kind, setup, debuffed=False)
    assert after != before


def test_a_crimson_heart_joker_pays_no_row_on_the_cash_out_that_frees_it():
    """calculate_dollar_bonus returns early for a debuffed joker (card.lua:1656)
    and the rows are built at state_events.lua:1176, while the blind's release
    of its joker is still a queued event (1150-1153, blind.lua:333-337)."""
    game = _run("Golden Joker")
    joker = _debuff(game, "Golden Joker")
    game.blind = make_blind(BlindKind.SMALL, game.ante)
    game._beat_blind()
    assert not joker.debuffed          # freed on the cash-out screen ...
    game._cash_out()
    assert not [line for line in game.logs
                if line.startswith("Golden Joker: +$")]   # ... and unpaid


# -- the rest of the contexts ----------------------------------------------

def _face(game):
    card = game.hand[0]
    card.rank = Rank.KING
    return card


def _glass(game):
    card = game.hand[0]
    card.enhancement = Enhancement.GLASS
    return card


def _shop(game):
    game.money = 100
    game._open_shop()
    return game


def _holding_the_fool(game):
    game.consumables.append(game.hold_consumable(CONSUMABLES["The Fool"]))
    return game


def _vagabond(game):
    game.money = 0
    game.step(Action(ActionType.PLAY, cards=(0,)))


CONTEXTS = {
    # name: (build the run, act, read)
    "Hologram": (lambda: _run("Hologram"),          # playing_card_added 2457
                 lambda g: g.add_card_to_hand(g.hand[0].copy()),
                 lambda g: _joker(g, "Hologram").counter),
    "Constellation": (lambda: _run("Constellation"),   # using_consumeable 2727
                      lambda g: g.use_consumable(CONSUMABLES["Mercury"], []),
                      lambda g: _joker(g, "Constellation").counter),
    "Campfire": (lambda: _run("Campfire"),          # selling_card 2396
                 lambda g: g.note_card_sold(),
                 lambda g: _joker(g, "Campfire").counter),
    "Canio": (lambda: _run("Canio"),                # remove_playing_cards 2623
              lambda g: g.remove_card(_face(g)),
              lambda g: _joker(g, "Canio").counter),
    "Glass Joker": (lambda: _run("Glass Joker"),    # using_consumeable 2709
                    lambda g: g.use_consumable(CONSUMABLES["The Hanged Man"],
                                               [_glass(g)]),
                    lambda g: _joker(g, "Glass Joker").counter),
    "Flash Card": (lambda: _shop(_run("Flash Card")),   # reroll_shop 2404
                   lambda g: g.step(Action(ActionType.REROLL)),
                   lambda g: _joker(g, "Flash Card").counter),
    "Red Card": (lambda: _shop(_run("Red Card")),   # skipping_booster 2442
                 lambda g: (g._open_pack(BY_KEY["p_buffoon_normal_1"]),
                            g.step(Action(ActionType.SKIP_PACK))),
                 lambda g: _joker(g, "Red Card").counter),
    "Perkeo": (lambda: _holding_the_fool(_run("Perkeo")),   # ending_shop 2413
               lambda g: (setattr(g, "shop", None), g._leave_shop()),
               lambda g: len(g.consumables)),
    "Diet Cola": (lambda: _run("Diet Cola"),        # selling_self 2361
                  lambda g: g.step(Action(ActionType.SELL_JOKER, index=0)),
                  lambda g: g.tags.count(Tag.DOUBLE)),
    "DNA": (lambda: _run("DNA"),                    # before 3501
            lambda g: g.step(Action(ActionType.PLAY, cards=(0,))),
            lambda g: len(g.full_deck)),
    "Vagabond": (lambda: _run("Vagabond"),          # joker_main 3743
                 _vagabond,
                 lambda g: len(g.consumables)),
}


def _hallucination():
    game = GameState(seed="LC4JWH61", deck="Nebula Deck")
    game.ante = 3
    game._open_shop()
    game.shop.slots = []
    game.gain_joker(JokerInstance(JOKERS["Hallucination"]))
    return game


CONTEXTS["Hallucination"] = (                       # open_booster 2336
    _hallucination,
    lambda g: g._open_pack(BY_KEY["p_arcana_normal_1"]),
    lambda g: len(g.consumables))


def _context(name, debuffed):
    build, act, read = CONTEXTS[name]
    game = build()
    if debuffed:
        game.set_joker_debuff(_joker(game, name), True)
    before = read(game)
    act(game)
    return before, read(game)


@pytest.mark.parametrize("name", sorted(CONTEXTS))
def test_a_debuffed_joker_answers_no_other_context(name):
    """card.lua:2292, for every context outside the discard and the round's
    end; each name is commented with its branch's card.lua line."""
    before, after = _context(name, debuffed=True)
    assert after == before


@pytest.mark.parametrize("name", sorted(CONTEXTS))
def test_a_live_joker_answers_it(name):
    before, after = _context(name, debuffed=False)
    assert after != before


def test_a_perished_certificate_makes_no_card():
    """first_hand_drawn (game.lua:3229) reaches Certificate (card.lua:2463)
    through the guard at card.lua:2292."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["Certificate"], perishable=True,
                                  perish_tally=0, debuffed=True))
    game._start_round()
    assert len(game.hand) == game.hand_size


# -- copiers ----------------------------------------------------------------

def _score(row, debuff_at):
    game = _run(*row)
    if debuff_at is not None:
        game.set_joker_debuff(game.jokers[debuff_at], True)
    return game.preview_score((0, 1, 2, 3, 4))


def test_blueprint_beside_a_debuffed_joker_copies_nothing():
    """Blueprint calls G.jokers.cards[i+1]:calculate_joker (card.lua:2305-2314),
    which is nil for a debuffed one -- it does not reach past it to the next."""
    assert _score(("Blueprint", "Joker", "Joker"), 1) == _score(("Joker",), None)


def test_brainstorm_on_a_debuffed_first_joker_copies_nothing():
    """Brainstorm calls G.jokers.cards[1]:calculate_joker (card.lua:2318-2327)."""
    assert _score(("Joker", "Joker", "Brainstorm"), 0) == _score(("Joker",), None)


def test_a_debuffed_blueprint_copies_nothing():
    assert _score(("Blueprint", "Joker"), 0) == _score(("Joker",), None)


# -- find_joker and add_to_deck ---------------------------------------------

FOUND = [
    ("Four Fingers", lambda g: g._four_fingers()),      # misc_functions.lua:524
    ("Shortcut", lambda g: g._shortcut()),              # misc_functions.lua:567
    ("Splash", lambda g: g._splash()),                  # state_events.lua:583
    ("Pareidolia", lambda g: g.has_pareidolia()),       # card.lua:967
    ("Smeared Joker", lambda g: g.has_smeared()),       # card.lua:4072
    ("Oops! All 6s", lambda g: g.probability_scale() > 1),  # card.lua:665
]


@pytest.mark.parametrize("name,probe", FOUND, ids=[f[0] for f in FOUND])
def test_a_debuffed_rule_joker_is_not_found(name, probe):
    """find_joker(name) skips `v.debuff` (misc_functions.lua:907); Oops! All 6s
    halves the probabilities back in remove_from_deck (card.lua:665-669)."""
    game = _run(name)
    assert probe(game)
    _debuff(game, name)
    assert not probe(game)


def test_a_debuffed_chicot_leaves_the_boss_alone():
    """Chicot disables the boss from setting_blind (card.lua:2492) and
    add_to_deck (card.lua:596); both are shut to a debuffed one."""
    game = _run("Chicot")
    game.blind = make_blind(BlindKind.BOSS, game.ante, HEART)
    assert game.boss is None
    _debuff(game, "Chicot")
    assert game.boss is not None


# -- what the guard does not cover ------------------------------------------

def test_a_debuffed_rental_still_pays_its_rent():
    """calculate_rental has no debuff check (card.lua:2271-2276) and end_round
    calls it for every joker (state_events.lua:108)."""
    game = _run("Joker")
    joker = _joker(game, "Joker")
    joker.rental = True
    game.set_joker_debuff(joker, True)
    game.blind = make_blind(BlindKind.SMALL, game.ante)
    money = game.money
    game._beat_blind()
    assert game.money == money - 3


def test_a_debuffed_perishable_still_counts_down():
    """calculate_perishable has no debuff check (card.lua:2278-2289)."""
    game = _run("Joker")
    joker = _joker(game, "Joker")
    joker.perishable, joker.perish_tally = True, 3
    game.set_joker_debuff(joker, True)
    game.blind = make_blind(BlindKind.SMALL, game.ante)
    game._beat_blind()
    assert joker.perish_tally == 2


def test_gift_card_still_raises_a_debuffed_jokers_value():
    """Gift Card walks every G.jokers.cards (card.lua:2993-2994) and sets
    extra_value whatever its debuff."""
    game = _run("Gift Card", "Joker")
    joker = _debuff(game, "Joker")
    game.blind = make_blind(BlindKind.SMALL, game.ante)
    game._beat_blind()
    assert joker.extra_sell_value == 1


def test_a_debuffed_negative_keeps_its_slot():
    """remove_from_deck(true) only queues the Negative's removal
    (card.lua:687-689)."""
    game = _run("Joker")
    slots = game.joker_slots
    joker = _joker(game, "Joker")
    joker.edition = Edition.NEGATIVE
    assert game.joker_slots == slots + 1
    game.set_joker_debuff(joker, True)
    assert game.joker_slots == slots + 1
