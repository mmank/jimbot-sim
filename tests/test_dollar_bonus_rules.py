"""The cash-out screen's joker rows: Satellite's count, and debuffed jokers.

Satellite
---------
card.lua:1667-1674 pays $1 for every entry of G.GAME.consumeable_usage whose
set is Planet -- one per *distinct* Planet used this run, however often each
was used. set_consumeable_usage (functions/misc_functions.lua:1184-1196)
writes that table from Card:use_consumeable (card.lua:1093), whatever the card
was used from. The simulator declared `unique_planets` and never wrote it, so
Satellite paid nothing for a whole run. Found by the handcrafted policy on the
headless engine: 6F5UPNKL and BUYS36P1 each cashed out a dollar short.

A debuffed joker pays no row
----------------------------
calculate_dollar_bonus starts `if self.debuff then return end` (card.lua:1656).
A perishable is debuffed by calculate_perishable in end_round
(state_events.lua:109, card.lua:2278-2284), before evaluate_round builds the
rows (state_events.lua:1176) -- so a Golden Joker in its last round pays
nothing on the cash-out that ends it. The simulator paid every joker's row
regardless. Found on TLCIZGT5: $4 over on the cash-out where Golden Joker
perished.

A perishable is off before the held cards pay
---------------------------------------------
end_round walks the row once -- each joker's end_of_round effect, its rent,
its perish tick (state_events.lua:99-110) -- and only then pays the cards held
(:170). So a Mime on its last round retriggers nothing. Found by the Rust
simulator's shadow on JOKER211 (Yellow Deck, stake -5): two Gold 6s held
beside it paid $6 in the game and $12 in the shadow.
"""

from jimbot_sim.blinds import BlindKind, make_blind
from jimbot_sim.cards import Card, Enhancement, Rank, Suit
from jimbot_sim.consumables import REGISTRY as CONSUMABLES
from jimbot_sim.game import GameState
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance


def _run(*names):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    return game


def _round_end(game, kind=BlindKind.SMALL):
    game.blind = make_blind(kind, game.ante)
    game._beat_blind()
    game._cash_out()


def _paid(game, name):
    return [int(line.split("+$")[1]) for line in game.logs
            if line.startswith(name + ": +$")]


def test_satellite_pays_nothing_before_a_planet():
    game = _run("Satellite")
    _round_end(game)
    assert _paid(game, "Satellite") == []


def test_satellite_pays_a_dollar_per_distinct_planet():
    game = _run("Satellite")
    for name in ("Mercury", "Mercury", "Venus"):
        game.use_consumable(CONSUMABLES[name], [])
    _round_end(game)
    assert _paid(game, "Satellite") == [2]


def test_satellite_does_not_count_tarots_or_spectrals():
    game = _run("Satellite")
    game.use_consumable(CONSUMABLES["Pluto"], [])
    game.use_consumable(CONSUMABLES["Black Hole"], [])
    _round_end(game)
    assert _paid(game, "Satellite") == [1]


def test_a_golden_joker_pays_four():
    game = _run("Golden Joker")
    _round_end(game)
    assert _paid(game, "Golden Joker") == [4]


def test_a_golden_joker_that_perishes_this_round_pays_nothing():
    game = _run("Golden Joker")
    joker = game.jokers[0]
    joker.perishable, joker.perish_tally = True, 1
    _round_end(game)
    assert joker.debuffed
    assert _paid(game, "Golden Joker") == []


def test_a_debuffed_rocket_pays_nothing():
    game = _run("Rocket")
    game.jokers[0].debuffed = True
    _round_end(game)
    assert _paid(game, "Rocket") == []


def _hold_a_gold_six(game):
    gold = Card(Rank.SIX, Suit.SPADES)
    gold.enhancement = Enhancement.GOLD
    game.hand = [gold]


def test_a_mime_that_perishes_this_round_does_not_retrigger_a_gold_card():
    game = _run("Mime")
    joker = game.jokers[0]
    joker.perishable, joker.perish_tally = True, 1
    _hold_a_gold_six(game)
    _round_end(game)
    assert joker.debuffed
    assert _paid(game, "gold cards") == [3]


def test_a_mime_with_rounds_left_retriggers_a_gold_card():
    game = _run("Mime")
    joker = game.jokers[0]
    joker.perishable, joker.perish_tally = True, 2
    _hold_a_gold_six(game)
    _round_end(game)
    assert not joker.debuffed
    assert _paid(game, "gold cards") == [6]
