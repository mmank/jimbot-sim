"""The scoring pipeline.

Balatro resolves a played hand in a fixed order, and the order matters because
XMult is not commutative with +Mult:

    1. base chips/mult for the poker hand at its current level
    2. each scoring card, left to right: chips, enhancement, edition, seal,
       then every joker's "on scored card" hook
    3. each card held in hand: enhancement (Steel), then joker "held" hooks
    4. every joker's independent effect, left to right, plus its own edition
    5. score = floor(chips * mult)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .cards import Card, Edition, Enhancement, Seal
from .effects import ScoreContext
from .hands import HandResult
from .jokers import JokerInstance, JokerSpec

if TYPE_CHECKING:  # pragma: no cover
    from .game import GameState

GLASS_SHATTER_CHANCE = (1, 4)
LUCKY_MULT_CHANCE = (1, 5)
LUCKY_MONEY_CHANCE = (1, 15)


def effective_specs(jokers: list[JokerInstance]) -> list[JokerSpec]:
    """Resolve Blueprint/Brainstorm copies to the spec they actually run."""
    specs: list[JokerSpec] = []
    for i, joker in enumerate(jokers):
        spec = joker.spec
        seen = {i}
        idx = i
        while spec.copier is not None:
            target = idx + 1 if spec.copier == "right" else 0
            if target >= len(jokers) or target in seen:
                spec = joker.spec  # copies nothing; contributes no effect
                break
            seen.add(target)
            idx = target
            spec = jokers[target].spec
        specs.append(spec)
    return specs


def _apply_edition(edition: Edition, ctx: ScoreContext, source: str) -> None:
    if edition is Edition.FOIL:
        ctx.add_chips(50, f"{source} foil")
    elif edition is Edition.HOLOGRAPHIC:
        ctx.add_mult(10, f"{source} holo")
    elif edition is Edition.POLYCHROME:
        ctx.times_mult(1.5, f"{source} polychrome")


def _score_card_once(card: Card, ctx: ScoreContext) -> None:
    """One trigger of a single scoring card's own abilities."""
    game = ctx.game
    ctx.add_chips(card.base_chips, repr(card))

    if card.enhancement is Enhancement.BONUS:
        ctx.add_chips(30, "bonus card")
    elif card.enhancement is Enhancement.MULT:
        ctx.add_mult(4, "mult card")
    elif card.enhancement is Enhancement.GLASS:
        ctx.times_mult(2.0, "glass card")
    elif card.enhancement is Enhancement.LUCKY:
        if game.rng.chance("lucky", *LUCKY_MULT_CHANCE):
            ctx.add_mult(20, "lucky card")
        if game.rng.chance("lucky_money", *LUCKY_MONEY_CHANCE):
            ctx.money_gained += 20

    _apply_edition(card.edition, ctx, "card")

    if card.seal is Seal.GOLD:
        ctx.money_gained += 3


def _held_card_once(card: Card, ctx: ScoreContext) -> None:
    if card.enhancement is Enhancement.STEEL:
        ctx.times_mult(1.5, "steel card")


def score_hand(game: "GameState", result: HandResult, played: list[Card],
               held: list[Card]) -> ScoreContext:
    """Run the full pipeline and return the context (caller reads `.score`)."""
    ctx = ScoreContext(
        hand=result.hand,
        scoring=result.scoring,
        played=tuple(played),
        held=tuple(held),
        game=game,
    )
    specs = effective_specs(game.jokers)
    pairs = list(zip(game.jokers, specs))

    chips, mult = game.hand_levels.values(result.hand)
    boss = game.boss
    if boss is not None and boss.halve_base:
        chips, mult = chips / 2, mult / 2
    ctx.add_chips(chips, result.hand.label)
    ctx.add_mult(mult, result.hand.label)

    for joker, spec in pairs:
        if spec.update is not None:
            spec.update(joker, ctx)

    for card in result.scoring:
        if card.debuffed:
            continue
        triggers = 1 + (1 if card.seal is Seal.RED else 0)
        for joker, spec in pairs:
            if spec.retrigger_scored is not None:
                triggers += spec.retrigger_scored(joker, card, ctx)
        for _ in range(triggers):
            _score_card_once(card, ctx)
            for joker, spec in pairs:
                if spec.scored is not None:
                    spec.scored(joker, card, ctx)

    for card in held:
        if card.debuffed:
            continue
        triggers = 1 + (1 if card.seal is Seal.RED else 0)
        for joker, spec in pairs:
            if spec.retrigger_held is not None:
                triggers += spec.retrigger_held(joker, card, ctx)
        for _ in range(triggers):
            _held_card_once(card, ctx)
            for joker, spec in pairs:
                if spec.held is not None:
                    spec.held(joker, card, ctx)

    for joker, spec in pairs:
        if spec.independent is not None:
            spec.independent(joker, ctx)
        _apply_edition(joker.edition, ctx, joker.name)

    return ctx


def shattered_glass(game: "GameState", scoring: tuple[Card, ...]) -> list[Card]:
    """Glass cards that break after scoring, to be removed from the deck."""
    return [c for c in scoring
            if c.enhancement is Enhancement.GLASS
            and game.rng.chance("glass", *GLASS_SHATTER_CHANCE)]
