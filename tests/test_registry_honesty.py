"""What "implemented" currently means, joker by joker.

All 150 are registered, which is what the shop needs -- a joker absent from
the registry can never be offered, and a policy trained against a shop missing
Credit Card is learning a different game. But registration is not
implementation, and the gap between the two is exactly the sort of thing that
rots into a false claim.

So the jokers that carry no behaviour are listed here by name. Two kinds:

  read elsewhere   Four Fingers and Shortcut are read by the hand evaluator,
                   Splash, Smeared Joker, Pareidolia and Oops! All 6s by the
                   run, Chicot by the boss lookup, Perkeo by the shop. Having
                   no hook is correct for these.

  not yet built    fifteen that create cards or edit the deck. Their triggers
                   are written down and their text is the game's, but nothing
                   happens when the trigger fires, because card creation is
                   not wired up yet.

The test fails when either list drifts, so a joker cannot quietly slip from
"working" to "declared" and nothing can be added to the second list without
saying so.
"""

from balatro.jokers import REGISTRY

HOOKS = ("update", "scored", "held", "independent", "round_end", "discarded",
         "retrigger_scored", "retrigger_held", "copier")
DECLARATIONS = ("hand_size", "extra_hands", "extra_discards", "free_rerolls",
                "debt_limit", "interest_bonus", "free_planets",
                "allows_duplicates", "prevents_death",
                "disables_boss_on_sell", "enhancement_gate")

# Correct with no hook: the run or the evaluator reads these directly.
READ_ELSEWHERE = {
    "Four Fingers", "Shortcut",          # hands.evaluate
    "Splash", "Smeared Joker", "Pareidolia", "Oops! All 6s",   # GameState
    "Chicot",                            # GameState.boss
    "Perkeo",                            # shop, on leaving
}

# Registered, triggers recorded, effect not yet built. Every one of these
# creates a card or edits the deck.
NOT_YET_BUILT = {
    "8 Ball", "Burnt Joker", "Cartomancer", "Certificate", "DNA",
    "Diet Cola", "Gift Card", "Hallucination", "Marble Joker", "Riff-Raff",
    "Sixth Sense", "Superposition", "S\u00e9ance", "Trading Card", "Vagabond",
}


def _behaviourless():
    return {name for name, spec in REGISTRY.items()
            if not any(getattr(spec, h) for h in HOOKS)
            and not any(getattr(spec, d) for d in DECLARATIONS)}


def test_all_150_are_registered():
    assert len(REGISTRY) == 150


def test_the_hollow_jokers_are_the_ones_we_say_they_are():
    assert _behaviourless() == READ_ELSEWHERE | NOT_YET_BUILT


def test_nothing_claims_to_be_both():
    assert not (READ_ELSEWHERE & NOT_YET_BUILT)


def test_every_name_is_real():
    unknown = (READ_ELSEWHERE | NOT_YET_BUILT) - set(REGISTRY)
    assert not unknown, "listed a joker that is not registered: %s" % sorted(unknown)
