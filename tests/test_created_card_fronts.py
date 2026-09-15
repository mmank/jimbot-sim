"""Cards the run makes get the rank and suit the game's own seed gives them.

Both of these showed up as a hand that differed the moment the made card was
first drawn, often many decisions after it was made -- which is why they
looked like the tarot divergence they were grouped with.

Grim. card.lua:1322-1324 makes its Aces with::

    _rank = 'A'
    _suit = pseudorandom_element({'S','H','D','C'}, pseudoseed('grim_create'))

one draw per card, from the suit. Familiar and Incantation draw the rank *and*
the suit from their pool (card.lua:1319-1327), and the simulator shared one
helper for all three, so it drew a rank for Grim too -- from a one-element
list, which still advances `grim_create`. Every Ace after the first came out
of the stream one step late: seeds 9WGGFWDN, YE4JXOD9, K3NVQZMD and QTC7RIRN.

Marble Joker. card.lua:2583 builds its Stone card on
``pseudorandom_element(G.P_CARDS, pseudoseed('marb_fr'))``. The simulator made
an Ace of Spades every time. A Stone card scores no rank or suit, but the
front is still what the hand shows and what orders two Stone cards
(card.lua:950-955), so seed TTL5O2HL showed a 7C where the shadow held an AS.
"""

import pytest

pytest.importorskip("lupa")

from jimbot_sim.cards import Enhancement, Rank  # noqa: E402
from jimbot_sim.consumables import REGISTRY  # noqa: E402
from jimbot_sim.game import GameState  # noqa: E402
from jimbot_sim.headless.runtime import HeadlessBalatro, engine_available  # noqa: E402
from jimbot_sim.headless.scenario import Scenario  # noqa: E402
from jimbot_sim.jokers import REGISTRY as JOKERS  # noqa: E402

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not engine_available(),
                       reason="no Balatro engine: need vendor/balatro_src"),
]

SEEDS = ["9WGGFWDN", "YE4JXOD9", "K3NVQZMD", "QTC7RIRN", "TTL5O2HL"]
VALUE = {"10": "T", "Jack": "J", "Queen": "Q", "King": "K", "Ace": "A"}

# Every card in the hand as "S_A|m_glass", read straight off the game's cards.
_ENGINE_CARDS = """(function(cards)
  local out = {}
  for _, c in ipairs(cards) do
    out[#out + 1] = c.base.suit:sub(1, 1) .. '_' ..
        ((%s)[c.base.value] or c.base.value) .. '|' .. c.config.center.key
  end
  return table.concat(out, ',')
end)(%s)"""


def _lua_value_table():
    return "{" + ", ".join('["%s"] = "%s"' % kv for kv in VALUE.items()) + "}"


def _engine(game, cards_expr):
    text = game.eval(_ENGINE_CARDS % (_lua_value_table(), cards_expr))
    return [entry.split("|") for entry in text.split(",") if entry]


def _code(card):
    """The simulator card as the game's P_CARDS key: "S_A"."""
    letter = {10: "T", 11: "J", 12: "Q", 13: "K", 14: "A"}
    return "%s_%s" % (card.suit.value,
                      letter.get(card.rank.value, str(card.rank.value)))


@pytest.mark.parametrize("seed", SEEDS)
def test_grim_makes_the_aces_the_game_makes(seed):
    game = HeadlessBalatro().boot()
    # No Aces in hand, so every Ace afterwards is one Grim made.
    Scenario(game).start(seed=seed).hand("H_2 C_3 D_4 S_5 H_6 C_7 D_8 S_9")
    game.execute(
        'local c = create_card("Spectral", G.consumeables, nil, nil, nil, '
        'nil, "c_grim"); c:add_to_deck(); G.consumeables:emplace(c); '
        'api.pump(30); api.use_consumable(1)')
    made = sorted((front, key) for front, key in _engine(game, "G.hand.cards")
                  if front.endswith("_A"))
    assert len(made) == 2

    sim = GameState(seed=seed, deck="Red Deck", stake=1)
    sim.hand = [c for c in sim.full_deck if c.rank is not Rank.ACE][:8]
    REGISTRY["Grim"].apply(sim, [])
    ours = sorted((_code(c), "m_" + c.enhancement.value)
                  for c in sim.hand if c.rank is Rank.ACE)
    assert ours == made


@pytest.mark.parametrize("seed", SEEDS)
def test_marble_joker_makes_the_stone_card_the_game_makes(seed):
    game = HeadlessBalatro().boot()
    game.execute(f'BOT.start_run({{"{seed}","Red_Deck",1}}); api.pump(300)')
    game.execute('add_joker("j_marble"); api.pump(60)')
    game.execute("api.select_blind(); api.pump(120)")
    stones = [front for front, key in _engine(game, "G.playing_cards")
              if key == "m_stone"]
    assert len(stones) == 1

    sim = GameState(seed=seed, deck="Red Deck", stake=1)
    before = list(sim.full_deck)
    JOKERS["Marble Joker"].on_blind_select(None, sim)
    made = [c for c in sim.full_deck if c not in before]
    assert len(made) == 1 and made[0].enhancement is Enhancement.STONE
    assert _code(made[0]) == stones[0]
