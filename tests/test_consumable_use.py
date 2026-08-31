"""When the game lets a consumable be used, and what it costs when it is.

The same audit as test_declared_flags, turned on the consumables. All 52 are
registered with the right kind and the right selection limits, and every one
has an effect -- but only the *count* of selected cards was being checked
before a use was allowed, and Card:can_use_consumeable checks a good deal
more.

That is not a harmless extra option for a policy. A move that looks legal,
gets chosen, and does nothing is worse than no move: it is a hole the agent
can fall into repeatedly with no signal.

The table below was measured on a live engine, one call to
can_use_consumeable per cell. Two cautions learned the hard way while taking
it: create_card must be given the set that matches the key, and mod_num and
the eligible-joker lists are filled by Card:update rather than by the
constructor, so a freshly conjured card compares a number with nil.
"""

import pytest

from balatro.cards import Card, Edition, Rank, Suit
from balatro.consumables import REGISTRY as CONSUMABLES
from balatro.game import Action, ActionType, GameState, Phase
from balatro.jokers import REGISTRY as JOKERS, JokerInstance


def _run(*joker_names, phase=Phase.PLAYING):
    game = GameState(seed="TESTSEED", deck="Red Deck")
    for name in joker_names:
        game.gain_joker(JokerInstance(JOKERS[name]))
    game._start_round()
    game.phase = phase
    return game


def _can(game, name, targets=()):
    return game.can_use_consumable(CONSUMABLES[name], tuple(targets))


# ------------------------------------------------------------------
# the state gate: anything that selects cards needs a hand
# ------------------------------------------------------------------

# Measured "shop" column. Everything else in the roster came back no.
USABLE_IN_A_SHOP = [
    "The Fool", "The High Priestess", "The Emperor", "The Hermit",
    "The Wheel of Fortune", "Temperance", "Judgement", "Wraith", "Ectoplasm",
    "Ankh", "Hex", "The Soul", "Black Hole", "Pluto",
]


@pytest.mark.parametrize("name", USABLE_IN_A_SHOP)
def test_the_consumables_a_shop_allows(name):
    game = _run("Joker", phase=Phase.SHOP)
    game.last_tarot_planet = "c_death"
    assert _can(game, name)


@pytest.mark.parametrize("name", [
    "The Magician", "The Empress", "The Hierophant", "Strength",
    "The Hanged Man", "Death", "The Star", "The Moon", "The Sun", "The World",
    "Familiar", "Grim", "Incantation", "Sigil", "Ouija", "Immolate",
    "The Lovers", "The Chariot", "Justice", "The Devil", "The Tower",
    "Talisman", "Aura", "Deja Vu", "Trance", "Medium", "Cryptid",
])
def test_the_consumables_a_shop_refuses(name):
    """They select cards, and the shop has no hand to select from.

    An Arcana or Spectral pack is the exception the game builds for exactly
    this: it deals a hand when it opens so its cards have somewhere to land.
    """
    game = _run("Joker", phase=Phase.SHOP)
    spec = CONSUMABLES[name]
    targets = tuple(game.hand[:max(1, spec.targets)])
    assert not _can(game, name, targets)


def test_a_pack_gives_a_targeting_tarot_somewhere_to_land():
    game = _run("Joker", phase=Phase.PACK)
    assert _can(game, "The Magician", tuple(game.hand[:1]))


# ------------------------------------------------------------------
# room for what they make
# ------------------------------------------------------------------

@pytest.mark.parametrize("name", ["Judgement", "The Soul", "Wraith"])
def test_the_joker_makers_need_a_free_slot(name):
    game = _run()
    assert _can(game, name)
    while len(game.jokers) < game.joker_slots:
        game.gain_joker(JokerInstance(JOKERS["Joker"]))
    assert not _can(game, name)


@pytest.mark.parametrize("name", ["The Emperor", "The High Priestess"])
def test_the_consumable_makers_may_use_their_own_slot(name):
    """`or self.area == G.consumeables` -- using it frees the slot it sits in."""
    game = _run()
    game.consumables.append(game.hold_consumable(CONSUMABLES[name]))
    while len(game.consumables) < game.consumable_slots:
        game.consumables.append(game.hold_consumable(CONSUMABLES["The Fool"]))
    assert _can(game, name)


# ------------------------------------------------------------------
# jokers with no edition yet
# ------------------------------------------------------------------

@pytest.mark.parametrize("name", ["Ectoplasm", "Hex", "The Wheel of Fortune"])
def test_they_need_a_joker_with_no_edition(name):
    """Engine: no jokers = no, all foil = no, one plain among them = yes."""
    game = _run()
    assert not _can(game, name), "an empty row has nothing to edition"

    for _ in range(3):
        joker = JokerInstance(JOKERS["Joker"], edition=Edition.FOIL)
        game.gain_joker(joker)
    assert not _can(game, name), "every joker already has an edition"

    game.gain_joker(JokerInstance(JOKERS["Joker"]))
    assert _can(game, name)


# ------------------------------------------------------------------
# the rest of the special cases
# ------------------------------------------------------------------

def test_aura_refuses_a_card_that_already_has_an_edition():
    """`(not G.hand.highlighted[1].edition)` -- its own branch, its own rule."""
    game = _run()
    card = game.hand[0]
    assert _can(game, "Aura", (card,))
    card.edition = Edition.POLYCHROME
    assert not _can(game, "Aura", (card,))


@pytest.mark.parametrize("name", ["Familiar", "Grim", "Incantation",
                                  "Immolate", "Sigil", "Ouija"])
def test_the_random_destroyers_want_a_card_to_spare(name):
    """`#G.hand.cards > 1`. They eat a card chosen at random."""
    game = _run()
    assert _can(game, name)
    game.hand[:] = game.hand[:1]
    assert not _can(game, name)


def test_the_fool_needs_something_to_copy():
    game = _run()
    game.last_tarot_planet = ""
    assert not _can(game, "The Fool")
    game.last_tarot_planet = "c_death"
    assert _can(game, "The Fool")


def test_the_fool_will_not_copy_itself():
    game = _run()
    game.last_tarot_planet = "c_fool"
    assert not _can(game, "The Fool")


def test_ankh_asks_only_for_a_joker_which_is_the_bug():
    """can_use_consumeable enables it; check_use then refuses a full row.

    The two disagree, and the disagreement is reachable: buy-and-use an Ankh
    with a full joker row and the card is charged for, removed, and filed
    nowhere. Keeping the gate loose here is what makes refuses_use reachable.
    """
    game = _run()
    assert not _can(game, "Ankh"), "no jokers at all"
    game.gain_joker(JokerInstance(JOKERS["Joker"]))
    while len(game.jokers) < game.joker_slots:
        game.gain_joker(JokerInstance(JOKERS["Joker"]))
    assert _can(game, "Ankh"), "the button is live even with a full row"
    assert game.refuses_use(CONSUMABLES["Ankh"]), "and then it says No Room"


# ------------------------------------------------------------------
# what a use costs
# ------------------------------------------------------------------

def test_ectoplasm_costs_more_hand_size_every_time():
    """Engine: eight becomes seven, and ecto_minus is left reading two."""
    game = _run()
    for _ in range(3):
        game.gain_joker(JokerInstance(JOKERS["Joker"]))
    start = game.hand_size

    CONSUMABLES["Ectoplasm"].apply(game, [])
    assert game.hand_size == start - 1
    CONSUMABLES["Ectoplasm"].apply(game, [])
    assert game.hand_size == start - 3        # a further two
    CONSUMABLES["Ectoplasm"].apply(game, [])
    assert game.hand_size == start - 6        # and a further three


def test_ouija_costs_a_flat_one_unlike_ectoplasm():
    game = _run()
    start = game.hand_size
    CONSUMABLES["Ouija"].apply(game, [])
    CONSUMABLES["Ouija"].apply(game, [])
    assert game.hand_size == start - 2


def test_the_legal_action_list_agrees_with_the_gate():
    """Whatever else changes, these two must not drift apart."""
    game = _run("Joker")
    game.hand[:] = []
    game._open_shop()
    game.phase = Phase.SHOP
    game.consumables[:] = [game.hold_consumable(CONSUMABLES[n]) for n in
                           ("The Magician", "Judgement", "Black Hole")]
    for action in game.legal_actions():
        assert game.is_legal(action), action
    offered = {a.index for a in game.legal_actions()
               if a.type is ActionType.USE_CONSUMABLE}
    assert 0 not in offered, "The Magician selects cards; there is no hand"
    assert {1, 2} <= offered
