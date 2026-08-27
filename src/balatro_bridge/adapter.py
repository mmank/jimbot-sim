"""Translate the mod's JSON state into engine objects.

Anything the mod reports that this engine cannot represent -- an unimplemented
joker, an unknown enhancement -- raises `UnsupportedContent` rather than being
dropped. Silently ignoring a joker would make the engine and the real game
disagree about the score, which is exactly what the bridge exists to detect.
"""

from __future__ import annotations

from typing import Any

from balatro.cards import Card, Edition, Enhancement, Rank, Seal, Suit
from balatro.consumables import REGISTRY as CONSUMABLE_REGISTRY
from balatro.game import GameState, Phase
from balatro.hands import HandType
from balatro.jokers import REGISTRY as JOKER_REGISTRY, JokerInstance

RANK_BY_NAME = {r.short: r for r in Rank} | {str(r.value): r for r in Rank}
SUIT_BY_NAME = {s.value: s for s in Suit} | {s.name.title(): s for s in Suit}
PHASE_BY_NAME = {p.value: p for p in Phase}
HAND_BY_NAME = {h.label: h for h in HandType}


class UnsupportedContent(RuntimeError):
    """The real game contains something the engine does not model."""


def card_from_json(data: dict[str, Any]) -> Card:
    rank = RANK_BY_NAME.get(str(data["rank"]))
    suit = SUIT_BY_NAME.get(str(data["suit"]))
    if rank is None or suit is None:
        raise UnsupportedContent(f"unknown card {data!r}")
    try:
        return Card(
            rank=rank,
            suit=suit,
            enhancement=Enhancement(data.get("enhancement", "none")),
            edition=Edition(data.get("edition", "none")),
            seal=Seal(data.get("seal", "none")),
            debuffed=bool(data.get("debuffed", False)),
        )
    except ValueError as exc:
        raise UnsupportedContent(f"unknown card modifier in {data!r}") from exc


def joker_from_json(data: dict[str, Any]) -> JokerInstance:
    name = data["name"]
    spec = JOKER_REGISTRY.get(name)
    if spec is None:
        raise UnsupportedContent(f"joker {name!r} is not implemented in the engine")
    try:
        edition = Edition(data.get("edition", "none"))
    except ValueError as exc:
        raise UnsupportedContent(f"unknown edition on {name!r}") from exc
    return JokerInstance(spec, edition=edition,
                         counter=float(data.get("counter", spec.init_counter)),
                         eternal=bool(data.get("eternal", False)))


def state_from_json(data: dict[str, Any]) -> GameState:
    """Rebuild a `GameState` from a mod snapshot.

    Untested against a real mod -- the Lua side does not exist yet. It is
    written against the schema in `protocol.py` and covered by tests that feed
    it synthetic snapshots.
    """
    game = GameState(seed=int(data.get("seed", 0)))
    game.ante = int(data.get("ante", 1))
    game.blind_index = int(data.get("blind_index", 0))
    game.round_number = int(data.get("round", 0))
    game.money = int(data.get("money", 0))
    game.hands_left = int(data.get("hands_left", 0))
    game.discards_left = int(data.get("discards_left", 0))
    game.chips_scored = int(data.get("chips_scored", 0))

    phase = PHASE_BY_NAME.get(str(data.get("phase", "")))
    if phase is None:
        raise UnsupportedContent(f"unknown phase {data.get('phase')!r}")
    game.phase = phase

    game.hand = [card_from_json(c) for c in data.get("hand", [])]
    game.full_deck = [card_from_json(c) for c in data.get("deck", [])] or game.full_deck
    game.jokers = [joker_from_json(j) for j in data.get("jokers", [])]

    consumables = []
    for name in data.get("consumables", []):
        spec = CONSUMABLE_REGISTRY.get(name)
        if spec is None:
            raise UnsupportedContent(f"consumable {name!r} is not implemented")
        consumables.append(spec)
    game.consumables = consumables

    for label, level in (data.get("hand_levels") or {}).items():
        hand = HAND_BY_NAME.get(label)
        if hand is None:
            raise UnsupportedContent(f"unknown poker hand {label!r}")
        game.hand_levels.levels[hand] = int(level)

    if game.blind is not None and "blind_target" in data:
        game.blind.target = int(data["blind_target"])
    return game
