"""Hallucination's Tarot is built after the pack's own cards, not before.

Card:open (card.lua:1721-1798) does everything inside one event. It calls
self:explode() first, then queues the event that fills the pack (card.lua:1725,
`blockable = false, blocking = false`, delay 1.3*sqrt(GAMESPEED)), then runs
the open_booster joker context (card.lua:1796-1798). Hallucination rolls
'halu'..ante there and then (card.lua:2337), but only *queues* the Tarot: an
ordinary blockable 'before' event (card.lua:2339-2348).

That event cannot run yet. Card:explode queued its own events ahead of both,
all `blockable = false` with the default `blocking = true`, and the last of
them only finishes at 1.5*explode_time = 1.95*sqrt(GAMESPEED)
(card.lua:2002-2012, 2071-2075). Being unblockable they run anyway and block
everything behind them (engine/event.lua:182-185). The pack fill is
unblockable too, so it goes at 1.3*sqrt(GAMESPEED); the Tarot waits until
1.95*sqrt(GAMESPEED). Both scale the same way, so the order holds at any
game speed.

A card marks its centre used as it is built (card.lua:352), so by the time
Hallucination draws, the pack's Tarots are blanked from its pool
(common_events.lua:1987) and the resample moves it on. The simulator made the
Tarot first, which blanked it from the *pack* instead. Seed LC4JWH61, Nebula
Deck, stake 7, ante 3: the game gave The Empress with Strength, The Tower and
The Chariot in the pack; the simulator gave Strength with The Hermit, The
Tower and The Chariot.
"""

from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY, JokerInstance
from jimbot_sim.shop import BY_KEY


def _open_arcana_with_hallucination():
    game = GameState(seed="LC4JWH61", deck="Nebula Deck")
    game.ante = 3
    game._open_shop()
    game.shop.slots = []
    game.gain_joker(JokerInstance(REGISTRY["Hallucination"]))
    game._open_pack(BY_KEY["p_arcana_normal_1"])
    return game


def test_the_pack_is_filled_before_hallucination_draws():
    game = _open_arcana_with_hallucination()
    assert [o.name for o in game.pack_options] == [
        "Strength", "The Tower", "The Chariot"]
    assert [c.name for c in game.consumables] == ["The Empress"]


def test_hallucination_never_duplicates_a_card_in_the_pack():
    game = _open_arcana_with_hallucination()
    offered = {o.name for o in game.pack_options}
    assert not offered & {c.name for c in game.consumables}
