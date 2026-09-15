"""Death copies the card on the right of the *settled* hand, in the engine too.

card.lua:1111-1113 picks the card Death copies by screen position::

    local rightmost = G.hand.highlighted[1]
    for i=1, #G.hand.highlighted do
        if G.hand.highlighted[i].T.x > rightmost.T.x then ... end end

and `T.x` only follows the hand's list order once `CardArea:align_cards` has
run (cardarea.lua:496-507: x from the index, then the list re-sorted by x).
The real game runs it every frame -- `CardArea:move` ends in `align_cards`
(cardarea.lua:240) -- so a player clicking Use always sees, and uses, a hand
whose screen order is its list order. `CardArea:sort` (cardarea.lua:577-590),
which runs after every draw, reorders the list and leaves every `T.x` alone.

The headless engine never calls `Game:update` (headless_api.lua:59), so it
never calls `move` either: after a draw the list is sorted and the x
positions are still the order the cards were dealt in. Play and discard
already settle the hand first for exactly this reason (state_events.lua:463);
using a consumable did not, so Death copied whichever highlighted card had been
dealt last. Seed 86I68JIR (Yellow Deck, white stake) held 8H at hand position
2 and a lucky 7S at 4 on both sides; the engine turned the 7S into an 8H, the
simulator -- and the real game -- the 8H into a lucky 7S.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("lupa")

from jimbot_sim.cards import Card, Rank, Suit  # noqa: E402
from jimbot_sim.consumables import REGISTRY  # noqa: E402
from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available  # noqa: E402
from jimbot_sim.headless.scenario import Scenario  # noqa: E402

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not engine_available(),
                       reason="no Balatro engine: need vendor/balatro_src"),
]

RANKS = {1: "2", 2: "3", 3: "4", 4: "5", 5: "6", 6: "7", 7: "8", 8: "9",
         9: "T", 10: "J", 11: "Q", 12: "K", 13: "A"}
SUITS = {1: "S", 2: "H", 3: "C", 4: "D"}

# Dealt low to high, so after the game's own sort the list runs high to low
# while the x positions still run low to high: list position 1 is physically
# the rightmost card.
DEALT = "H_2 C_3 D_4 S_5 H_6 C_7 D_8 S_9"


def _hand(game):
    rows = game.eval("BOT_CMD.state()")["hand"]
    return ["%s%s" % (RANKS[int(rows[i]["rank"])], SUITS[int(rows[i]["suit"])])
            for i in range(1, len(rows) + 1)]


def _unsettled_hand_with_death():
    game = HeadlessBalatro().boot()
    Scenario(game).start().hand(DEALT)
    game.execute(
        'local c = create_card("Tarot", G.consumeables, nil, nil, nil, nil, '
        '"c_death"); c:add_to_deck(); G.consumeables:emplace(c); '
        # What a draw leaves behind: the list sorted, the x positions not.
        'G.hand:sort(); api.pump(30)')
    assert _hand(game) == ["9S", "8D", "7C", "6H", "5S", "4D", "3C", "2H"]
    xs = [float(game.eval("G.hand.cards[%d].T.x" % i)) for i in (1, 2)]
    assert xs[0] > xs[1], "setup: list position 1 should sit right of 2"
    return game


def test_simulator_copies_the_right_card_of_the_hand():
    """The simulator already resolves "right" against the hand's list order."""
    cards = [Card(Rank.NINE, Suit.SPADES), Card(Rank.EIGHT, Suit.DIAMONDS)]
    REGISTRY["Death"].apply(SimpleNamespace(hand=list(cards)), list(cards))
    assert [(c.rank, c.suit) for c in cards] == [
        (Rank.EIGHT, Suit.DIAMONDS), (Rank.EIGHT, Suit.DIAMONDS)]


def test_bridge_death_copies_the_right_card_of_the_settled_hand():
    """The smoke harness's path: the client selects, then BOT_CMD.use_consumable."""
    from jimbot_sim.bridge.headless import HeadlessBridge

    game = _unsettled_hand_with_death()
    HeadlessBridge(game).use_consumable(1, [1, 2])
    assert _hand(game)[:3] == ["8D", "8D", "7C"]


def test_api_death_copies_the_right_card_of_the_settled_hand():
    """The training environment's path: headless_api.use_consumable."""
    game = _unsettled_hand_with_death()
    game.execute("api.use_consumable(1, {1, 2})")
    assert _hand(game)[:3] == ["8D", "8D", "7C"]
