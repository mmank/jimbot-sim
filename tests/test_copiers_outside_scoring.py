"""A Blueprint or a Brainstorm copies outside the scoring of a hand too.

Card:calculate_joker gives every context to the copied joker
(card.lua:2304-2333):

    context.blueprint = (context.blueprint and (context.blueprint + 1)) or 1
    context.blueprint_card = context.blueprint_card or self
    local other_joker_ret = other_joker:calculate_joker(context)

So a copy answers whatever the copied joker answers -- a discard, a pack
opened, the shop closing, the copier itself being sold -- unless the copied
joker's branch says `not context.blueprint`. The simulator ran every hook
outside scoring on the joker's own spec only, so a Blueprint copied none of
them.

Each case is measured on the engine and pinned to the number the Lua gives;
the fast test holds the simulator to the same number. The comment on each
case names the branch that decides it.
"""

import pytest

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Rank, Suit
from jimbot_sim.consumables import REGISTRY as CONSUMABLES
from jimbot_sim.game import Action, ActionType, GameState, Tag
from jimbot_sim.hands import HandType
from jimbot_sim.headless.scenario import Scenario
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance
from jimbot_sim.shop import BY_KEY

try:
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
except ImportError:          # no lupa: the simulator half still runs
    HeadlessBalatro, engine_available = None, lambda: False

_RANKS = {"2": Rank.TWO, "3": Rank.THREE, "4": Rank.FOUR, "5": Rank.FIVE,
          "6": Rank.SIX, "7": Rank.SEVEN, "8": Rank.EIGHT, "9": Rank.NINE,
          "T": Rank.TEN, "J": Rank.JACK, "Q": Rank.QUEEN, "K": Rank.KING,
          "A": Rank.ACE}
_SUITS = {"S": Suit.SPADES, "H": Suit.HEARTS, "D": Suit.DIAMONDS,
          "C": Suit.CLUBS}

HAND = "S_A H_A D_A C_K S_Q H_J D_9 C_2"
STRAIGHT = "S_A H_K D_Q C_J S_T D_9 C_2 H_3"
STRAIGHT_FLUSH = "S_9 S_K S_Q S_J S_T D_9 C_2 H_3"
SIX = "S_6 H_A D_A C_K S_Q H_J D_9 C_2"


# -- engine side ------------------------------------------------------------

def _read(engine, expr):
    return engine.eval("(function() return %s end)()" % expr)


def _e_round(engine, jokers, hand=HAND, lua=""):
    scene = Scenario(engine).start().hand(hand).jokers(jokers)
    if lua:
        engine.execute(lua + "; api.pump(10)")
    return scene


def _e_discard(engine, jokers, picks, read, lua=""):
    _e_round(engine, jokers, lua=lua)
    before = _read(engine, read)
    engine.execute("api.discard({%s}); api.pump(60)"
                   % ",".join(str(i) for i in picks))
    return _read(engine, read) - before


def _e_play(engine, jokers, hand, picks, lua=""):
    _e_round(engine, jokers, hand, lua).play(picks)
    engine.execute("api.pump(60)")
    return int(_read(engine, "#G.consumeables.cards"))


def _e_win(engine):
    engine.execute("G.GAME.blind.chips = 1; api.highlight({1}); "
                   "G.FUNCS.play_cards_from_highlighted()")
    engine.execute("api.pump_until(function() "
                   "return G.STATE == G.STATES.ROUND_EVAL end); api.pump(600)")


def _e_shop(engine, jokers):
    Scenario(engine).start()
    _e_win(engine)
    engine.execute("api.cash_out(); G.GAME.dollars = 100")
    Scenario(engine).jokers(jokers)


def _e_sell(engine, jokers, read, boss=None, lua=""):
    scene = Scenario(engine).start()
    if boss:
        scene.boss(boss)
    scene.jokers(jokers)
    if lua:
        engine.execute(lua)
    engine.execute("api.sell('jokers', 1); api.pump(60)")
    return int(_read(engine, read))


def _e_certificate(engine):
    engine.execute('BOT.start_run({"TESTSEED","Red_Deck",1})')
    engine.execute("api.pump(300)")
    Scenario(engine).jokers("j_blueprint j_certificate")
    engine.execute("api.select_blind(); api.pump(120)")
    return int(_read(engine, "#G.hand.cards"))


def _e_pack(engine):
    _e_shop(engine, "j_blueprint j_hallucination j_blueprint j_red_card")
    engine.execute("G.GAME.probabilities.normal = 2; "
                   "api.buy('shop_booster', 1)")
    engine.execute("api.pump(60)")
    made = int(_read(engine, "#G.consumeables.cards"))
    engine.execute("api.skip_pack()")
    return made, int(_read(engine, "G.jokers.cards[4].ability.mult"))


def _e_leave_shop(engine):
    _e_shop(engine, "j_perkeo j_blueprint j_flash j_brainstorm")
    engine.execute('local c = create_card("Tarot", G.consumeables, nil, nil, '
                   'nil, nil, "c_fool"); c:add_to_deck(); '
                   'G.consumeables:emplace(c); api.pump(30)')
    engine.execute("api.reroll()")
    flash = int(_read(engine, "G.jokers.cards[3].ability.mult"))
    engine.execute("api.leave_shop(); api.pump(60)")
    return flash, int(_read(engine, "#G.consumeables.cards"))


def _e_round_end(engine):
    _e_round(engine, "j_blueprint j_egg j_blueprint j_gift")
    _e_win(engine)
    return [int(_read(engine, "G.jokers.cards[%d].ability.extra_value or 0"
                      % i)) for i in range(1, 5)]


def _e_cash_out(engine):
    paid = []
    for jokers in ("j_golden", "j_blueprint j_golden"):
        _e_round(engine, jokers)
        _e_win(engine)
        before = _read(engine, "G.GAME.dollars")
        engine.execute("api.cash_out()")
        paid.append(_read(engine, "G.GAME.dollars") - before)
    return paid[1] - paid[0]


def _e_sixth_sense(engine):
    made = _e_play(engine, "j_blueprint j_sixth_sense", SIX, [1])
    return made, int(_read(engine, "#G.playing_cards"))


# -- simulator side ---------------------------------------------------------

def _run(*names, hand=HAND):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    game.blind.target = 10 ** 9
    for card, code in zip(game.hand, hand.split()):
        suit, rank = code.split("_")
        card.rank, card.suit = _RANKS[rank], _SUITS[suit]
    return game


def _joker(game, name):
    return next(j for j in game.jokers if j.name == name)


def _s_discard(names, picks, read, setup=None):
    game = _run(*names)
    if setup:
        setup(game)
    before = read(game)
    game._discard(tuple(i - 1 for i in picks))
    return read(game) - before


def _s_play(names, hand, picks, setup=None):
    game = _run(*names, hand=hand)
    if setup:
        setup(game)
    game.step(Action(ActionType.PLAY, cards=tuple(i - 1 for i in picks)))
    return len(game.consumables)


def _s_sell(names, read, setup=None):
    game = _run(*names)
    if setup:
        setup(game)
    game.step(Action(ActionType.SELL_JOKER, index=0))
    return read(game)


def _s_certificate():
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in ("Blueprint", "Certificate"):
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    return len(game.hand)


def _s_shop(*names):
    game = _run(*names)
    game.money = 100
    game._open_shop()
    return game


def _s_pack():
    game = _s_shop("Blueprint", "Hallucination", "Blueprint", "Red Card")
    game.probability_scale = lambda: 2
    game._open_pack(BY_KEY["p_buffoon_normal_1"])
    made = len(game.consumables)
    game.step(Action(ActionType.SKIP_PACK))
    return made, int(_joker(game, "Red Card").counter)


def _s_leave_shop():
    game = _s_shop("Perkeo", "Blueprint", "Flash Card", "Brainstorm")
    game.consumables.append(game.hold_consumable(CONSUMABLES["The Fool"]))
    game.step(Action(ActionType.REROLL))
    flash = int(_joker(game, "Flash Card").counter)
    game._leave_shop()
    return flash, len(game.consumables)


def _s_round_end():
    game = _run("Blueprint", "Egg", "Blueprint", "Gift Card")
    game.blind = make_blind(BlindKind.SMALL, game.ante)
    game._beat_blind()
    return [j.extra_sell_value for j in game.jokers]


def _s_cash_out():
    paid = []
    for names in (("Golden Joker",), ("Blueprint", "Golden Joker")):
        game = _run(*names)
        game.blind = make_blind(BlindKind.SMALL, game.ante)
        game._beat_blind()
        before = game.money
        game._cash_out()
        paid.append(game.money - before)
    return paid[1] - paid[0]


def _s_sixth_sense():
    game = _run("Blueprint", "Sixth Sense", hand=SIX)
    game.step(Action(ActionType.PLAY, cards=(0,)))
    return len(game.consumables), len(game.full_deck)


def _mail_ace(game):
    game.mail_rank = Rank.ACE


def _charge_invisible(game):
    _joker(game, "Invisible Joker").counter = 2


def _club(game):
    game.blind = make_blind(BlindKind.BOSS, game.ante,
                            next(b for b in BOSSES if b.name == "The Club"))


def _money(game):
    return game.money


def _pair_level(game):
    return game.hand_levels.levels[HandType.PAIR]


# name: (engine, simulator, what the Lua gives)
CASES = {
    # discard, card.lua:2825 -- no guard: $5 an ace, twice.
    "discard Mail-In Rebate": (
        lambda e: _e_discard(e, "j_blueprint j_mail", [1, 2, 3],
                             "G.GAME.dollars",
                             "G.GAME.current_round.mail_card.id = 14"),
        lambda: _s_discard(("Blueprint", "Mail-In Rebate"), [1, 2, 3],
                           _money, _mail_ace),
        30),
    # discard, card.lua:2858 -- no guard: $5 for three faces, twice.
    "discard Faceless Joker": (
        lambda e: _e_discard(e, "j_blueprint j_faceless", [4, 5, 6],
                             "G.GAME.dollars"),
        lambda: _s_discard(("Blueprint", "Faceless Joker"), [4, 5, 6],
                           _money),
        10),
    # pre_discard, card.lua:2749 -- no guard: the pair levels twice.
    "pre_discard Burnt Joker": (
        lambda e: _e_discard(e, "j_blueprint j_burnt", [1, 2],
                             "G.GAME.hands['Pair'].level"),
        lambda: _s_discard(("Blueprint", "Burnt Joker"), [1, 2],
                           _pair_level),
        2),
    # discard, card.lua:2757 `not context.blueprint`: X0.01 a card, once.
    "discard Ramen, not copied": (
        lambda e: round(_e_discard(e, "j_blueprint j_ramen", [1, 2, 3, 4, 5],
                                   "G.jokers.cards[2].ability.x_mult"), 2),
        lambda: round(_s_discard(("Blueprint", "Ramen"), [1, 2, 3, 4, 5],
                                 lambda g: _joker(g, "Ramen").counter), 2),
        -0.05),
    # discard, card.lua:2802 `not context.blueprint`: $3 and one card gone.
    "discard Trading Card, not copied": (
        lambda e: _e_discard(e, "j_blueprint j_trading", [8],
                             "G.GAME.dollars * 100 + #G.playing_cards"),
        lambda: _s_discard(("Blueprint", "Trading Card"), [8],
                           lambda g: g.money * 100 + len(g.full_deck)),
        299),
    # joker_main, card.lua:3743 -- no guard: a Tarot each.
    "after hand Vagabond": (
        lambda e: _e_play(e, "j_blueprint j_vagabond", HAND, [8],
                          "G.GAME.dollars = 0"),
        lambda: _s_play(("Blueprint", "Vagabond"), HAND, [8],
                        lambda g: setattr(g, "money", 0)),
        2),
    # joker_main, card.lua:3762 -- no guard.
    "after hand Superposition": (
        lambda e: _e_play(e, "j_blueprint j_superposition", STRAIGHT,
                          [1, 2, 3, 4, 5]),
        lambda: _s_play(("Blueprint", "Superposition"), STRAIGHT,
                        [1, 2, 3, 4, 5]),
        2),
    # joker_main, card.lua:3787 -- no guard.
    "after hand Seance": (
        lambda e: _e_play(e, "j_blueprint j_seance", STRAIGHT_FLUSH,
                          [1, 2, 3, 4, 5]),
        lambda: _s_play(("Blueprint", "Séance"), STRAIGHT_FLUSH,
                        [1, 2, 3, 4, 5]),
        2),
    # destroying_card, card.lua:2603 `and not context.blueprint`: one
    # Spectral, one six destroyed.
    "before hand Sixth Sense, not copied": (
        _e_sixth_sense, _s_sixth_sense, (1, 51)),
    # first_hand_drawn, card.lua:2463 -- no guard: two cards over eight.
    "first hand drawn Certificate": (_e_certificate, _s_certificate, 10),
    # open_booster, card.lua:2336 -- no guard: two Tarots. skipping_booster,
    # card.lua:2442 `not context.blueprint`: +3 once.
    "open and skip a pack": (_e_pack, _s_pack, (2, 3)),
    # reroll_shop, card.lua:2404 `not context.blueprint`: +2 once.
    # ending_shop, card.lua:2413 -- no guard: two Negative copies of the Fool.
    "reroll and leave the shop": (_e_leave_shop, _s_leave_shop, (2, 3)),
    # selling_self is the sold card's own (card.lua:1599), and a sold
    # Blueprint copies it: Diet Cola card.lua:2361, no guard.
    "sell a Blueprint on Diet Cola": (
        lambda e: _e_sell(e, "j_blueprint j_diet_cola", "#G.GAME.tags"),
        lambda: _s_sell(("Blueprint", "Diet Cola"),
                        lambda g: g.tags.count(Tag.DOUBLE)),
        1),
    # Luchador card.lua:2355, no guard.
    "sell a Blueprint on Luchador": (
        lambda e: _e_sell(e, "j_blueprint j_luchador",
                          "G.GAME.blind.disabled and 1 or 0", boss="bl_club"),
        lambda: _s_sell(("Blueprint", "Luchador"),
                        lambda g: int(g.blind.disabled), _club),
        1),
    # Invisible Joker card.lua:2371 `not context.blueprint`.
    "sell a Blueprint on Invisible Joker, not copied": (
        lambda e: _e_sell(e, "j_blueprint j_invisible", "#G.jokers.cards",
                          lua="G.jokers.cards[2].ability.invis_rounds = 2"),
        lambda: _s_sell(("Blueprint", "Invisible Joker"),
                        lambda g: len(g.jokers), _charge_invisible),
        1),
    # end_of_round, card.lua:2888 `elseif not context.blueprint`: Egg +3 and
    # Gift Card +1 all round, once each.
    "round end Egg and Gift Card, not copied": (
        _e_round_end, _s_round_end, [1, 4, 1, 1]),
    # calculate_dollar_bonus (card.lua:1655) is not calculate_joker at all.
    "cash out Golden Joker, not copied": (_e_cash_out, _s_cash_out, 0),
}


@pytest.fixture(scope="module")
def engine():
    return HeadlessBalatro().boot()


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_simulator_copies_what_the_game_copies(name):
    _, simulate, expected = CASES[name]
    assert simulate() == expected


@pytest.mark.slow
@pytest.mark.skipif(not engine_available(),
                    reason="no Balatro engine: need vendor/balatro_src")
@pytest.mark.parametrize("name", sorted(CASES))
def test_the_engine_gives_those_numbers(engine, name):
    measure, _, expected = CASES[name]
    assert measure(engine) == expected
