"""Where a run's money comes from and where it goes, round by round.

Marcin, on the two recordings he added: *"look how much money is being
generated especially"*. That question was answered before off the recording
JSON, which can only see the balance between actions -- a number that has
already netted a payout against a purchase. Now that every recording replays
end to end the simulator itself can be asked, and it books every dollar:
`add_money` carries a reason string, and the one flow that does not go
through it -- interest, which is folded into `pending_payout` -- is worked
out here from the balance the game reads it off.

Run it on a recording, or on a policy run with `--policy SEED`, and the two
come out on the same form so they can be put side by side.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from jimbot_sim.game import GameState, BlindKind          # noqa: E402
import sim_replay                                          # noqa: E402


# What to call each `add_money` source. Anything unrecognised keeps its own
# name, so a source nobody thought of shows up rather than vanishing into an
# "other" bucket.
def bucket(source: str) -> str:
    s = source.lower()
    if "payout" in s:
        return "blind + hands + interest"
    if "rental" in s:
        return "rental"
    if "sold" in s or "sell" in s:
        return "sold"
    if "reroll" in s:
        return "reroll"
    if "bought" in s or "buy" in s or "purchase" in s:
        return "bought"
    if s == "cards":
        # ScoreContext.money_gained: everything paid out *while a hand
        # scores* -- a Lucky card hitting, a gold seal, Business Card, a
        # Golden Ticket on a played gold card, Rough Gem.
        return "scoring"
    if s == "gold cards":
        # $3 a gold card *held* at the end of a round, paid in the hand loop.
        return "gold held"
    return source


class Ledger:
    """Every dollar in and out, tagged with the round it moved in."""

    def __init__(self, game: GameState):
        self.game = game
        self.rows: list[dict] = []
        self.entries: list[tuple] = []
        self._round = 0
        self._wrap()

    def _wrap(self):
        """Patch the *class*, and answer only for this game.

        Patching the instance does not work, and the way it fails is worth
        writing down. `Position.fork` deepcopies the run, and `copy` treats a
        function as atomic -- so the copy comes away holding the very same
        closure, still bound to the original game. Every dollar a fork
        imagined then landed on the real run and was counted, which both
        inflated the ledger and changed the run it was measuring.

        A class-level patch is shared by every copy, so the guard is
        identity: this ledger books a move only when it is this game moving.
        """
        game = self.game
        cls = type(game)
        real_add = cls.add_money
        real_beat = cls._beat_blind

        def add_money(this, amount: int, source: str = ""):
            if amount and this is game:
                self.entries.append((self._round, this.ante, source, amount))
            real_add(this, amount, source)

        def beat_blind(this, reward: bool = True):
            if this is not game:
                return real_beat(this, reward)
            # Read the interest off the balance the game reads it off: after
            # the end-of-round joker pass and the rent, before the payout.
            before = game.money
            blind = game.blind
            real_beat(this, reward)
            after_rent = game.money
            per_block = 1 + sum(j.spec.interest_bonus
                                for j in game.active_jokers)
            no_interest = game.deck_config.get("no_interest")
            blocks = 0 if no_interest else min(game.interest_cap,
                                               max(0, after_rent) // 5)
            self._round += 1
            self.rows.append({
                "round": self._round,
                "ante": game.ante,
                "blind": blind.name if blind else "?",
                "kind": blind.kind.name if blind else "?",
                "money_before_payout": after_rent,
                "interest": per_block * blocks,
                "interest_blocks": blocks,
                "interest_cap": game.interest_cap,
                "interest_forgone": max(0, game.interest_cap - blocks),
                "reward": (blind.reward if (reward and blind) else 0),
                "hands_left": max(0, game.hands_left),
                "payout": game.pending_payout,
                "_bal_at_beat": before,
            })

        cls.add_money = add_money
        cls._beat_blind = beat_blind
        self._restore = (cls, real_add, real_beat)

    def close(self) -> None:
        """Put the class back, so one ledger cannot stack on the next."""
        cls, real_add, real_beat = self._restore
        cls.add_money = real_add
        cls._beat_blind = real_beat

    def by_round(self) -> list[dict]:
        """Attach each round's non-payout flows to its row."""
        flows = collections.defaultdict(lambda: collections.Counter())
        for rnd, _ante, source, amount in self.entries:
            flows[rnd][bucket(source)] += amount
        for row in self.rows:
            # A round's shop happens *after* its cash-out, and the ledger
            # counts a cash-out as ending the round -- so the spending that
            # belongs to a round is what happened before the next one beat
            # its blind.
            row["flows"] = dict(flows.get(row["round"], {}))
        return self.rows


def run_policy(seed: str, deck: str = "Red Deck", stake: int = 1):
    """The same ledger, off a policy run, so the two can be put side by side."""
    import os.path
    root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    sys.path.insert(0, os.path.join(root, "src"))
    from handcrafted.config import Config
    from handcrafted.drive import is_legal
    from handcrafted.policy import Policy

    game = GameState(seed=seed, deck=deck, stake=stake)
    ledger = Ledger(game)
    policy = Policy(Config())
    policy.reset()
    n = 0
    while not game.is_over and n < 4000:
        action, _why = policy.decide(game)
        if not is_legal(game, action):
            break
        game.step(action)
        n += 1
    return ledger, game, n, n


def run_recording(path: str) -> tuple[Ledger, GameState, int, int]:
    payload = json.loads(open(path).read())
    actions = sim_replay.merge_buy_and_use(payload["actions"])
    game = GameState(seed=payload["seed"], deck=payload["deck"],
                     stake=payload.get("stake") or 1)
    if payload.get("money") is not None:
        game.money = payload["money"]
    ledger = Ledger(game)
    deck_index = {card.uid: i for i, card in enumerate(game.full_deck)}
    done = 0
    for entry in actions:
        recorded = entry.get("before") or {}
        sim_replay.match_hand_order(game, recorded.get("hand_ids"), deck_index)
        sim_replay.match_joker_order(game, recorded.get("jokers"))
        if sim_replay.apply(game, entry["action"],
                            entry.get("params") or {}, None) is not None:
            break
        done += 1
    return ledger, game, done, len(actions)


def report(name: str, ledger: Ledger, game: GameState,
           quiet: bool = False) -> None:
    rows = ledger.by_round()
    print("== %s ==" % name)
    if quiet:
        return summary(rows, game)
    print("%-3s %-4s %-14s %6s %6s %5s %6s   %s"
          % ("rnd", "ante", "blind", "bal", "intr", "blk/c", "payout",
             "other flows"))
    for r in rows:
        others = ", ".join("%s %+d" % (k, v)
                           for k, v in sorted(r["flows"].items())
                           if k != "blind + hands + interest")
        print("%-3d %-4d %-14s %6d %6d %2d/%-2d %6d   %s"
              % (r["round"], r["ante"], r["blind"][:14],
                 r["money_before_payout"], r["interest"],
                 r["interest_blocks"], r["interest_cap"], r["payout"],
                 others))
    summary(rows, game)


def summary(rows, game) -> None:
    if rows:
        n = len(rows)
        got = sum(r["interest"] for r in rows)
        cap = sum(r["interest_cap"] for r in rows)
        print("\n  rounds %d   interest $%d total, $%.2f/round"
              % (n, got, got / n))
        print("  of a possible $%d -- %.0f%% of the interest on the table"
              % (cap, 100.0 * got / cap if cap else 0))
        full = sum(1 for r in rows if r["interest_forgone"] == 0)
        print("  rounds at the cap: %d of %d" % (full, n))
    print("  final: ante %d, $%d\n" % (game.ante, game.money))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("recordings", nargs="*")
    ap.add_argument("--policy", help="comma-separated seeds to play instead")
    ap.add_argument("--deck", default="Red Deck")
    ap.add_argument("--stake", type=int, default=1)
    ap.add_argument("--quiet", action="store_true",
                    help="totals only, no per-round table")
    args = ap.parse_args()
    for path in args.recordings:
        ledger, game, done, total = run_recording(path)
        label = "%s (%d/%d actions)" % (os.path.basename(path), done, total)
        report(label, ledger, game, quiet=args.quiet)
    for seed in (args.policy or "").split(","):
        if not seed:
            continue
        ledger, game, done, _ = run_policy(seed, args.deck, args.stake)
        report("policy %s (%d decisions)" % (seed, done), ledger, game,
               quiet=args.quiet)


if __name__ == "__main__":
    main()
