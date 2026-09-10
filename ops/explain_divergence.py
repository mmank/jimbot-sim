"""Replay a recording to where it stops, then explain how it got there.

    python ops/explain_divergence.py recordings/8.json
    python ops/explain_divergence.py recordings/8.json --window 30

sim_replay says *that* the simulator diverged and on which field. That is the
first question and never the last one, and answering the rest by hand has cost
this project a great many turns: dump the actions leading in, dump the joker
row and its editions, dump the cards actually in hand, dump what the last
scored hand was made of, dump every dollar that moved. Each of those has
pinpointed a real bug, and each was rebuilt from scratch the next time.

What it prints, in the order it has usually been wanted:

  the divergence      which fields differ, recorded against simulated
  the actions before  side by side, so a field can be watched as it drifts
  the money ledger    every add_money in the last few actions, with reasons
  the scoring         the last hand chip by chip and mult by mult, with
                      running totals
  the position        jokers with editions and counters, the hand with
                      enhancements, editions and seals, vouchers, levels

Three of the five fixes this came out of were found in one of these panes. A
Wheel of Fortune putting its polychrome on the wrong joker was two actions
before a scoring divergence and invisible in the divergence itself. Gold cards
paying 18 dollars instead of 24 was a money ledger. A copier contributing
nothing to a hand was a scoring breakdown with a joker missing from it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def replay_module():
    spec = importlib.util.spec_from_file_location(
        "sim_replay", ROOT / "ops" / "sim_replay.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def joker_line(joker) -> str:
    bits = [joker.name]
    if joker.edition.value != "none":
        bits.append(joker.edition.value)
    if joker.counter:
        bits.append("counter=%g" % joker.counter)
    for flag in ("eternal", "rental", "perishable", "debuffed"):
        if getattr(joker, flag, False):
            bits.append(flag)
    return " ".join(bits)


def card_line(card) -> str:
    bits = ["%s%s" % (card.rank.name[:5].title(), card.suit.name[0])]
    for name, value in (("enh", card.enhancement.value),
                        ("ed", card.edition.value),
                        ("seal", card.seal.value)):
        if value != "none":
            bits.append("%s=%s" % (name, value))
    if card.debuffed:
        bits.append("debuffed")
    return " ".join(bits)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("recording", type=Path)
    parser.add_argument("--window", type=int, default=20,
                        help="how many actions before the stop to show")
    parser.add_argument("--ledger", type=int, default=4,
                        help="how many actions to log dollar movements for")
    args = parser.parse_args()

    from jimbot_sim.game import GameState
    from jimbot_sim import scoring as scoring_mod
    import jimbot_sim.game as game_mod

    replay = replay_module()
    payload = json.loads(args.recording.read_text(encoding="utf-8"))
    actions = replay.merge_buy_and_use(payload["actions"])
    game = GameState(seed=payload["seed"], deck=payload["deck"],
                     stake=payload.get("stake") or 1, endless=True)
    if payload.get("money") is not None:
        game.money = payload["money"]
    deck_index = {card.uid: i for i, card in enumerate(game.full_deck)}

    # Watch the scoring and the money without changing either.
    scored = []
    original_score = scoring_mod.score_hand

    def watch_score(g, result, played, held):
        ctx = original_score(g, result, played, held)
        scored.append(ctx)
        return ctx

    scoring_mod.score_hand = watch_score
    game_mod.score_hand = watch_score

    ledger = []
    watching = {"step": 0}
    original_money = GameState.add_money

    def watch_money(self, amount, reason=""):
        if amount:
            ledger.append((watching["step"], amount, reason,
                           self.money + amount))
        return original_money(self, amount, reason)

    GameState.add_money = watch_money

    trail = []
    stop_at, problems, stop_reason = None, [], None
    for i, entry in enumerate(actions, start=1):
        watching["step"] = i
        recorded = entry.get("before") or {}
        replay.match_hand_order(game, recorded.get("hand_ids"), deck_index)
        replay.match_joker_order(game, recorded.get("jokers"))
        trail.append((i, entry, dict(recorded), {
            "dollars": game.money, "chips": game.chips_scored,
            "hands_left": game.hands_left,
            "discards_left": game.discards_left}))

        found = replay.differences(recorded, replay.sim_view(game))
        if found:
            stop_at, problems = i, found
            break
        try:
            reason = replay.apply(game, entry["action"],
                                  entry.get("params") or {}, None)
        except Exception as error:                  # the driver giving up
            stop_at, stop_reason = i, "driver raised: %s" % error
            break
        if reason is not None:
            stop_at, stop_reason = i, reason
            break
    else:
        print("  the simulator followed the whole recording")
        return

    print("=" * 78)
    print("  %s stopped at step %d of %d, before %s"
          % (args.recording.name, stop_at, len(actions),
             actions[stop_at - 1]["action"]))
    print("=" * 78)
    for line in problems:
        print(line.rstrip())
    if stop_reason:
        print("  %s" % stop_reason)

    print("")
    print("--- the %d actions leading in ---" % args.window)
    print("  %4s %-26s %-24s %-24s" % ("step", "action", "recorded",
                                       "simulated"))
    for i, entry, recorded, mine in trail[-args.window:]:
        rec = "$%s chips=%s h/d=%s/%s" % (
            recorded.get("dollars"), recorded.get("chips"),
            recorded.get("hands_left"), recorded.get("discards_left"))
        sim = "$%s chips=%s h/d=%s/%s" % (
            mine["dollars"], mine["chips"], mine["hands_left"],
            mine["discards_left"])
        flag = "  <-- differs" if rec != sim else ""
        print("  %4d %-26s %-24s %-24s%s"
              % (i, entry["action"][:26], rec, sim, flag))
        params = entry.get("params")
        if params:
            print("       %s" % str(params)[:94])

    print("")
    print("--- every dollar that moved in the last %d actions ---" % args.ledger)
    recent = [row for row in ledger if row[0] > stop_at - args.ledger]
    if not recent:
        print("  nothing moved")
    for step, amount, reason, after in recent:
        print("  step %-4d %+5d  %-38s -> $%d" % (step, amount, reason, after))

    if scored:
        print("")
        print("--- the last hand the simulator scored ---")
        ctx = scored[-1]
        print("  %s, %d scoring cards: %s"
              % (ctx.hand.label, len(ctx.scoring),
                 ", ".join(card_line(c) for c in ctx.scoring)))
        for line in ctx.log:
            print("    " + line)
        print("  final %g chips x %g mult = %d"
              % (ctx.chips, ctx.mult, ctx.score))

    print("")
    print("--- the position where it stopped ---")
    print("  jokers, in row order, which is what a Blueprint reads:")
    for joker in game.jokers:
        print("    %s" % joker_line(joker))
    print("  hand:")
    for n, card in enumerate(game.hand, start=1):
        print("    %2d %s" % (n, card_line(card)))
    print("  vouchers: %s" % ", ".join(v.key for v in game.vouchers))
    print("  $%d, ante %d, %s" % (game.money, game.ante,
                                  getattr(game.blind, "name", "no blind")))
    levels = {h.label: game.hand_levels.levels[h]
              for h in game.hand_levels.levels
              if game.hand_levels.levels[h] != 1}
    print("  hand levels above one: %s" % (levels or "none"))


if __name__ == "__main__":
    main()
