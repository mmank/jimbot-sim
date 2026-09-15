"""Crimson Heart debuffs a joker when a hand is drawn, not when one is played.

Blind:set_blind marks the blind prepped (blind.lua:94, inside `if not reset`),
Blind:press_play marks it again whenever a hand is played with a joker held
(blind.lua:488-493), and the pick itself is in Blind:drawn_to_hand
(blind.lua:588-602):

    if self.name == 'Crimson Heart' and self.prepped and G.jokers.cards[1] then
        local jokers = {}
        for i = 1, #G.jokers.cards do
            if not G.jokers.cards[i].debuff or #G.jokers.cards < 2 then
                jokers[#jokers+1] = G.jokers.cards[i] end
            G.jokers.cards[i]:set_debuff(false)
        end
        local _card = pseudorandom_element(jokers, pseudoseed('crimson_heart'))
        if _card then _card:set_debuff(true) ... end
    end
    ...
    self.prepped = nil

drawn_to_hand runs from update_draw_to_hand (game.lua:3238), after every deal:
the opening hand, the draw after a played hand, the draw after a discard. So
the first joker goes the moment the blind is selected, a discard moves nothing
(prepped is nil by then), a played hand moves it on the draw that follows, and
the hand that wins the round draws nothing. The simulator picked in _play just
before scoring -- the same draws off the same stream, each one a step late,
and nothing on the row between hands. Smoke run N1OA90W1 (Abandoned Deck,
stake 1) stopped on it at decision 188, Stencil debuffed in the game straight
after select_blind and nothing in the shadow; RRT5KY7W (Yellow Deck) at 185,
with Banner.

Two things take it off again. Blind:disable asks debuff_card of every joker
(blind.lua:410-412), which a disabled Crimson Heart no longer holds
(blind.lua:647-651); and Blind:defeat's set_blind(nil) (blind.lua:336,
211-213) does the same for the empty blind the round leaves behind.

set_debuff moves a joker in and out of the deck (card.lua:526-538), so a
hand-size joker it lets go deals into the hand (cardarea.lua:94-111).
"""

import pytest

from jimbot_sim import game as game_module
from jimbot_sim import shop_pool
from jimbot_sim.blinds import FINISHER_BOSSES, BlindKind, make_blind
from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.jokers import REGISTRY, JokerInstance

HEART = next(b for b in FINISHER_BOSSES if b.name == "Crimson Heart")
ROW = ("Juggler", "Greedy Joker", "Joker", "Lusty Joker")


def _selected(seed="CRIMSON1", names=ROW):
    """Crimson Heart on deck, the row built in order, the blind just selected."""
    game = GameState(seed=seed, deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(REGISTRY[name]))
    game.blind_index = 2
    game.blind = make_blind(BlindKind.BOSS, game.ante, HEART)
    game.step(Action(ActionType.SELECT_BLIND))
    game.blind.target = 10 ** 12
    return game


def _debuffed(game):
    return [j.name for j in game.jokers if j.debuffed]


def _spy_on_scoring(monkeypatch, seen):
    real = game_module.score_hand

    def spy(game, *args, **kwargs):
        seen.append(_debuffed(game))
        return real(game, *args, **kwargs)

    monkeypatch.setattr(game_module, "score_hand", spy)


def test_the_first_joker_goes_when_the_opening_hand_is_dealt():
    game = _selected()
    assert game.phase is Phase.PLAYING
    assert len(_debuffed(game)) == 1, (
        "nothing debuffed after selecting Crimson Heart: the pick waited for "
        "the first hand to be played")


def test_a_discard_moves_nothing():
    game = _selected()
    before = _debuffed(game)
    game.step(Action(ActionType.DISCARD, cards=(0,)))
    assert len(before) == 1
    assert _debuffed(game) == before


def test_a_played_hand_moves_it_on_the_draw_that_follows():
    game = _selected()
    first = _debuffed(game)
    game.step(Action(ActionType.PLAY, cards=(0,)))
    second = _debuffed(game)
    assert len(first) == 1 and len(second) == 1
    assert second != first, "the pick is drawn from the jokers not debuffed"


def test_the_hand_scores_against_the_joker_the_deal_debuffed(monkeypatch):
    game = _selected()
    held = _debuffed(game)
    seen = []
    _spy_on_scoring(monkeypatch, seen)
    game.step(Action(ActionType.PLAY, cards=(0,)))
    assert len(held) == 1
    assert seen == [held]


def test_selling_the_debuffed_joker_leaves_the_hand_with_none(monkeypatch):
    """Nothing is drawn between the sale and the play, so nothing is picked."""
    game = _selected()
    debuffed = [i for i, j in enumerate(game.jokers) if j.debuffed]
    assert len(debuffed) == 1
    game.step(Action(ActionType.SELL_JOKER, index=debuffed[0]))
    seen = []
    _spy_on_scoring(monkeypatch, seen)
    game.step(Action(ActionType.PLAY, cards=(0,)))
    assert seen == [[]]


def test_the_winning_hand_draws_nothing_and_the_round_releases_it():
    game = _selected()
    game.blind.target = 1
    game.step(Action(ActionType.PLAY, cards=(0,)))
    assert game.phase is Phase.ROUND_EVAL
    assert _debuffed(game) == []


def test_releasing_a_hand_size_joker_deals_into_the_hand():
    """Measured on the engine: nine dealt, Juggler taken, nine under eight;
    after a one-card play Juggler comes back and deals its card."""
    game = _selected()
    assert _debuffed(game) == ["Juggler"]
    assert (len(game.hand), game.hand_size) == (9, 8)
    game.step(Action(ActionType.PLAY, cards=(0,)))
    assert "Juggler" not in _debuffed(game)
    assert (len(game.hand), game.hand_size) == (9, 9)


def test_disabling_the_blind_releases_the_joker():
    game = _selected(names=("Joker", "Greedy Joker", "Luchador",
                            "Lusty Joker"))
    assert len(_debuffed(game)) == 1 and "Luchador" not in _debuffed(game)
    names = [j.name for j in game.jokers]
    game.step(Action(ActionType.SELL_JOKER, index=names.index("Luchador")))
    assert _debuffed(game) == []
    game.step(Action(ActionType.PLAY, cards=(0,)))
    assert _debuffed(game) == [], "a disabled Crimson Heart picked again"


def test_a_debuffed_joker_takes_its_discard_away_once():
    """set_debuff(true) runs remove_from_deck(true) (card.lua:535), which
    takes Drunkard's discard off the round (card.lua:650-653). Selling it
    afterwards takes nothing more: remove_from_deck opens on added_to_deck
    (card.lua:646). A lone joker is picked even when already debuffed
    (blind.lua:591)."""
    plain = _selected(names=("Joker",))
    game = _selected(names=("Drunkard",))
    assert _debuffed(game) == ["Drunkard"]
    assert game.discards_left == plain.discards_left
    game.step(Action(ActionType.SELL_JOKER, index=0))
    assert game.discards_left == plain.discards_left


# -- against the engine ------------------------------------------------------

_ENGINE_ROW = (
    '(function() local t = {} for _, c in ipairs(G.jokers.cards) do '
    'if c.debuff then t[#t+1] = c.config.center.key end end '
    'return table.concat(t, " ").."|"..#G.hand.cards.."/"'
    '..G.hand.config.card_limit end)()')


def _sim_row(game):
    keys = " ".join(shop_pool.KEY_BY_JOKER_NAME[n] for n in _debuffed(game))
    return "%s|%d/%d" % (keys, len(game.hand), game.hand_size)


@pytest.mark.slow
def test_the_engine_moves_it_at_the_same_moments():
    pytest.importorskip("lupa")
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
    from jimbot_sim.headless.scenario import Scenario

    if not engine_available():
        pytest.skip("no Balatro engine: need vendor/balatro_src")
    engine = HeadlessBalatro().boot()
    engine.execute('BOT.start_run({"CRIMSON1","Red_Deck",1})')
    engine.execute("api.pump(300)")
    Scenario(engine).jokers([shop_pool.KEY_BY_JOKER_NAME[n] for n in ROW])
    engine.execute(
        "G.GAME.round_resets.blind_choices.Boss = 'bl_final_heart'; "
        "G.GAME.round_resets.blind_states.Small = 'Defeated'; "
        "G.GAME.round_resets.blind_states.Big = 'Defeated'")
    engine.execute("api.select_blind(); api.pump(120)")
    engine.execute("G.GAME.blind.chips = 999999999")
    seen = [engine.eval(_ENGINE_ROW)]
    engine.execute("api.discard({1}); api.pump(60)")
    seen.append(engine.eval(_ENGINE_ROW))
    for _ in range(2):
        engine.execute("api.play({1}); api.pump(60)")
        seen.append(engine.eval(_ENGINE_ROW))
    engine.execute("G.GAME.blind.chips = 1; api.highlight({1}); "
                   "G.FUNCS.play_cards_from_highlighted(); api.pump(600)")
    seen.append(engine.eval(_ENGINE_ROW).split("|")[0])

    game = _selected()
    sim = [_sim_row(game)]
    game.step(Action(ActionType.DISCARD, cards=(0,)))
    sim.append(_sim_row(game))
    for _ in range(2):
        game.step(Action(ActionType.PLAY, cards=(0,)))
        sim.append(_sim_row(game))
    game.blind.target = 1
    game.step(Action(ActionType.PLAY, cards=(0,)))
    sim.append(_sim_row(game).split("|")[0])

    assert sim == seen


@pytest.mark.slow
def test_the_engine_takes_a_debuffed_jokers_discard_once():
    pytest.importorskip("lupa")
    from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available
    from jimbot_sim.headless.scenario import Scenario

    if not engine_available():
        pytest.skip("no Balatro engine: need vendor/balatro_src")
    engine = HeadlessBalatro().boot()
    engine.execute('BOT.start_run({"CRIMSON1","Red_Deck",1})')
    engine.execute("api.pump(300)")
    Scenario(engine).jokers(["j_drunkard"])
    engine.execute(
        "G.GAME.round_resets.blind_choices.Boss = 'bl_final_heart'; "
        "G.GAME.round_resets.blind_states.Small = 'Defeated'; "
        "G.GAME.round_resets.blind_states.Big = 'Defeated'")
    engine.execute("api.select_blind(); api.pump(120)")
    discards = "G.GAME.current_round.discards_left"
    seen = [engine.eval(_ENGINE_ROW), int(engine.eval(discards))]
    engine.execute("api.sell('jokers', 1)")
    seen.append(int(engine.eval(discards)))

    game = _selected(names=("Drunkard",))
    sim = [_sim_row(game), game.discards_left]
    game.step(Action(ActionType.SELL_JOKER, index=0))
    sim.append(game.discards_left)
    assert sim == seen
