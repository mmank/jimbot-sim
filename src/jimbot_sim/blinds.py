"""Blind schedule, ante scaling and boss blind effects."""

from __future__ import annotations

import math

from dataclasses import dataclass
from enum import Enum

from .cards import Suit

# The chip requirement per ante, one row per scaling. get_blind_amount keeps
# three tables and picks by G.GAME.modifiers.scaling, which the stake sets:
# 1 up to Green, 2 from Green, 3 from Purple. The higher rows are steeper
# everywhere, not just at the top -- ante 3 is 2000, 2600 or 3200.
ANTE_BASE: dict[int, list[int]] = {
    1: [300, 800, 2000, 5000, 11000, 20000, 35000, 50000],
    2: [300, 900, 2600, 8000, 20000, 36000, 60000, 100000],
    3: [300, 1000, 3200, 9000, 25000, 60000, 110000, 200000],
}


def ante_base_chips(ante: int, scaling: int = 1) -> int:
    """The chips an ante asks for, exactly as get_blind_amount computes them.

    Past ante eight the game leaves the table behind for a formula, and then
    rounds the result down to two significant figures -- `amount - amount %
    10^floor(log10(amount)-1)`. Multiplying by 1.6 in a loop, which is what
    this did, drifts from it immediately.
    """
    amounts = ANTE_BASE.get(scaling, ANTE_BASE[1])
    if ante < 1:
        return 100
    if ante <= 8:
        return amounts[ante - 1]
    a, b, c, k = amounts[7], 1.6, ante - 8, 0.75
    d = 1 + 0.2 * (ante - 8)
    amount = math.floor(a * (b + (k * c) ** d) ** c)
    return amount - amount % (10 ** math.floor(math.log10(amount) - 1))


class BlindKind(Enum):
    SMALL = "small"
    BIG = "big"
    BOSS = "boss"


BLIND_MULT = {BlindKind.SMALL: 1.0, BlindKind.BIG: 1.5, BlindKind.BOSS: 2.0}
BLIND_REWARD = {BlindKind.SMALL: 3, BlindKind.BIG: 4, BlindKind.BOSS: 5}
# Except the five finishers, which pay eight. Straight off P_BLINDS in
# game.lua, where every one of the twenty-three ordinary bosses carries
# `dollars = 5` and each of
#
#     bl_final_acorn  bl_final_bell  bl_final_heart
#     bl_final_leaf   bl_final_vessel
#
# carries `dollars = 8`. Paying every boss five made an ante-8 win three
# dollars short every time, and recording 12 is where that showed: the
# Crimson Heart cash-out paid $15 there and $12 here. `showdown` in
# BOSS_DATA is the same flag the game reads, so the two cannot drift apart
# without the generator noticing.
FINISHER_REWARD = 8


@dataclass(frozen=True)
class BossEffect:
    """Declarative boss modifiers; the engine reads these fields directly.

    Bosses whose effect is purely about face-down cards are represented with no
    mechanical modifier, since this engine has full information anyway.
    """

    name: str
    text: str
    chip_mult: float = 2.0
    debuff_suit: Suit | None = None
    debuff_face: bool = False
    hand_size_delta: int = 0
    hands_delta: int = 0
    discards_delta: int = 0
    min_cards_played: int = 0
    money_per_card_played: int = 0
    zero_money_on_most_played: bool = False
    discard_random_on_play: int = 0
    level_down_played_hand: bool = False
    no_repeat_hand: bool = False
    lock_first_hand_type: bool = False
    debuff_previously_played: bool = False
    halve_base: bool = False
    # The finishers, and one ordinary boss, that do something to the run
    # rather than to a card. These were all left blank on the grounds that
    # face-down cards mean nothing to an engine with full information, which
    # is true of four of them and not of these five.
    always_draw_three: bool = False    # The Serpent
    shuffles_jokers: bool = False      # Amber Acorn
    debuff_until_sale: bool = False    # Verdant Leaf
    debuff_a_joker: bool = False       # Crimson Heart
    forces_a_card: bool = False        # Cerulean Bell
    is_finisher: bool = False


BOSSES: list[BossEffect] = [
    BossEffect("The Hook", "Discards 2 random cards per hand played",
               discard_random_on_play=2),
    BossEffect("The Ox", "Playing your most played hand sets money to $0",
               zero_money_on_most_played=True),
    BossEffect("The House", "First hand is drawn face down"),
    BossEffect("The Wall", "Extra large blind", chip_mult=4.0),
    BossEffect("The Wheel", "1 in 7 cards get drawn face down"),
    BossEffect("The Arm", "Decrease level of played poker hand",
               level_down_played_hand=True),
    BossEffect("The Club", "All Club cards are debuffed", debuff_suit=Suit.CLUBS),
    BossEffect("The Fish", "Cards drawn face down after each hand played"),
    BossEffect("The Psychic", "Must play 5 cards", min_cards_played=5),
    BossEffect("The Goad", "All Spade cards are debuffed", debuff_suit=Suit.SPADES),
    BossEffect("The Water", "Start with 0 discards", discards_delta=-99),
    BossEffect("The Window", "All Diamond cards are debuffed", debuff_suit=Suit.DIAMONDS),
    BossEffect("The Manacle", "-1 hand size", hand_size_delta=-1),
    BossEffect("The Eye", "No repeat hand types this round", no_repeat_hand=True),
    BossEffect("The Mouth", "Play only one hand type this round",
               lock_first_hand_type=True),
    BossEffect("The Plant", "All face cards are debuffed", debuff_face=True),
    BossEffect("The Serpent", "After play or discard, always draw 3 cards", always_draw_three=True),
    BossEffect("The Pillar", "Cards played earlier this ante are debuffed",
               debuff_previously_played=True),
    # One hand, and the *small* blind's requirement for it: bl_needle is
    # `mult = 1` in game.lua:285, alone among the ordinary bosses. The
    # default here said two, so the simulator asked for twice what the
    # game asks -- see the check below, which is why it cannot happen
    # again.
    BossEffect("The Needle", "Play only 1 hand", hands_delta=-99,
               chip_mult=1.0),
    BossEffect("The Head", "All Heart cards are debuffed", debuff_suit=Suit.HEARTS),
    BossEffect("The Tooth", "Lose $1 per card played", money_per_card_played=-1),
    BossEffect("The Flint", "Base Chips and Mult are halved", halve_base=True),
    BossEffect("The Mark", "All face cards are drawn face down"),
]

FINISHER_BOSSES: list[BossEffect] = [
    BossEffect("Amber Acorn", "Flips and shuffles all Jokers", is_finisher=True, shuffles_jokers=True),
    BossEffect("Verdant Leaf", "All cards debuffed until a Joker is sold",
               is_finisher=True, debuff_until_sale=True),
    BossEffect("Violet Vessel", "Very large blind", chip_mult=6.0, is_finisher=True),
    BossEffect("Crimson Heart", "One random Joker disabled each hand", is_finisher=True, debuff_a_joker=True),
    BossEffect("Cerulean Bell", "Forces one card to always be selected",
               is_finisher=True, forces_a_card=True),
]


# The multiplier is written twice -- here, and in the table generated from the
# game's own P_BLINDS -- so it is checked here rather than trusted. The Needle
# is what this is for: `mult = 1` in the game and two by default here, so the
# simulator asked four thousand chips for a blind the game prices at two, and
# nothing said so until the policy played the engine with a shadow beside it.
def _check_multipliers() -> None:
    from .boss_data import BOSS_DATA

    mult_by_name = {name: mult for name, _min, _show, mult in BOSS_DATA.values()}
    for boss in BOSSES + FINISHER_BOSSES:
        if boss.name not in mult_by_name:
            raise AssertionError("%s is not in BOSS_DATA" % boss.name)
        if boss.chip_mult != mult_by_name[boss.name]:
            raise AssertionError(
                "%s asks x%g here and x%g in the game's own table"
                % (boss.name, boss.chip_mult, mult_by_name[boss.name]))


_check_multipliers()

@dataclass
class Blind:
    kind: BlindKind
    ante: int
    target: int
    reward: int
    boss: BossEffect | None = None
    # Set by Luchador, Chicot and The Fool's Gold. The game keeps this on the
    # blind rather than on the joker, which matters: the blind stays disabled
    # for the rest of the round even after the joker that did it is gone.
    disabled: bool = False
    # G.GAME.blind.triggered: whether the boss's ability went off on the hand
    # being played, which is the only thing Matador reads. Starts at nil in
    # set_blind (blind.lua:93); every play clears it, and GameState._play and
    # score_hand set it again the way the game does.
    triggered: bool = False
    # Set by set_blind (blind.lua:94) -- which here is _start_round, not the
    # moment a blind is put on offer -- and by press_play when Crimson Heart
    # has a joker to take (blind.lua:488-493); cleared by drawn_to_hand
    # (blind.lua:602). Only Crimson Heart reads it: GameState._drawn_to_hand.
    prepped: bool = False
    # On offer on the blind select screen and not yet set (set_blind is
    # _start_round). The game's G.GAME.blind is then the empty one the last
    # round left (blind.lua:336), so a boss on deck does nothing: see
    # GameState.boss. A blind built any other way -- a scenario, a policy's
    # fork pricing against the boss to come -- is in force.
    on_deck: bool = False

    @property
    def name(self) -> str:
        if self.boss is not None:
            return self.boss.name
        return f"{self.kind.value.title()} Blind"


def _reward(kind: BlindKind, boss: BossEffect | None) -> int:
    """What beating this blind pays, which is not one number per kind."""
    if kind is BlindKind.BOSS and boss is not None and boss.is_finisher:
        return FINISHER_REWARD
    return BLIND_REWARD[kind]


def make_blind(kind: BlindKind, ante: int, boss: BossEffect | None = None,
               ante_scaling: float = 1.0, scaling: int = 1,
               no_reward: bool = False) -> Blind:
    """The game: get_blind_amount(ante) * mult * ante_scaling.

    ante_scaling comes from the deck -- the Plasma Deck doubles every target
    in the run, which is the price it pays for balancing chips and mult.
    """
    mult = boss.chip_mult if (kind is BlindKind.BOSS and boss) else BLIND_MULT[kind]
    return Blind(
        kind=kind,
        ante=ante,
        target=int(ante_base_chips(ante, scaling) * mult * ante_scaling),
        reward=0 if no_reward else _reward(kind, boss),
        boss=boss,
    )
