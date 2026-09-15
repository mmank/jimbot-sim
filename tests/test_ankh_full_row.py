"""An Ankh with a full joker row is not a move: the game presses it and nothing happens.

Card:can_use_consumeable (card.lua:1536-1543) enables Ankh on "any joker and a
limit above one", so its button is live with the row full. G.FUNCS.use_card
(functions/button_callbacks.lua:2163-2169) then asks Card:check_use
(card.lua:1581-1588), which with `#G.jokers.cards >= card_limit` shows No Room
and returns true, and use_card puts the button back and returns -- before the
card leaves its area, before a pack choice is spent, before anything.

So from a pack or from a slot the card stays exactly where it was. The
simulator offered both, took them, and applied the Ankh; the engine did
nothing and the harness waited for a card that was never used. Seeds
3FBLDRVK/Abandoned/1 (6 of 6 jokers) and 9977JY5C/Anaglyph/3 (5 of 5), both
taking Ankh out of a Spectral pack.

Buy-and-use is the one path where the refusal *costs* something (the card is
paid for and lost), which is why can_use_consumable itself stays loose -- see
test_consumable_use.test_ankh_asks_only_for_a_joker_which_is_the_bug.
"""

from jimbot_sim.consumables import REGISTRY as CONSUMABLES
from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance


def _run(free_slots: int) -> GameState:
    game = GameState(seed="TESTSEED", deck="Red Deck")
    while len(game.jokers) < game.joker_slots - free_slots:
        game.gain_joker(JokerInstance(JOKERS["Joker"]))
    game._start_round()
    return game


def _in_spectral_pack(game: GameState) -> GameState:
    game.phase = Phase.PACK
    game.pack_options = [CONSUMABLES["Ankh"]]
    game.pack_picks_left = 1
    return game


def test_ankh_is_not_taken_from_a_pack_into_a_full_row():
    game = _in_spectral_pack(_run(free_slots=0))
    assert not any(a.type is ActionType.PICK_PACK
                   for a in game.legal_actions())
    assert not game.is_legal(Action(ActionType.PICK_PACK, index=0))


def test_ankh_is_taken_from_a_pack_with_a_slot_free():
    game = _in_spectral_pack(_run(free_slots=1))
    pick = Action(ActionType.PICK_PACK, index=0)
    assert pick in game.legal_actions()
    assert game.is_legal(pick)


def test_ankh_is_not_used_from_a_slot_with_a_full_row():
    game = _run(free_slots=0)
    game.consumables.append(game.hold_consumable(CONSUMABLES["Ankh"]))
    assert game.phase is Phase.PLAYING
    assert not any(a.type is ActionType.USE_CONSUMABLE
                   for a in game.legal_actions())
    assert not game.is_legal(Action(ActionType.USE_CONSUMABLE, index=0))


def test_ankh_is_used_from_a_slot_with_a_slot_free():
    game = _run(free_slots=1)
    game.consumables.append(game.hold_consumable(CONSUMABLES["Ankh"]))
    use = Action(ActionType.USE_CONSUMABLE, index=0)
    assert use in game.legal_actions()
    assert game.is_legal(use)
