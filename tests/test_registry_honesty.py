"""What "implemented" currently means, joker by joker.

All 150 are registered, which is what the shop needs -- a joker absent from
the registry can never be offered, and a policy trained against a shop missing
Credit Card is learning a different game. But registration is not
implementation, and the gap between the two is exactly the sort of thing that
rots into a false claim.

So the jokers that carry no behaviour are listed here by name. Two kinds:

  read elsewhere   Four Fingers and Shortcut are read by the hand evaluator,
                   Splash, Smeared Joker, Pareidolia and Oops! All 6s by the
                   run and Chicot by the boss lookup. Having
                   no hook is correct for these.

  not yet built    fifteen that create cards or edit the deck. Their triggers
                   are written down and their text is the game's, but nothing
                   happens when the trigger fires, because card creation is
                   not wired up yet.

The test fails when either list drifts, so a joker cannot quietly slip from
"working" to "declared" and nothing can be added to the second list without
saying so.
"""

from jimbot_sim.jokers import REGISTRY

# Every hook a joker can carry. Kept in step with JokerSpec deliberately: a
# hook missing from this list makes the jokers that use it look behaviourless,
# which is how thirteen implemented jokers were briefly still counted hollow.
HOOKS = ("update", "scored", "lucky_trigger", "held", "independent", "other_joker",
         "round_end", "discarded",
         "retrigger_scored", "retrigger_held", "copier",
         "on_blind_select", "on_round_start", "on_sell", "on_reroll", "on_pack_skip", "on_pack_open",
         "on_cards_destroyed", "on_glass_shattered", "on_shop_end",
         "round_money",
         "before", "before_hand", "after_hand", "on_first_discard",
         "on_debuffed_hand")
DECLARATIONS = ("hand_size", "extra_hands", "extra_discards", "free_rerolls",
                "debt_limit", "interest_bonus", "free_planets",
                "allows_duplicates", "prevents_death",
                "disables_boss_on_sell", "enhancement_gate",
                "hand_size_from_counter", "rerolls_a_hand")

# Correct with no hook: the run or the evaluator reads these directly.
READ_ELSEWHERE = {
    "Four Fingers", "Shortcut",          # hands.evaluate
    "Splash", "Smeared Joker", "Pareidolia", "Oops! All 6s",   # GameState
    "Chicot",                            # GameState.boss
}

# Registered, triggers recorded, effect not yet built. Empty, and worth
# keeping: the two that used to be here were blocked on machinery rather than
# on themselves -- Diet Cola wanted a tag pool that held Double Tag, and
# Hallucination wanted something that opens a booster pack -- and both
# blockers are gone. The set stays so that anything new has to be named.
NOT_YET_BUILT = set()


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


def test_the_hook_list_matches_the_spec():
    """HOOKS must name every callable field JokerSpec has.

    Otherwise this file measures hollowness against a stale list and reports
    working jokers as unimplemented -- or, worse, the reverse.
    """
    import dataclasses

    from jimbot_sim.jokers import JokerSpec

    callable_fields = {
        f.name for f in dataclasses.fields(JokerSpec)
        if f.name not in {"name", "rarity", "text", "cost", "init_counter",
                          "init_secondary", "update_before_scoring"}
        and f.name not in DECLARATIONS}
    assert callable_fields == set(HOOKS), (
        "JokerSpec and HOOKS disagree: %s"
        % sorted(callable_fields ^ set(HOOKS)))


def test_every_name_is_real():
    unknown = (READ_ELSEWHERE | NOT_YET_BUILT) - set(REGISTRY)
    assert not unknown, "listed a joker that is not registered: %s" % sorted(unknown)


def test_every_declaration_is_read_by_something():
    """A declared flag with no reader is a joker that silently does nothing.

    DECLARATIONS is the escape hatch of this file: a joker carrying one of
    these counts as implemented without owning a hook, because the run or the
    evaluator is supposed to read the flag directly. Nothing checked that the
    reader existed. Mr. Bones went the whole project that way -- registered,
    offered in shops, bought, held, and worth exactly nothing, because
    prevents_death appeared once in the spec and nowhere else. It is the
    difference between losing a run and continuing it.
    """
    import pathlib

    package = pathlib.Path(__file__).resolve().parents[1] / "src" / "jimbot_sim"
    body = "\n".join(path.read_text(encoding="utf-8")
                     for path in sorted(package.glob("*.py"))
                     if path.name != "jokers.py")

    unread = [flag for flag in DECLARATIONS if flag not in body]
    assert not unread, (
        "declared on JokerSpec and read by nothing outside jokers.py: %s"
        % sorted(unread))
