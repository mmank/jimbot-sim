"""Play the same run in both engines and report where they part company.

Everything else verified here is a component: scoring a hand, dealing a hand,
which joker a shop draw yields. None of it says the simulator can play a
*run*, and until something does, "the simulator works" is an assumption made
of parts that have never been assembled.

So this drives the real engine and the Python simulator through the same
choices from the same seed and compares the whole state after every action.
Choices are made once, by the simulator, and applied to both -- picking
independently would compare two different games and call the difference a bug.

    python ops/run_differential.py --seed TESTSEED --steps 200

It is meant to fail. Each failure is one more thing the simulator does not
model yet, named precisely instead of guessed at.
"""

import argparse
import itertools
import sys

sys.path.insert(0, "src")

from balatro.game import Action, ActionType, GameState   # noqa: E402
from balatro.hands import HandType, evaluate                       # noqa: E402
from balatro_headless.runtime import HeadlessBalatro     # noqa: E402

RANK_NAME = {"Two": "2", "Three": "3", "Four": "4", "Five": "5", "Six": "6",
             "Seven": "7", "Eight": "8", "Nine": "9", "Ten": "10",
             "Jack": "Jack", "Queen": "Queen", "King": "King", "Ace": "Ace"}

BLIND_SELECT, SELECTING_HAND, SHOP, ROUND_EVAL, GAME_OVER = 7, 1, 5, 8, 4


def engine_state(engine):
    ev = lambda body: engine.eval("(function() %s end)()" % body)
    hand = ev('local t = {} for i, c in ipairs(G.hand.cards) do '
              't[i] = tostring(c.base.value) .. "/" .. tostring(c.base.suit) end '
              'return table.concat(t, " ")')
    jokers = ev('local t = {} for i, c in ipairs(G.jokers.cards) do '
                't[i] = c.config.center.key end return table.concat(t, " ")')
    phase = {7: "blind_select", 1: "playing", 8: "round_eval", 5: "shop",
             2: "playing", 3: "playing", 4: "game_over"}.get(
                 int(ev("return G.STATE")), "other")
    return {
        "phase": phase,
        "ante": int(ev("return G.GAME.round_resets.ante")),
        "round": int(ev("return G.GAME.round")),
        "dollars": int(ev("return G.GAME.dollars")),
        "hands_left": int(ev("return G.GAME.current_round.hands_left")),
        "discards_left": int(ev("return G.GAME.current_round.discards_left")),
        "chips": int(ev("return G.GAME.chips")),
        "blind_chips": int(ev("return G.GAME.blind and G.GAME.blind.chips or 0")),
        "boss": ev("return tostring(G.GAME.blind and G.GAME.blind.name or '')"),
        "deck": int(ev("return #G.deck.cards")),
        "hand": hand.split(" ") if hand else [],
        "jokers": jokers.split(" ") if jokers else [],
    }


def sim_state(game):
    return {
        "ante": game.ante,
        "phase": game.phase.value,
        "round": game.round_number,
        "dollars": game.money,
        "hands_left": game.hands_left,
        "discards_left": game.discards_left,
        "chips": game.chips_scored,
        "blind_chips": game.blind_target,
        "boss": game.blind_name,
        "deck": len(game.draw_pile),
        "hand": ["%s/%s" % (RANK_NAME[c.rank.name.title()], c.suit.name.title())
                 for c in game.hand],
        "jokers": [j.name for j in game.jokers],
    }


def best_five(game):
    """The highest-scoring five cards the simulator can see.

    Deliberately simple. The choice only has to be *legal and identical* on
    both sides; how good it is decides how deep the run gets before the
    comparison runs out of game, not whether the comparison is valid.
    """
    hand = game.hand
    best, best_score = None, -1
    for size in (5, 4, 3, 2, 1):
        for combo in itertools.combinations(range(len(hand)), size):
            cards = [hand[i] for i in combo]
            result = evaluate(cards)
            chips, mult = game.hand_levels.values(result.hand)
            score = (chips + sum(c.base_chips for c in result.scoring)) * mult
            if score > best_score:
                best, best_score = combo, score
        if best is not None and size == 5:
            break
    return best


def differences(left, right):
    return ["  %-14s engine %s\n  %-14s sim    %s" % (k, left[k], "", right.get(k))
            for k in sorted(left) if left[k] != right.get(k)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default="TESTSEED")
    parser.add_argument("--deck", default="Red Deck")
    parser.add_argument("--steps", type=int, default=200)
    args = parser.parse_args()

    engine = HeadlessBalatro().boot()
    engine.execute('BOT.start_run({"%s","%s"}); api.pump(300)'
                   % (args.seed, args.deck.replace(" ", "_")))
    game = GameState(seed=args.seed, deck=args.deck)

    print("comparing %s, %s\n" % (args.seed, args.deck))
    state_of = lambda: int(engine.eval("(function() return G.STATE end)()"))

    settled = (BLIND_SELECT, SELECTING_HAND, SHOP, ROUND_EVAL,
               GAME_OVER)

    def settle():
        """Let the engine finish resolving before comparing.

        A played hand passes through HAND_PLAYED and DRAW_TO_HAND
        before the game decides whether the round is over, so
        comparing the instant the pump returns catches the engine
        mid-thought and reports it as the simulator being wrong.
        """
        for _ in range(40):
            if state_of() in settled:
                return
            engine.execute("api.pump(60)")

    for step in range(1, args.steps + 1):
        phase = state_of()

        if phase == GAME_OVER:
            print("\nrun ended after %d steps (engine reached GAME_OVER)" % step)
            return
        if phase == BLIND_SELECT:
            engine.execute("api.select_blind(); api.pump(200)")
            game.step(Action(ActionType.SELECT_BLIND))
            what = "select_blind"
        elif phase == SELECTING_HAND:
            picks = best_five(game)
            # Discard the cards the best hand does not use, while discards
            # remain and the hand is weak. Without this the run never beats
            # the first blind, and everything past it -- cash out, the shop,
            # the next ante -- stays untested.
            kept = [game.hand[i] for i in picks]
            hand_type = evaluate(kept).hand
            if (game.discards_left > 0 and game.hands_left > 1
                    and hand_type.value <= HandType.PAIR.value):
                junk = [i for i in range(len(game.hand)) if i not in picks][:5]
                if junk:
                    engine.execute(
                        "api.clear_highlights(); api.highlight({%s}); "
                        "api.pump(10); api.discard_selected(); api.pump(600)"
                        % ",".join(str(i + 1) for i in junk))
                    game.step(Action(ActionType.DISCARD, cards=tuple(junk)))
                    settle()
                    left, right = engine_state(engine), sim_state(game)
                    problems = differences(left, right)
                    if problems:
                        print("step %d (discard %s) DIVERGED:" % (step, junk))
                        for line in problems:
                            print(line)
                        return
                    print("step %3d %-18s ok   ante %d round %d  $%-4d "
                          "chips %-6d hands %d"
                          % (step, "discard %s" % junk, left["ante"],
                             left["round"], left["dollars"], left["chips"],
                             left["hands_left"]))
                    continue
            engine.execute("api.clear_highlights(); api.highlight({%s}); "
                           "api.pump(10); api.play_selected(); api.pump(900)"
                           % ",".join(str(i + 1) for i in picks))
            game.step(Action(ActionType.PLAY, cards=tuple(picks)))
            what = "play %s" % list(picks)
        elif phase == ROUND_EVAL:
            engine.execute("api.cash_out(); api.pump(400)")
            game.step(Action(ActionType.CASH_OUT))
            what = "cash_out"
        elif phase == SHOP:
            engine.execute("api.leave_shop(); api.pump(300)")
            game.step(Action(ActionType.LEAVE_SHOP))
            what = "leave_shop"
        else:
            print("step %d: engine is in state %d, which this script does not "
                  "drive yet" % (step, phase))
            return

        settle()
        left, right = engine_state(engine), sim_state(game)
        problems = differences(left, right)
        if problems:
            print("step %d (%s) DIVERGED:" % (step, what))
            for line in problems:
                print(line)
            return
        print("step %3d %-18s ok   ante %d round %d  $%-4d chips %-6d "
              "hands %d" % (step, what, left["ante"], left["round"],
                            left["dollars"], left["chips"], left["hands_left"]))

    print("\nno divergence in %d steps" % args.steps)


if __name__ == "__main__":
    main()
