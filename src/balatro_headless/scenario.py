"""Force an exact game state, so a result can be checked rather than trusted.

    game = HeadlessBalatro().boot()
    s = Scenario(game).start().hand("S_A S_K S_Q S_J S_T").jokers("j_greedy_joker")
    s.play([1, 2, 3, 4, 5])        # -> 1208, every time

Two things need this. Regression tests for the engine: we have been deleting
work from the game's Lua -- per-frame card updates, the autosave, overlay
menus -- on the evidence of three recordings, which do not cover most jokers or
most bosses. A pinned scenario says what a specific joker with a specific hand
scores, and stays true or fails loudly.

And differential testing of any second implementation. Comparing two engines
means putting both in the same position, and "the same position" has to be
constructible rather than waited for: a Blueprint copying a Baron with two
steel kings in hand will not turn up by playing randomly.

Card codes are the game's own: rank in A23456789TJQK, suit in SHDC, so "S_A"
is the ace of spades. Joker and enhancement keys are the game's too.
"""

from __future__ import annotations

from typing import Iterable


class Scenario:
    """A game forced into a known state."""

    def __init__(self, game) -> None:
        self.game = game

    # -- setup ---------------------------------------------------------------

    def start(self, seed: str = "TESTSEED", deck: str = "Red Deck",
              stake: int = 1) -> "Scenario":
        self.game.execute(
            f'BOT.start_run({{"{seed}","{deck.replace(" ", "_")}",{stake}}})')
        self.game.execute("api.pump(300)")
        self.game.execute("api.select_blind()")
        # A blind that cannot be beaten by accident, so a measurement is not
        # cut short by the round ending.
        self.game.execute("G.GAME.blind.chips = 999999999; api.pump(60)")
        return self

    def hand(self, codes: str | Iterable[str]) -> "Scenario":
        """Set the held cards, in order, by code: "S_A S_K H_2".

        The alignment at the end is not cosmetic. Only the *bases* are
        rewritten here, so the cards keep whatever screen positions the deal
        left them with -- and a freshly dealt hand does not have them in
        array order, because the last card drawn has not animated into place.
        Measured on an eight-card hand: card 1 sat at x=15.14 with cards 2
        through 8 running 4.90 to 13.68, so the first card of the hand was
        physically the rightmost.

        That matters because the game orders scoring_hand by T.x rather than
        by array index. Hanging Chad retriggers scoring_hand[1], and against
        an unaligned hand it retriggered the second card -- which read as the
        simulator disagreeing with the engine over a flush, and was the
        harness placing the cards somewhere the scenario never asked for.
        align_cards puts the row back in array order.
        """
        if isinstance(codes, str):
            codes = codes.split()
        for i, code in enumerate(codes, start=1):
            self.game.execute(
                f'if G.hand.cards[{i}] then '
                f'G.hand.cards[{i}]:set_base(G.P_CARDS["{code}"]) end')
        self.game.execute("G.hand:align_cards(); api.pump(30)")
        return self

    def boss(self, key: str) -> "Scenario":
        """Play against a specific boss blind.

        Nothing else in the harness reaches a boss: a scenario forces a huge
        chip target on whatever blind the run opens with, which is always a
        small blind, so all twenty-eight boss effects -- and the jokers that
        answer them, Chicot and Luchador -- sat untested.
        """
        self.game.execute(
            f'G.GAME.blind:set_blind(G.P_BLINDS["{key}"], nil, nil); '
            f'G.GAME.blind.chips = 999999999; api.pump(90)')
        return self

    def enhance(self, index: int, enhancement: str | None = None,
                seal: str | None = None, edition: str | None = None
                ) -> "Scenario":
        """Put an enhancement, seal or edition on one held card."""
        if enhancement:
            self.game.execute(
                f'G.hand.cards[{index}]:set_ability('
                f'G.P_CENTERS["{enhancement}"], nil, true)')
        if seal:
            self.game.execute(f'G.hand.cards[{index}]:set_seal("{seal}", true)')
        if edition:
            self.game.execute(
                f'G.hand.cards[{index}]:set_edition({{{edition} = true}}, '
                f'true, true)')
        self.game.execute("api.pump(30)")
        return self

    def jokers(self, keys: str | Iterable[str]) -> "Scenario":
        """Replace the jokers with exactly these, in order."""
        if isinstance(keys, str):
            keys = keys.split()
        self.game.execute(
            "for i = #G.jokers.cards, 1, -1 do "
            "G.jokers:remove_card(G.jokers.cards[i]) end")
        for key in keys:
            self.game.execute(
                f'local c = create_card("Joker", G.jokers, nil, nil, nil, '
                f'nil, "{key}"); c:add_to_deck(); G.jokers:emplace(c)')
        self.game.execute("api.pump(120)")
        return self

    def hand_level(self, hand: str, level: int) -> "Scenario":
        """Set a poker hand's level, as planet cards would."""
        self.game.execute(
            f'level_up_hand(nil, "{hand}", true, '
            f'{level} - G.GAME.hands["{hand}"].level); api.pump(60)')
        return self

    # -- measurement ---------------------------------------------------------

    def select(self, indices: Iterable[int]) -> "Scenario":
        """Highlight these held cards without playing them.

        Worth doing separately: the game only fills in
        current_round.current_hand once cards are selected, so anything that
        wants to know which poker hand is about to be played -- or read run
        state as it will be when the jokers score -- has to select first.
        """
        picks = ",".join(str(i) for i in indices)
        self.game.execute("api.clear_highlights()")
        self.game.execute(f"api.highlight({{{picks}}})")
        self.game.execute("api.pump(10)")
        return self

    def play(self, indices: Iterable[int] | None = None) -> int:
        """Play the selected cards and return exactly what they scored."""
        if indices is not None:
            self.select(indices)
        before = int(self.game.eval("G.GAME.chips"))
        self.game.execute("api.play_selected(); api.pump(900)")
        return int(self.game.eval("G.GAME.chips")) - before

    def state(self) -> dict:
        return dict(self.game.eval("BOT.state()"))
