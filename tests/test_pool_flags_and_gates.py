"""Cavendish only after Gros Michel dies, and a gated joker only with its card.

Two gates in get_current_pool (common_events.lua:2012-2028) decide whether a
joker can be rolled at all, and neither is on the joker's text:

  * `yes_pool_flag` / `no_pool_flag`. Gros Michel carries
    no_pool_flag 'gros_michel_extinct' and Cavendish yes_pool_flag for the
    same name; card.lua:3037 sets the flag in the branch where Gros Michel
    goes extinct. The simulator destroyed it without setting the flag, and
    passed no flags to any stocking call either -- so Cavendish never
    appeared. Marcin's live run of QWEFRTUZ, Blue Deck, stake 5 stopped on
    it after a reroll: Cavendish in the game, Delayed Gratification here.

  * `enhancement_gate`. Lucky Cat needs a Lucky card somewhere in
    G.playing_cards, Glass Joker a Glass card, and so on. The shop, the
    packs and Judgement checked it; the Uncommon and Rare Tags did not.
"""

from jimbot_sim import shop_pool
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY, JokerInstance, _gros_michel_end

GROS_MICHEL = shop_pool.KEY_BY_JOKER_NAME["Gros Michel"]


def test_extinction_sets_the_flag(monkeypatch):
    game = GameState(seed="QWEFRTUZ", deck="Blue Deck")
    michel = JokerInstance(REGISTRY["Gros Michel"])
    game.gain_joker(michel)
    monkeypatch.setattr(game.rng, "chance", lambda *a, **k: True)
    _gros_michel_end(michel, game)
    assert "gros_michel_extinct" in game.pool_flags
    assert michel not in game.jokers


def test_surviving_does_not(monkeypatch):
    game = GameState(seed="QWEFRTUZ", deck="Blue Deck")
    michel = JokerInstance(REGISTRY["Gros Michel"])
    game.gain_joker(michel)
    monkeypatch.setattr(game.rng, "chance", lambda *a, **k: False)
    _gros_michel_end(michel, game)
    assert "gros_michel_extinct" not in game.pool_flags


def test_the_flag_swaps_gros_michel_for_cavendish_in_the_pool():
    before = shop_pool.build_pool(1)
    after = shop_pool.build_pool(1, pool_flags={"gros_michel_extinct"})
    assert GROS_MICHEL in before and "j_cavendish" not in before
    assert "j_cavendish" in after and GROS_MICHEL not in after
    assert len(before) == len(after), "entries are blanked, never removed"


def test_the_shop_stocks_with_the_run_flags():
    game = GameState(seed="QWEFRTUZ", deck="Blue Deck")
    game.pool_flags.add("gros_michel_extinct")
    seen = set()
    for _ in range(400):
        game._open_shop()
        seen.update(s.joker.name for s in game.shop.slots if s.joker is not None)
        if "Cavendish" in seen:
            break
    assert "Cavendish" in seen and "Gros Michel" not in seen


def test_a_rare_tag_joker_respects_the_enhancement_gate(monkeypatch):
    game = GameState(seed="QWEFRTUZ", deck="Blue Deck")
    calls = []
    real = shop_pool.draw_joker

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(shop_pool, "draw_joker", spy)
    from jimbot_sim.game import Tag

    game.tags.append(Tag.RARE)
    game._open_shop()
    tag_calls = [k for k in calls if k.get("append") == "rta"]
    assert tag_calls, "the Rare Tag drew no joker"
    assert "owned_enhancements" in tag_calls[0]
    assert "pool_flags" in tag_calls[0]
