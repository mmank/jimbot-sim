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

import math

from typing import TYPE_CHECKING

from .cards import Card, Edition, Enhancement, Seal
from .effects import ScoreContext
from .hands import PLANET_FOR_HAND, HandResult
from .jokers import JokerInstance, JokerSpec

if TYPE_CHECKING:  # pragma: no cover
    from .game import GameState

GLASS_SHATTER_CHANCE = (1, 4)


def _listed(game: "GameState", key: str, numerator: int,
            denominator: int) -> bool:
    """A listed probability, scaled by any Oops! All 6s in play.

    The game writes every one of these as
    `pseudorandom(key) < G.GAME.probabilities.normal / odds`, and Oops! All
    6s doubles that numerator, so a one in four becomes a one in two. The
    jokers went through a helper that knew this; the card effects here did
    not, so a glass card with an Oops! in the row shattered half as often as
    it should -- and a shattered card leaves the deck, which changes every
    hand dealt afterwards.
    """
    return game.rng.chance(key, numerator * game.probability_scale(),
                           denominator)
LUCKY_MULT_CHANCE = (1, 5)
LUCKY_MONEY_CHANCE = (1, 15)


def effective_specs(jokers: list[JokerInstance]
                    ) -> list[tuple[JokerSpec, JokerInstance]]:
    """What each joker in the row actually runs, and whose state it runs on.

    Both halves matter for a copier. The game does not lift an ability out of
    the copied joker and run it on Blueprint; it calls the copied joker:

        local eval = other_joker:calculate_joker(context)

    so a Blueprint on a Ceremonial Dagger adds the *dagger's* accumulated
    mult, and a Brainstorm on a Hiker reads the Hiker's counter. Returning the
    spec alone and then calling it with the copier's instance looked right and
    silently scored zero for every copied joker that keeps a counter -- which
    is most of the ones worth copying. It contributed nothing at all rather
    than contributing wrongly, so it left no trace in the log either.

    Recording 8 stopped on it: a Brainstorm copying a Ceremonial Dagger worth
    eighteen mult, adding none of it.

    The edition stays with the copier -- a polychrome Blueprint is polychrome
    whatever it copies -- so the caller keeps the row's own joker for that.
    """
    out: list[tuple[JokerSpec, JokerInstance]] = []
    for i, joker in enumerate(jokers):
        spec, source = joker.spec, joker
        seen = {i}
        idx = i
        while spec.copier is not None:
            target = idx + 1 if spec.copier == "right" else 0
            if (target >= len(jokers) or target in seen
                    or jokers[target].debuffed):
                # Copies nothing: its own spec has no effect hooks. A debuffed
                # joker is nothing to copy -- other_joker:calculate_joker is nil
                # for it (card.lua:2292) -- and the copier does not reach past
                # it, which is why this wants the whole row, debuffed included.
                spec, source = joker.spec, joker
                break
            seen.add(target)
            idx = target
            spec, source = jokers[target].spec, jokers[target]
        out.append((spec, source))
    return out


def calculating_specs(jokers: list[JokerInstance]
                      ) -> list[tuple[JokerInstance, JokerSpec, JokerInstance]]:
    """(owner, spec, source) for each joker in the row that answers at all.

    `jokers` is the whole row. A debuffed joker answers no calculate_joker
    context (card.lua:2291-2292) and is left out, and a copier beside one
    copies nothing (effective_specs). Resolving the copies over the working
    jokers alone moved a Blueprint on to the joker past a debuffed neighbour,
    and a Brainstorm on to the second joker when the first was debuffed.
    """
    return [(owner, spec, source)
            for owner, (spec, source) in zip(jokers, effective_specs(jokers))
            if not owner.debuffed]


def _edition_before(edition: Edition, ctx: ScoreContext, source: str) -> None:
    """A joker's foil and holo, which land before its own effect.

    The game evaluates a joker's edition twice and in two places. The chips
    and the mult -- foil and holo -- go in before the joker does anything,
    and the X-mult of a polychrome goes in after, once the joker-on-joker
    effects have run too. Applying the whole edition afterwards, which is
    what this did, gets polychrome right and holo wrong: a holographic
    Loyalty Card should give its ten mult and *then* be multiplied by four,
    not the other way round, which is a third of the hand.
    """
    if edition is Edition.FOIL:
        ctx.add_chips(50, f"{source} foil")
    elif edition is Edition.HOLOGRAPHIC:
        ctx.add_mult(10, f"{source} holo")


def _edition_after(edition: Edition, ctx: ScoreContext, source: str) -> None:
    """And the polychrome X-mult, which lands last."""
    if edition is Edition.POLYCHROME:
        ctx.times_mult(1.5, f"{source} polychrome")


def _apply_edition(edition: Edition, ctx: ScoreContext, source: str) -> None:
    """Both halves at once -- right for a playing card, which the game does
    evaluate in one pass, in eval_card rather than in the joker loop."""
    _edition_before(edition, ctx, source)
    _edition_after(edition, ctx, source)


def _score_card_once(card: Card, ctx: ScoreContext) -> bool:
    """One trigger of a single scoring card's own abilities.

    Returns the card's `lucky_trigger` for this trigger: whether either of a
    Lucky card's rolls hit (card.lua:988-989, 1076-1077). The jokers answer
    it next, and the game clears it once they have (state_events.lua:700).
    """
    game = ctx.game
    lucky_trigger = False
    ctx.add_chips(card.base_chips, repr(card))

    if card.enhancement is Enhancement.BONUS:
        ctx.add_chips(30, "bonus card")
    elif card.enhancement is Enhancement.MULT:
        ctx.add_mult(4, "mult card")
    elif card.enhancement is Enhancement.GLASS:
        ctx.times_mult(2.0, "glass card")
    elif card.enhancement is Enhancement.LUCKY:
        # Every trigger rolls, hit or miss, so this count is exact where the
        # rolls themselves are not: how many chances the play gives a Lucky
        # Cat. See GameState.preview_outcome.
        ctx.lucky_rolls += 1
        # The game's pool is called lucky_mult, and a pool is identified by
        # its name -- a different name is a different stream of numbers.
        if _listed(game, "lucky_mult", *LUCKY_MULT_CHANCE):
            ctx.add_mult(20, "lucky card")
            lucky_trigger = True
        if _listed(game, "lucky_money", *LUCKY_MONEY_CHANCE):
            ctx.money_gained += 20
            lucky_trigger = True

    _apply_edition(card.edition, ctx, "card")

    if card.seal is Seal.GOLD:
        ctx.money_gained += 3
    return lucky_trigger


def _held_card_once(card: Card, ctx: ScoreContext,
                    pairs: list[tuple[JokerInstance, JokerSpec, JokerInstance]]
                    ) -> bool:
    """One pass over a card held in hand; whether anything answered for it.

    The card's own effect (a Steel card's x_mult, common_events.lua:630-633)
    and then each joker's, in row order. Every one of them moves the score or
    the money -- Steel, Baron, Shoot the Moon, Raised Fist, a Reserved
    Parking roll that pays -- so a pass that moved neither had no effect,
    which is what the game asks before it repeats a held card. A Parking roll
    that misses has still drawn from the stream, and still counts as nothing.
    """
    before = (len(ctx.log), ctx.money_gained)
    if card.enhancement is Enhancement.STEEL:
        ctx.times_mult(1.5, "steel card")
    for _owner, spec, source in pairs:
        if spec.held is not None:
            spec.held(source, card, ctx)
    return (len(ctx.log), ctx.money_gained) != before


def after_hand_pass(pairs: list[tuple[JokerInstance, JokerSpec, JokerInstance]],
                    ctx: ScoreContext) -> None:
    """context.after: the joker pass every played hand gets, scored or not.

    Scaling jokers grow *after* the hand they are part of, which the game
    does under context.after. Running it first cost Ice Cream five chips on
    every hand including its first, and would have done the same to Square
    Joker and Runner the moment a hand met their condition.

    evaluate_play asks it outside `if not G.GAME.blind:debuff_hand(...)`
    (state_events.lua:614, 1068-1075), so a hand the boss refuses is asked
    too -- GameState._play calls this for one, since score_hand is never
    reached. Ice Cream (card.lua:3571) and Seltzer (card.lua:3601) are the
    two jokers that answer; every other growth on a played hand is
    `context.before` (card.lua:3411-3569) and is skipped with the block.

    Only on its own account, for the reason given in score_hand: both
    branches are `not context.blueprint`.
    """
    for owner, spec, source in pairs:
        if owner is not source:
            continue
        if spec.update is not None and not spec.update_before_scoring:
            spec.update(source, ctx)


def score_hand(game: "GameState", result: HandResult, played: list[Card],
               held: list[Card]) -> ScoreContext:
    """Run the full pipeline and return the context (caller reads `.score`)."""
    ctx = ScoreContext(
        hand=result.hand,
        scoring=result.scoring,
        played=tuple(played),
        held=tuple(held),
        game=game,
        contains=result.contains,
    )
    # A debuffed joker scores nothing at all -- a perishable that has run out
    # its rounds sits in the row contributing neither chips nor mult.
    # (owner, spec, source): the joker in the row, the ability it runs, and
    # the joker whose state that ability reads. They differ only for a
    # Blueprint or a Brainstorm, and only the owner's edition applies.
    pairs = calculating_specs(game.jokers)

    for owner, spec, source in pairs:
        # context.before (state_events.lua:628-638), copies included. To Do
        # List pays here, not with the jokers' main effects: card.lua:3491-3499
        # is `ease_dollars` plus `G.GAME.dollar_buffer`, and Bootstraps
        # (card.lua:4046) and Bull (3936) read `dollars + dollar_buffer` in
        # joker_main -- so they count the $4 wherever the list sits in the row.
        # Paying it from the main pass, in row order, left a Bootstraps to its
        # left reading the money from before the hand. 2MIUP34I, Zodiac Deck,
        # stake 8: $4 held, a High Card the list named, the game 46 x 31 =
        # 1426 and this 46 x 29 = 1334. It came and went between runs of the
        # same seed because the hand the list names does (see hands.py).
        if spec.before is not None:
            spec.before(source, ctx)
        # Only on its own account. A copier runs the copied joker's scoring
        # hooks, and the game guards the *scaling* branches against that
        # with `not context.blueprint` -- so a Blueprint standing left of an
        # Ice Cream adds its chips and does not make it melt twice as fast.
        #
        # Marcin stopped a live run on it: seed 12346, decision 114, row
        # Ride the Bus / Photograph / Blueprint / Ice Cream / Jolly. One
        # hand took the game's Ice Cream from 100 to 95 and this from 100 to
        # 90, and the two decks of chips drifted apart from there.
        if owner is not source:
            continue
        if spec.update is not None and spec.update_before_scoring:
            spec.update(source, ctx)

    # The base is read after the before pass, and it is this reading The
    # Flint halves: evaluate_play reads G.GAME.hands[text] again at
    # state_events.lua:640-641, straight after the jokers' `before` pass, and
    # passes that to Blind:modify_hand (645-646). Space Joker levels the hand
    # in that pass (card.lua:3420-3426, level_up_hand at 634-635), so the new
    # level is what scores and what gets halved. Reading and halving the base
    # first, with Space Joker adding the level's gain on top, let the gain
    # through whole into The Flint: OCMTUFBK, Blue Deck, stake 3, a Two Pair
    # levelled from 9 to 10 scored 214 x 74 in the game and 224 x 74 here.
    chips, mult = game.hand_levels.values(result.hand)
    boss = game.boss
    if boss is not None and boss.halve_base:
        # The Flint rounds rather than halving. Blind:modify_hand is
        #
        #     max(floor(mult*0.5 + 0.5), 1), max(floor(chips*0.5 + 0.5), 0)
        #
        # so a Three of a Kind's three mult becomes two, not one and a half.
        # Dividing by two loses a whole point of mult on every odd number,
        # and it is the base mult, so everything the hand multiplies by
        # magnifies it.
        chips = max(int(chips * 0.5 + 0.5), 0)
        mult = max(int(mult * 0.5 + 0.5), 1)
    # G.GAME.blind.triggered, the half of it set while the hand scores:
    # modify_hand for The Flint (blind.lua:512), and any debuffed card in the
    # scoring hand, whatever the boss (state_events.lua:655-656) -- which is
    # how a Flush of Clubs into The Club triggers it. GameState._play has
    # cleared it and set the rest; Matador reads it with the jokers below.
    if game.blind is not None and ((boss is not None and boss.halve_base)
                                   or any(c.debuffed for c in result.scoring)):
        game.blind.triggered = True
    ctx.add_chips(chips, result.hand.label)
    ctx.add_mult(mult, result.hand.label)

    for card in result.scoring:
        if card.debuffed:
            continue
        triggers = 1 + (1 if card.seal is Seal.RED else 0)
        for _owner, spec, source in pairs:
            if spec.retrigger_scored is not None:
                triggers += spec.retrigger_scored(source, card, ctx)
        for _ in range(triggers):
            lucky_trigger = _score_card_once(card, ctx)
            for owner, spec, source in pairs:
                if spec.scored is not None:
                    spec.scored(source, card, ctx)
                # The growth the same branch guards with `not
                # context.blueprint`: Wee Joker's +8 per scoring 2
                # (card.lua:3083-3085). A copy adds the chips in joker_main
                # and does not grow them; asking `scored` for copies grew a
                # Wee Joker beside a Blueprint to 48 on three 2s where the
                # engine's reached 24.
                if spec.scored_growth is not None and owner is source:
                    spec.scored_growth(source, card, ctx)
                # Lucky Cat, in the same pass and on its own account only:
                # `not context.blueprint` (card.lua:3076). Once per trigger
                # however many of the card's rolls hit, since the flag is
                # cleared after this pass (state_events.lua:700), not after
                # each roll. AWEFRTUZ, Blue Deck, stake 5, decision 70: a
                # Lucky Queen under Hanging Chad hit both rolls on its third
                # trigger, the game's Lucky Cat went to X1.25 and the
                # simulator's, which had nothing that grew it, stayed at X1.
                if (lucky_trigger and spec.lucky_trigger is not None
                        and owner is source):
                    spec.lucky_trigger(source, card, ctx)

    # A held card is repeated only when its first pass did something. The
    # game asks for repetitions once, after that pass, and gates the red seal
    # on `next(effects[1]) or #effects > 1` (state_events.lua:812-817) while
    # Mime asks the same of context.card_effects (card.lua:2879-2880); played
    # cards have no such gate (669-683). So a face card whose Reserved Parking
    # roll misses is not rolled again under Mime, and the draw stays on the
    # stream for the next one. Retriggering every held card rolled it twice:
    # OH4OWIIZ, Ghost Deck, stake 8, Mime and Reserved Parking, took one draw
    # too many at decision 12 and paid $2 at decision 21 where the game's
    # Jack drew the leftover and missed -- game $1, simulator $3.
    for card in held:
        if card.debuffed:
            continue
        if not _held_card_once(card, ctx, pairs):
            continue
        repeats = 1 if card.seal is Seal.RED else 0
        for _owner, spec, source in pairs:
            if spec.retrigger_held is not None:
                repeats += spec.retrigger_held(source, card, ctx)
        for _ in range(repeats):
            _held_card_once(card, ctx, pairs)

    # One joker at a time, and the whole row answers about each one before the
    # next (state_events.lua:877-944): its foil and holo, its own joker_main,
    # then every joker asked under context.other_joker, then its polychrome.
    # Baseball Card lives in that third step (card.lua:3396-3408), so its X1.5
    # lands straight after each Uncommon joker and not at its own position.
    # The game walks every joker in the row there, debuffed or not; a debuffed
    # one has no edition and no effect of its own (card.lua:1016-1017,
    # 2291-2292) but is still a joker for the others to answer about.
    # 8KUQ2KZU stopped on it at decision 19: Mime, Popcorn, Baseball Card,
    # Troubadour scored 77 x 54 here against the game's 77 x 39.
    answering = {id(owner): (spec, source) for owner, spec, source in pairs}
    about_others = [(spec, source) for _owner, spec, source in pairs
                    if spec.other_joker is not None]
    for joker in game.jokers:
        own = answering.get(id(joker))
        if own is not None:
            _edition_before(joker.edition, ctx, joker.name)
            if own[0].independent is not None:
                own[0].independent(own[1], ctx)
        for spec, source in about_others:
            spec.other_joker(source, joker, ctx)
        if own is not None:
            _edition_after(joker.edition, ctx, joker.name)

    # Observatory: a Planet card sitting in a consumable slot gives X1.5 Mult
    # for its own hand type. It is the one voucher whose effect is a scoring
    # one, which is why it had no field on Voucher to hold it and was doing
    # nothing at all.
    if any(v.key == "v_observatory" for v in game.vouchers):
        for spec in game.consumables:
            if PLANET_FOR_HAND.get(result.hand) == spec.name:
                ctx.times_mult(1.5, "Observatory")

    after_hand_pass(pairs, ctx)

    # The Plasma Deck's final scoring step: chips and mult are averaged, both
    # floored, so a hand scores the square of half their sum. It happens after
    # everything else has had its say, which is why it lives at the very end
    # rather than anywhere a joker could reach.
    if game.deck_config.get("balance_chips_mult") or game.deck == "Plasma Deck":
        total = ctx.chips + ctx.mult
        ctx.chips = math.floor(total / 2)
        ctx.mult = math.floor(total / 2)

    return ctx


def shattered_glass(game: "GameState", scoring: tuple[Card, ...]) -> list[Card]:
    """Glass cards that break after scoring, to be removed from the deck.

    A debuffed Glass card never breaks, and never rolls to (state_events.lua:
    961):

        if scoring_hand[i].ability.name == 'Glass Card'
            and not scoring_hand[i].debuff
            and pseudorandom('glass') < G.GAME.probabilities.normal/... then

    The `and` stops before the draw, so the 'glass' stream does not move
    either. Unreachable until a debuffed card could score at all -- it can
    in a flush, since a flush reads a debuffed card's printed suit (see
    `hands.flush_suit`). Seed QWERTYUI on the headless engine, the decision
    after that fix: The Club and Smeared Joker debuff a Glass Ace of Spades
    in a scoring flush, and this broke it -- the deck went to 51 here and
    stayed at 52 in the game.
    """
    return [c for c in scoring
            if c.enhancement is Enhancement.GLASS
            and not c.debuffed
            and _listed(game, "glass", *GLASS_SHATTER_CHANCE)]


def held_triggers(game: "GameState", card: Card) -> int:
    """How many times a card held in hand fires its abilities.

    A red seal retriggers it once and Mime retriggers every held ability, and
    both apply at the end of the round as well as during scoring -- the game
    runs the same repetition loop over G.hand in its end-of-round pass. So a
    blue seal under a Mime makes two Planet cards, and a gold card pays six
    dollars rather than three.

    Through effective_specs, so a Blueprint or a Brainstorm copying a Mime
    retriggers too. Reading each joker's own spec missed that, and missed it
    quietly: the copier simply had no retrigger to offer, so the count came
    out one short and everything downstream was merely smaller. Recording 8
    stopped on it at step 314 -- two gold Kings with red seals, held under a
    Mime with a Brainstorm copying it, paid $18 here against the game's $24.
    """
    if card.debuffed:
        return 0
    triggers = 1 + (1 if card.seal is Seal.RED else 0)
    for _owner, spec, source in calculating_specs(game.jokers):
        if spec.retrigger_held is not None:
            triggers += spec.retrigger_held(source, card, None)
    return triggers
