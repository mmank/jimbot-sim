"""Three simulator-only effects a headless smoke test found.

1. Burnt Joker does not level on a Hook discard.

   vendor/balatro_src/card.lua:2749

       if self.ability.name == 'Burnt Joker'
          and G.GAME.current_round.discards_used <= 0 and not context.hook then

   The Hook calls G.FUNCS.discard_cards_from_highlighted(nil, true)
   (blind.lua:482), and that passes the flag into the pre_discard context
   (functions/state_events.lua:395). The simulator levelled the two cards The
   Hook took: K0ET7R2V, Erratic Deck, stake 3, had High Card at level 2 after
   one Straight into The Hook.

2. The Emperor cannot make another Emperor.

   G.FUNCS.use_card takes the card out of its area (button_callbacks.lua:2209)
   but it is not removed -- and G.GAME.used_jokers is not cleared
   (card.lua:4741-4749) -- until it dissolves, after its effect has run
   (button_callbacks.lua:2258-2260). The Emperor's two Tarots are created
   inside that window (card.lua:1401-1411), and get_current_pool blanks every
   centre in used_jokers (functions/common_events.lua:1987), so The Emperor
   itself is not in the pool it draws from. The simulator popped the card first
   and drew against a pool with The Emperor back in it: KTBCH0MX (Magic, 5) got
   [Hermit, Emperor] where the game made [Hermit, Temperance], and UCEMEN3F
   (Painted, 1) got [Star, Emperor] where the game made [Star, Justice].

3. 8 Ball spends no roll when the consumable row is full.

   card.lua:3106-3107

       if self.ability.name == '8 Ball' and #G.consumeables.cards
          + G.GAME.consumeable_buffer < G.consumeables.config.card_limit then
           if (context.other_card:get_id() == 8)
              and (pseudorandom('8ball') < G.GAME.probabilities.normal/...) then

   Room first, then the roll. The simulator rolled for every scored 8, so a
   hand of three 8s that made a Tarot on the first spent two draws the game
   never took, and 90WTJQJP (Nebula, 3) missed a High Priestess nineteen
   decisions later.
"""

from jimbot_sim.blinds import BOSSES, BlindKind, make_blind
from jimbot_sim.cards import Rank
from jimbot_sim.consumables import REGISTRY as CONSUMABLES
from jimbot_sim.game import Action, ActionType, GameState, Phase
from jimbot_sim.jokers import REGISTRY as JOKERS, JokerInstance

HOOK = next(b for b in BOSSES if b.name == "The Hook")


def _levels(game) -> dict:
    return {h.label: n for h, n in game.hand_levels.levels.items() if n != 1}


# ------------------------------------------------------------------
# Burnt Joker and The Hook
# ------------------------------------------------------------------

def _burnt_on_the_hook(seed: str = "TESTSEED") -> GameState:
    game = GameState(seed=seed)
    game.gain_joker(JokerInstance(JOKERS["Burnt Joker"]))
    game._start_round()
    game.blind = make_blind(BlindKind.BOSS, 1, HOOK)
    game.hand[:] = game.hand[:4]
    return game


def test_burnt_joker_ignores_a_hook_discard():
    game = _burnt_on_the_hook()
    discard_before = len(game.discard_pile)

    game._play((0, 1))

    assert len(game.discard_pile) - discard_before >= 2, (
        "The Hook took nothing, so this test proves nothing")
    assert _levels(game) == {}, (
        "Burnt Joker levelled the cards The Hook discarded: %s" % _levels(game))


def test_burnt_joker_still_levels_the_first_real_discard_after_a_hook():
    """A Hook discard does not count as the round's discard either."""
    game = _burnt_on_the_hook()
    game._play((0, 1))
    assert game.discards_used == 0

    card = game.hand[0]
    game._discard((0,))

    assert _levels(game) == {"High Card": 2}, (
        "the first discard of the round, a lone %s, did not level High Card: "
        "%s" % (card, _levels(game)))


# ------------------------------------------------------------------
# The Emperor
# ------------------------------------------------------------------

SEEDS = ["EMP%04d" % i for i in range(160)]


def _made_by_emperor(seed: str, held: bool) -> list[str]:
    game = GameState(seed=seed, deck="Red Deck")
    game._start_round()
    game.phase = Phase.PLAYING
    game.consumables.clear()
    if held:
        game.consumables.append(game.hold_consumable(CONSUMABLES["The Emperor"]))
        game.step(Action(ActionType.USE_CONSUMABLE, index=0))
    else:
        # buy-and-use and a pack pick: the card is in no area while it is used
        game.use_consumable(CONSUMABLES["The Emperor"], [])
    return [c.name for c in game.consumables]


def test_the_emperor_used_from_a_slot_never_makes_an_emperor():
    made = {seed: _made_by_emperor(seed, held=True) for seed in SEEDS}
    assert all(len(names) == 2 for names in made.values())
    bad = {seed: names for seed, names in made.items() if "The Emperor" in names}
    assert not bad, "The Emperor made itself: %s" % bad


def test_the_emperor_used_from_nowhere_never_makes_an_emperor():
    made = {seed: _made_by_emperor(seed, held=False) for seed in SEEDS}
    bad = {seed: names for seed, names in made.items() if "The Emperor" in names}
    assert not bad, "The Emperor made itself: %s" % bad


def test_the_emperor_is_back_in_the_pool_once_it_is_gone():
    """used_jokers is cleared when the card is removed, so the next draw may
    offer an Emperor again -- the blank lasts exactly as long as the use."""
    game = GameState(seed="TESTSEED", deck="Red Deck")
    game._start_round()
    game.phase = Phase.PLAYING
    game.use_consumable(CONSUMABLES["The Emperor"], [])
    assert "c_emperor" not in game.seen_centers


# ------------------------------------------------------------------
# 8 Ball
# ------------------------------------------------------------------

def _eights(seed: str, full_row: bool) -> GameState:
    game = GameState(seed=seed, deck="Red Deck")
    game.gain_joker(JokerInstance(JOKERS["8 Ball"]))
    game._start_round()
    game.blind = make_blind(BlindKind.BOSS, 1, None)
    game.consumables.clear()
    if full_row:
        while len(game.consumables) < game.consumable_slots:
            game.consumables.append(
                game.hold_consumable(CONSUMABLES["The Hermit"]))
    for card in game.hand[:4]:
        card.rank = Rank.EIGHT
        card.enhancement = card.enhancement.__class__.NONE
        card.debuffed = False
    return game


def test_8_ball_spends_no_roll_with_a_full_row():
    game = _eights("TESTSEED", full_row=True)
    game._play((0, 1, 2, 3))

    fresh = GameState(seed="TESTSEED", deck="Red Deck")
    assert (game.rng.pseudorandom("8ball")
            == fresh.rng.pseudorandom("8ball")), (
        "8 Ball rolled for its 8s with no room for the Tarot")


def test_8_ball_stops_rolling_once_its_tarot_fills_the_row():
    """Room is re-read for every 8 -- consumeable_buffer is the game's way of
    counting a Tarot it has promised but not yet built -- so with one free
    slot the rolls stop at the first success."""
    fired_somewhere = False
    for i in range(40):
        seed = "EIGHT%03d" % i
        game = _eights(seed, full_row=True)
        game.consumables.pop()               # exactly one free slot
        rolls = []
        original = game.rng.chance

        def chance(key, numerator, denominator, _orig=original, _r=rolls):
            hit = _orig(key, numerator, denominator)
            if key == "8ball":
                _r.append(hit)
            return hit

        game.rng.chance = chance
        game._play((0, 1, 2, 3))
        if True in rolls:
            fired_somewhere = True
            assert rolls.index(True) == len(rolls) - 1, (
                "%s: 8 Ball kept rolling after its Tarot filled the row: %s"
                % (seed, rolls))
    assert fired_somewhere, "no seed made a Tarot, so this proves nothing"
