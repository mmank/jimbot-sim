"""Cerulean Bell's card stays selected through a consumable use, in the engine too.

Blind:drawn_to_hand (blind.lua:571-586) sets `ability.forced_selection` on one
held card and highlights it, and nothing a player does takes it back off:
CardArea:unhighlight_all skips it (cardarea.lua:201-208) and so does
remove_from_highlighted (cardarea.lua:188). A player who picks two more cards
for The Magician therefore has *three* highlighted, and the Magician's gate --
`mod_num >= #G.hand.highlighted` (card.lua:1566) -- greys the button out. Under
the Bell a targeting consumable is aimed at the forced card and the rest.

Seed 0RVVD29X (Ghost Deck, white stake), ante 8: the forced card was the AC at
hand position 1 on both sides. The simulator offered The Magician on positions
4 and 6, the policy took it, and the game held [1, 4, 6] and refused.

api.highlight, the training environment's path, had the other half:
add_to_highlighted (cardarea.lua:148-155) does not check whether a card is
already in the list, so naming the forced card -- which the simulator now
requires -- put it in G.hand.highlighted twice. The gate then counted three
cards for two, and clear_highlights could remove neither copy.
"""

import pytest

pytest.importorskip("lupa")

from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available  # noqa: E402
from jimbot_sim.headless.scenario import Scenario  # noqa: E402

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not engine_available(),
                       reason="no Balatro engine: need vendor/balatro_src"),
]

DEALT = "H_2 C_3 D_4 S_5 H_6 C_7 D_8 S_9"


def _under_the_bell():
    """A round against Cerulean Bell with The Magician held.

    Returns the game, the forced card's hand position and two others.
    """
    game = HeadlessBalatro().boot()
    Scenario(game).start().hand(DEALT).boss("bl_final_bell")
    game.execute(
        # set_blind does not deal, so nominate the way a draw does if the
        # swap did not already leave a card forced.
        'local any = false; for _, c in ipairs(G.hand.cards) do '
        'if c.ability.forced_selection then any = true end end; '
        'if not any then G.GAME.blind:drawn_to_hand() end; '
        'local c = create_card("Tarot", G.consumeables, nil, nil, nil, nil, '
        '"c_magician"); c:add_to_deck(); G.consumeables:emplace(c); '
        'api.pump(30)')
    held = int(game.eval("#G.hand.cards"))
    forced = [i for i in range(1, held + 1) if game.eval(
        "G.hand.cards[%d].ability.forced_selection and 1 or 0" % i) == 1]
    assert len(forced) == 1, "setup: exactly one card should be forced"
    others = [i for i in range(1, held + 1) if i != forced[0]]
    return game, forced[0], others


def _centres(game, positions):
    return [game.eval("G.hand.cards[%d].config.center.key" % i)
            for i in positions]


def test_the_game_keeps_the_forced_card_through_a_clear():
    """The rule itself: two picked cards are three selected."""
    from jimbot_sim.bridge import BridgeError
    from jimbot_sim.bridge.headless import HeadlessBridge

    game, forced, others = _under_the_bell()
    with pytest.raises(BridgeError, match="selected 3 cards, wanted 2"):
        HeadlessBridge(game).select(others[:2])
    assert game.eval("#G.hand.highlighted") == 3
    assert game.eval("G.consumeables.cards[1]:can_use_consumeable()") is False


def test_bridge_uses_the_magician_on_the_forced_card_and_one_more():
    """The smoke harness's path, aimed the way the simulator now aims it."""
    from jimbot_sim.bridge.headless import HeadlessBridge

    game, forced, others = _under_the_bell()
    HeadlessBridge(game).use_consumable(1, sorted([forced, others[0]]))
    assert game.eval("#G.consumeables.cards") == 0
    assert _centres(game, [forced, others[0], others[1]]) == [
        "m_lucky", "m_lucky", "c_base"]


def test_api_highlight_counts_the_forced_card_once():
    """The training environment's path: naming the forced card must not add it again."""
    game, forced, others = _under_the_bell()
    picked = game.eval("api.highlight({%d, %d})" % (forced, others[0]))
    assert picked == 2
    assert game.eval("#G.hand.highlighted") == 2
    assert game.eval("G.consumeables.cards[1]:can_use_consumeable()") is True

    game.execute("api.clear_highlights()")
    assert game.eval("#G.hand.highlighted") == 1, "the forced card alone stays"


def test_api_use_consumable_on_the_forced_card_and_one_more():
    game, forced, others = _under_the_bell()
    game.execute("api.use_consumable(1, {%d, %d})" % (forced, others[0]))
    assert game.eval("#G.consumeables.cards") == 0
    assert _centres(game, [forced, others[0], others[1]]) == [
        "m_lucky", "m_lucky", "c_base"]
    assert game.eval("#G.hand.highlighted") == 1
