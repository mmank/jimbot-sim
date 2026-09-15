"""Lucky Cat grows X0.25 each time a scoring Lucky card triggers.

The simulator had the X Mult and nothing that grew it, so a Lucky Cat sat at
X1 for a whole run. The game grows it while the hand scores, in the joker pass
for each trigger of each scoring card (card.lua:3076-3081):

    if self.ability.name == 'Lucky Cat' and context.other_card.lucky_trigger
       and not context.blueprint then
        self.ability.x_mult = self.ability.x_mult + self.ability.extra

`lucky_trigger` is set by either roll: the +20 Mult one in get_chip_mult
(card.lua:987-990, pseudorandom('lucky_mult')) and the $20 one in
get_p_dollars (card.lua:1075-1078, pseudorandom('lucky_money')), both asked
through eval_card (common_events.lua:598, 608) before the jokers answer
(state_events.lua:692-699). The flag is cleared straight after that joker pass
(state_events.lua:700), inside the loop over repetitions (685), so:

  * a trigger where both rolls hit grows it once, not twice;
  * each retrigger rolls again and can grow it again -- Hanging Chad gives the
    first scoring card three chances;
  * the growth lands before joker_main, so the hand it happens in already
    scores the larger X Mult;
  * a Blueprint or Brainstorm copying it passes `context.blueprint`
    (card.lua:2310, 2324) and does not grow it, though it copies the X Mult;
  * a debuffed Lucky Cat answers nothing (card.lua:2292).

AWEFRTUZ (Blue Deck, stake 5) stopped on it at decision 70: Hanging Chad,
Raised Fist, Banner, Photograph, Lucky Cat, a Pair led by a Lucky Queen of
Hearts. Both sides drew the same numbers -- lucky_mult 0.3145, 0.7259, 0.1109
and lucky_money 0.1260, 0.7223, 0.0620 -- so the third trigger hit both. The
game's Lucky Cat went to X1.25 and cleared The House; the shadow's stayed at
X1, 925 chips short.
"""

import pytest

from jimbot_sim.cards import Card, Enhancement, Rank, Suit, standard_deck
from jimbot_sim.game import GameState
from jimbot_sim.hands import evaluate
from jimbot_sim.jokers import make
from jimbot_sim.rng import RunRng
from jimbot_sim.scoring import score_hand

KEYS = {"Hanging Chad": "j_hanging_chad", "Lucky Cat": "j_lucky_cat",
        "Blueprint": "j_blueprint", "Brainstorm": "j_brainstorm"}


class _Rolls(RunRng):
    """Hands out chosen draws for the lucky pools, per pool, in order."""

    def __init__(self, **draws):
        super().__init__("LUCKYCAT")
        self.draws = {k: list(v) for k, v in draws.items()}

    def pseudorandom(self, key, low=None, high=None):
        if key in self.draws:
            return self.draws[key].pop(0)
        return super().pseudorandom(key, low, high)


HIT, MISS = 0.01, 0.99


def _played():
    lucky = Card(Rank.QUEEN, Suit.HEARTS, enhancement=Enhancement.LUCKY)
    return [lucky, Card(Rank.QUEEN, Suit.SPADES)]


def _score(game, played, held=()):
    held = list(held)
    game.hand = played + held
    game.full_deck = list(game.hand)
    result = evaluate(played, splash=game._splash(),
                      smeared=game.has_smeared(),
                      four_fingers=game._four_fingers(),
                      shortcut=game._shortcut())
    return score_hand(game, result, played, held)


def _cat(game):
    return next(j for j in game.jokers if j.name == "Lucky Cat")


def test_each_trigger_that_hits_grows_it_once():
    """Three Chad triggers: both rolls hit, neither, then the mult roll only.

    X1.5 after the hand, not X1.75 -- the double hit is one trigger.
    """
    game = GameState(seed=0)
    game.rng = _Rolls(lucky_mult=[HIT, MISS, HIT],
                      lucky_money=[HIT, MISS, MISS])
    game.jokers = [make("Hanging Chad"), make("Lucky Cat")]
    ctx = _score(game, _played())
    assert _cat(game).counter == pytest.approx(1.5), "\n".join(ctx.log)
    # Pair: 10 chips x 2 mult, Queens 10 + 10 + 10 + 10, two lucky +20s, and
    # the X1.5 it grew to during this very hand.
    assert ctx.score == int((10 + 40) * (2 + 40) * 1.5), "\n".join(ctx.log)
    assert ctx.money_gained == 20


def test_no_hit_no_growth():
    game = GameState(seed=0)
    game.rng = _Rolls(lucky_mult=[MISS], lucky_money=[MISS])
    game.jokers = [make("Lucky Cat")]
    _score(game, _played())
    assert _cat(game).counter == 1.0


def test_money_roll_alone_grows_it():
    game = GameState(seed=0)
    game.rng = _Rolls(lucky_mult=[MISS], lucky_money=[HIT])
    game.jokers = [make("Lucky Cat")]
    _score(game, _played())
    assert _cat(game).counter == pytest.approx(1.25)


@pytest.mark.parametrize("copier", ["Blueprint", "Brainstorm"])
def test_a_copy_does_not_grow_it(copier):
    """The copy passes context.blueprint, and still copies the X Mult."""
    game = GameState(seed=0)
    game.rng = _Rolls(lucky_mult=[HIT], lucky_money=[MISS])
    row = (["Blueprint", "Lucky Cat"] if copier == "Blueprint"
           else ["Lucky Cat", "Brainstorm"])
    game.jokers = [make(name) for name in row]
    ctx = _score(game, _played())
    assert _cat(game).counter == pytest.approx(1.25), "\n".join(ctx.log)
    assert ctx.score == int((10 + 20) * (2 + 20) * 1.25 * 1.25), \
        "\n".join(ctx.log)


def test_a_debuffed_lucky_cat_does_not_grow():
    game = GameState(seed=0)
    game.rng = _Rolls(lucky_mult=[HIT], lucky_money=[HIT])
    game.jokers = [make("Lucky Cat")]
    game.jokers[0].debuffed = True
    _score(game, _played())
    assert _cat(game).counter == 1.0


def test_a_preview_leaves_the_real_lucky_cat_alone():
    game = GameState(seed="LUCK0046")
    game.jokers = [make("Hanging Chad"), make("Lucky Cat")]
    game.hand = _played() + [Card(Rank.TWO, Suit.CLUBS)]
    game.full_deck = list(game.hand)
    _score_, row = game.preview_play((0, 1))
    assert _cat(game).counter == 1.0
    assert row[1] is not game.jokers[1]


# -- measured on the engine ----------------------------------------------------

# A seed whose first three draws from each lucky pool are, per trigger,
# (mult, money): (hit, hit), (miss, miss), (hit, miss).
SEED = "LUCK0046"
HAND = "H_Q S_Q D_2 C_5 H_7 S_9 D_3 C_4"


@pytest.fixture(scope="module")
def engine():
    pytest.importorskip("lupa")
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
    if not engine_available():
        pytest.skip("no Balatro engine: need vendor/balatro_src")
    return HeadlessBalatro().boot()


@pytest.mark.slow
@pytest.mark.parametrize("row", [("Hanging Chad", "Lucky Cat"),
                                 ("Hanging Chad", "Blueprint", "Lucky Cat")],
                         ids=" / ".join)
def test_lucky_cat_growth_matches_the_engine(engine, row):
    from jimbot_sim.headless.scenario import Scenario

    scene = Scenario(engine).start(seed=SEED).hand(HAND)
    scene.enhance(1, enhancement="m_lucky")
    scene.jokers(" ".join(KEYS[name] for name in row))
    scene.select((1, 2))
    seed = engine.eval(
        "(function() return tostring(G.GAME.pseudorandom.seed) end)()")
    pools = engine.eval(
        "(function() local t = {} "
        "for k, v in pairs(G.GAME.pseudorandom) do "
        "  if type(v) == 'number' then "
        "    t[#t+1] = k .. '=' .. string.format('%.17g', v) end "
        "end return table.concat(t, ' ') end)()")
    got = scene.play()
    cat = row.index("Lucky Cat") + 1
    engine_x = float(engine.eval(
        "(function() return G.jokers.cards[%d].ability.x_mult end)()" % cat))
    # The seed was chosen for two growing triggers; if the engine disagrees
    # the case no longer tests what it says.
    assert engine_x == pytest.approx(1.5)

    game = GameState(seed=0)
    game.rng = RunRng(seed)
    for entry in pools.split():
        key, _, value = entry.partition("=")
        if key != "hashed_seed":
            game.rng.pools[key] = float(value)
    game.jokers = [make(name) for name in row]
    codes = HAND.split()
    played = _played()
    held = [Card(r, s) for r, s in ((Rank.TWO, Suit.DIAMONDS),
                                    (Rank.FIVE, Suit.CLUBS),
                                    (Rank.SEVEN, Suit.HEARTS),
                                    (Rank.NINE, Suit.SPADES),
                                    (Rank.THREE, Suit.DIAMONDS),
                                    (Rank.FOUR, Suit.CLUBS))]
    assert len(codes) == len(played) + len(held)
    game.draw_pile = standard_deck()[:40]
    ctx = _score(game, played, held)
    assert _cat(game).counter == pytest.approx(engine_x), "\n".join(ctx.log)
    assert ctx.score == got, "\n".join(ctx.log)
