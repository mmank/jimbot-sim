"""A Balatro run engine: deterministic, headless, and checked against the
game itself.

The engine is deterministic given a seed, has no I/O, and exposes the run as a
phase-based state machine:

    game = GameState(seed=0)
    while not game.is_over:
        game.step(random.choice(game.legal_actions()))
"""

from .blinds import Blind, BlindKind, BossEffect
from .cards import Card, Edition, Enhancement, Rank, Seal, Suit, standard_deck
from .consumables import ConsumableKind, ConsumableSpec
from .game import Action, ActionType, GameState, Phase, Tag
from .hands import HandLevels, HandType, evaluate
from .jokers import JokerInstance, JokerSpec, Rarity, make
from .scoring import score_hand

__all__ = [
    "Action", "ActionType", "Blind", "BlindKind", "BossEffect", "Card",
    "ConsumableKind", "ConsumableSpec", "Edition", "Enhancement", "GameState",
    "HandLevels", "HandType", "JokerInstance", "JokerSpec", "Phase", "Rank",
    "Rarity", "Seal", "Suit", "Tag", "evaluate", "make", "score_hand",
    "standard_deck",
]
