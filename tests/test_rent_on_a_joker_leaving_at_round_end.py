"""A rental that leaves at the end of a round still pays that round's rent.

end_round walks G.jokers.cards once (state_events.lua:99-110) and, for each
joker in turn, asks calculate_joker({end_of_round}) and then calls
calculate_rental and calculate_perishable on that same card
(state_events.lua:101, 108-109). A joker that destroys itself on that context
has not left the row by then. Gros Michel (card.lua:3019-3036), Popcorn
(card.lua:2945-2962) and Turtle Bean (card.lua:2903-2920) only queue an event
that calls G.jokers:remove_card, and Mr. Bones only queues start_dissolve
(card.lua:3047-3062). So calculate_rental (card.lua:2271-2276) still charges
ease_dollars(-G.GAME.rental_rate) on the card that is leaving, and the interest
row in evaluate_round (state_events.lua:1191-1202) reads a balance that $3
has already come out of.

The simulator ran every round_end hook first and charged rent afterwards,
over whatever jokers were left, so the joker that had just gone paid nothing.

Two stake-8 live runs against the headless engine stopped on this, both on a
rental Gros Michel going extinct as a boss fell:
  DS2IGPRB, Red Deck, decision 68 (The House): the game charged rent twice
  ($16 + $3 gold - $6 = $13, interest $2), the shadow once ($16, interest $3).
  Game $21, shadow $25.
  N97LC9AB, Abandoned Deck, decision 23 (The Pillar), balance already
  negative: rent three times against twice. Game -$8, shadow -$5.
"""

from jimbot_sim.blinds import BlindKind, make_blind
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance


def _round(name, money, counter=None):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    joker = JokerInstance(JOKERS[name])
    joker.rental = True
    if counter is not None:
        joker.counter = counter
    game.gain_joker(joker)
    game._start_round()
    game.money = money
    game.hands_left = game.discards_left = 0
    game.blind = make_blind(BlindKind.SMALL, game.ante)
    return game, joker


def test_a_rental_gros_michel_that_goes_extinct_pays_its_rent(monkeypatch):
    """card.lua:3020-3036 queues the removal; state_events.lua:108 charges the
    rent on the same card straight after. Interest reads what the rent left:
    $16 less $3 is two blocks of five, not three."""
    game, joker = _round("Gros Michel", 16)
    real = game.rng.chance
    monkeypatch.setattr(game.rng, "chance",
                        lambda key, *a: key == "gros_michel" or real(key, *a))
    game._beat_blind()
    assert joker not in game.jokers
    assert "gros_michel_extinct" in game.pool_flags
    assert game.money == 16 - 3
    assert game.pending_payout == 3 + 13 // 5


def test_a_rental_popcorn_eaten_at_round_end_pays_its_rent():
    """card.lua:2946-2962: Popcorn on its last four mult queues its removal
    and is still in the end_round loop when calculate_rental runs."""
    game, joker = _round("Popcorn", 10, counter=4.0)
    game._beat_blind()
    assert joker not in game.jokers
    assert game.money == 10 - 3


def test_a_rental_mr_bones_that_saves_the_run_pays_its_rent():
    """card.lua:3047-3062 returns `saved` and only queues start_dissolve;
    state_events.lua:103-108 then charges rent on it in the same pass."""
    game, joker = _round("Mr. Bones", 10)
    game.chips_scored = game.blind.target // 2
    game._lose_round()
    assert joker not in game.jokers
    assert game.money == 10 - 3
