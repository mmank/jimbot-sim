"""Replay a recorded human game on any backend: the simulator, headless, the game.

A recording (`recordings/*.json`, made by scripts/record_replay.py) is the
player's actions, each with the state they were looking at when they chose
it -- the recorder's own snapshot, bot_api's `check`. Replaying one is a
step-by-step check: before each action the backend should show what the
player saw, and then it is told to do what the player did.

There were two replayers and they shared nothing. `ops/sim_replay.py` turned a
recorded action into a simulator `Action`; `scripts/record_replay.py` turned
it into the bridge client's calls, with its own waits and comparison. A wait
added to one did not reach the other -- SUPMAN01's Hallucination Tarot was a
false mismatch in the engine replay after the live run had learned to wait
for it. So this is the replayer, on `jimbot_sim.run`: one translation
(`to_move`), one comparison (`differences`), and the backend does the rest.

What a player does that is not an `Action` -- dragging cards or jokers into an
order, pressing a sort button -- the backend does directly: dragging is not a
function call and cannot be recorded, but every snapshot carries the order it
left, and the backend is put into that order before each step (`arrange`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .compare import CARD_KEYS
from .game import Action, ActionType

# Fields worth comparing. Money, jokers and round score are the ones that
# catch real divergence; hand_size and deck_size catch bookkeeping drift. A
# field is compared only where the recording has it and the backend reports
# it: a recording made before a field existed is not a divergence, and the
# simulator's snapshot is a subset (it numbers its cards its own way, so the
# ids are not in it).
COMPARED = ("phase", "dollars", "chips", "ante", "round", "hands_left",
            "discards_left", "blind", "blind_chips", "hand_size", "jokers",
            "consumables", "hand_levels", "deck_size", "hands_played",
            "last_hand", "hand_ids", "tags", "joker_ids")

# Phases where the round score is a settled number rather than mid-animation.
# cash_out resets it with ease_chips(0) over several frames, so between the
# cash-out and the next blind its value depends on exactly when the recorder
# sampled -- a race, not a divergence. Compared exactly everywhere else.
SCORE_STABLE_PHASES = {"SELECTING_HAND", "HAND_PLAYED", "ROUND_EVAL"}

# A card's id is its place in the run's card counter, and that counter moves
# for reasons a recording cannot capture: opening the deck collection screen
# builds fifty-two Card objects to fan out behind the deck art, and the
# recorder hooks game actions, not looking at a menu. The engine never builds
# that screen, so from the first time a player opens one, every card made
# afterwards is numbered differently while being the same card in the same
# place. What the numbers still carry is their order, so each list is ranked
# against itself -- smallest 0, next 1 -- which throws away the offset and
# keeps the order. [68, 53, 54, 136] and [68, 53, 54, 83] both rank to
# [2, 0, 1, 3], and a card genuinely out of place still moves a rank.
ID_FIELDS = ("hand_ids", "joker_ids")


def normalise(value):
    """Lua tables arrive as dicts keyed 1..n; compare them as lists."""
    if isinstance(value, dict):
        if not value:
            return []
        if all(isinstance(k, int) for k in value):
            return [normalise(value[k]) for k in sorted(value)]
        return {k: normalise(v) for k, v in sorted(value.items())}
    return value


def ranked(ids):
    """Replace each id by its position in the sorted list of ids present."""
    if not isinstance(ids, (list, tuple)):
        return ids
    if not all(isinstance(i, int) for i in ids):
        return ids
    rank = {value: place for place, value in enumerate(sorted(ids))}
    return [rank[i] for i in ids]


def differences(recorded: dict, snapshot: dict) -> list[str]:
    """Where a backend's snapshot differs from what the player saw."""
    out = []
    scoring = recorded.get("phase") in SCORE_STABLE_PHASES
    for name in COMPARED:
        if name == "chips" and "phase" in recorded and not scoring:
            continue
        if name not in recorded or recorded[name] is None:
            continue
        if name not in snapshot:
            continue
        want, got = normalise(recorded.get(name)), normalise(snapshot.get(name))
        if name in ID_FIELDS:
            want, got = ranked(want), ranked(got)
        if want != got:
            out.append("%s: recorded %r but replayed %r" % (name, want, got))
    return out


# ----------------------------------------------------------------------
# the recording
# ----------------------------------------------------------------------

def merge_buy_and_use(actions: list[dict]) -> list[dict]:
    """Fold the shop's buy-and-use click back into one action.

    The button routes through buy_from_shop, which calls use_card itself, so
    recordings made before the recorder knew about it hold two entries for
    one press: a buy, and a use of a card that by then belongs to no area at
    all (`area: "?"`). Nothing settles between them, so the second entry's
    snapshot is the state from before the first -- comparing against it says
    the money was never spent -- and replaying both buys the card into the
    consumable slots and then cannot find it to use. Newer recordings mark
    the buy itself with `buy_and_use`; either way it becomes one buy with
    that flag.
    """
    merged, skip = [], False
    for i, action in enumerate(actions):
        if skip:
            skip = False
            continue
        params = action.get("params") or {}
        following = actions[i + 1] if i + 1 < len(actions) else None
        pair = (action.get("action") == "buy_from_shop" and following
                and following.get("action") == "use_card"
                and (following.get("params") or {}).get("area") == "?"
                and (following.get("params") or {}).get("key")
                == params.get("key"))
        if action.get("action") == "buy_and_use" or pair:
            action = dict(action, action="buy_from_shop",
                          params=dict(params, buy_and_use=1))
            skip = bool(pair)
        merged.append(action)
    return merged


@dataclass
class Recording:
    seed: str
    deck: str
    stake: int
    money: int | None
    actions: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path) -> "Recording":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(seed=payload["seed"], deck=payload["deck"],
                   stake=payload.get("stake") or 1,
                   money=payload.get("money"),
                   actions=merge_buy_and_use(payload["actions"]))


# ----------------------------------------------------------------------
# a recorded action, as a move
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    """What to do for one recorded action.

    Exactly one of: an `Action` for the backend to step; a hand sort by
    `sort` ("rank" or "suit"), which is a button and not an Action; or
    `skip`, an action the game made itself (a pack tag using its own pack)
    that the backend repeats unprompted.
    """
    action: Action | None = None
    sort: str | None = None
    skip: bool = False


def _shelf(state, area: str) -> list[dict]:
    if callable(state):
        state = state()
    return [row for row in state.get("shop") or []
            if row.get("area") == area]


def _recorded_key(key) -> str:
    """The recording names a playing card by its centre -- c_base for a plain
    one, m_glass and the rest for an enhanced one -- where a state row says
    only that it is a card."""
    return "card" if key in CARD_KEYS else key


def to_move(entry: dict, state: dict, key) -> Move | str:
    """The recorded action as a `Move`, or the reason it has none.

    `state` is the backend's, in the engine's shape, already arranged in the
    recorded order -- or a callable returning it, read only for a purchase,
    which spares building a whole state for every card played. `key(row)`
    names a state row's centre in that backend's
    numbering. The recording names what it bought and redeemed, which is
    checked rather than assumed: buying the right slot holding the wrong card
    would look like a purchase and diverge later, somewhere else.
    """
    action = entry.get("action")
    params = normalise(entry.get("params")) or {}
    name = params.get("key")
    cards = tuple(i - 1 for i in (params.get("cards") or []))
    targets = tuple(i - 1 for i in (params.get("targets") or []))
    index = (params.get("index") or 1) - 1

    simple = {"select_blind": ActionType.SELECT_BLIND,
              "skip_blind": ActionType.SKIP_BLIND,
              "cash_out": ActionType.CASH_OUT,
              "reroll_boss": ActionType.REROLL_BOSS,
              "skip_booster": ActionType.SKIP_PACK,
              "toggle_shop": ActionType.LEAVE_SHOP,
              "reroll_shop": ActionType.REROLL}
    if action in simple:
        return Move(Action(simple[action]))
    if action == "play_cards_from_highlighted":
        return Move(Action(ActionType.PLAY, cards=cards))
    if action == "discard_cards_from_highlighted":
        return Move(Action(ActionType.DISCARD, cards=cards))
    if action == "sort_hand_value":
        return Move(sort="rank")
    if action == "sort_hand_suit":
        return Move(sort="suit")
    if action == "sell_card":
        area = params.get("area")
        if area == "jokers":
            return Move(Action(ActionType.SELL_JOKER, index=index))
        if area == "consumeables":
            return Move(Action(ActionType.SELL_CONSUMABLE, index=index))
        return "sell from %s is not modelled" % area
    if action == "buy_from_shop":
        area = params.get("area")
        if area != "shop_jokers":
            return "buying from %s is not modelled" % area
        shelf = _shelf(state, "shop_jokers")
        if index >= len(shelf):
            return "no slot %d; the shop has %d" % (index + 1, len(shelf))
        holding = key(shelf[index])
        if name and holding != _recorded_key(name):
            return ("shop slot %d holds %s, the recording bought %s"
                    % (index + 1, holding, name))
        if params.get("buy_and_use"):
            return Move(Action(ActionType.BUY_AND_USE, index=index,
                               cards=targets))
        return Move(Action(ActionType.BUY, index=index))
    if action == "use_card":
        area = params.get("area")
        if area == "consumeables":
            return Move(Action(ActionType.USE_CONSUMABLE, index=index,
                               cards=targets))
        if area == "pack_cards":
            # A consumable taken from a pack is used on the spot, so it needs
            # the cards it was used on. Dropping them meant The Chariot made
            # nothing steel and every hand after it scored a third short.
            return Move(Action(ActionType.PICK_PACK, index=index,
                               cards=targets))
        if area == "shop_booster":
            packs = _shelf(state, "shop_booster")
            if index >= len(packs):
                return ("no booster %d; the shop has %d"
                        % (index + 1, len(packs)))
            return Move(Action(ActionType.BUY_PACK, index=index))
        if area == "shop_vouchers":
            # A Voucher Tag puts a second voucher beside the round's own, so
            # match on the key the recording redeemed rather than assuming
            # the first slot.
            offered = [key(row) for row in _shelf(state, "shop_vouchers")]
            if not offered:
                return "the shop offers no voucher"
            index = 0
            if name:
                if name not in offered:
                    return ("the shop offers %s, the recording redeemed %s"
                            % ("/".join(offered), name))
                index = offered.index(name)
            return Move(Action(ActionType.BUY_VOUCHER, index=index))
        if area == "?":
            # The game did this itself, not the player. A pack tag creates its
            # pack card with from_tag set and calls use_card on it without
            # ever putting it in an area, so the recorder has nowhere to name.
            # Every backend opens that pack when the tag fires, so replaying
            # this would open it twice.
            if str(name or "").startswith("p_"):
                return Move(skip=True)
            return "a use of %s from no area at all" % name
        return "using from %s is not modelled" % area
    return "no mapping for %s (%s)" % (action, name)


# ----------------------------------------------------------------------
# the replay
# ----------------------------------------------------------------------

@dataclass
class Result:
    reached: int                      # steps that matched and were done
    total: int
    problem: str | None = None        # why it stopped, if it did
    mismatches: list = field(default_factory=list)   # (step, lines)


def _games_own(entry: dict) -> bool:
    """A use the game made of a pack it created for a tag. See `to_move`."""
    params = normalise(entry.get("params")) or {}
    return (entry.get("action") == "use_card" and params.get("area") == "?"
            and str(params.get("key") or "").startswith("p_"))


def replay(run, recording: Recording, *, keep_going: bool = False,
           check: bool = True, stop_at: int | None = None,
           report=None, settle: float = 5.0) -> Result:
    """Follow `recording` on `run`, comparing before every step.

    Stops at the first difference unless `keep_going`; stops regardless at
    an action that cannot be carried out, since past it the backend is no
    longer on the recorded run. `check=False` only follows, for a caller
    that wants the position a recording reaches (ops/fork_differential.py);
    `stop_at` stops after that many steps. `report(step, entry, snapshot,
    problems)` is called before each compared action.

    A difference is re-read until the backend settles, for up to `settle`
    seconds, before it is believed: the player looked at a finished screen,
    and the previous action can return with the game still paying out,
    dealing, or making a card (see `jimbot_sim.run.EngineRun.settle`).
    """
    run.start(recording.seed, recording.deck, recording.stake)
    # Reapply whatever bankroll the recording was made with, before comparing
    # anything -- otherwise every step diverges on dollars.
    if recording.money is not None:
        run.set_money(recording.money)

    actions = recording.actions
    if stop_at is not None:
        actions = actions[:stop_at]
    result = Result(reached=0, total=len(actions))
    for step, entry in enumerate(actions, start=1):
        recorded = normalise(entry.get("before")) or {}
        run.arrange(hand_ids=recorded.get("hand_ids"),
                    joker_ids=recorded.get("joker_ids"),
                    joker_keys=recorded.get("jokers"))
        if _games_own(entry):
            # Its snapshot was taken as the tag fired, and every backend has
            # already opened the pack on its own.
            result.reached = step
            continue
        if check:
            run.settle(lambda s: not differences(recorded, s),
                       timeout=settle, read=run.fingerprint)
            snapshot = run.fingerprint()
            problems = differences(recorded, snapshot)
            if report is not None:
                report(step, entry, snapshot, problems)
            if problems:
                result.mismatches.append((step, problems))
                if not keep_going:
                    result.problem = "\n".join(problems)
                    return result
        else:
            # Not compared, but still let the backend finish the last action
            # before the next is attempted.
            run.settle(lambda s: True, timeout=0.0)

        move = to_move(entry, run.state, run.key)
        if isinstance(move, str):
            result.problem = move
            return result
        try:
            if move.sort:
                run.sort_hand(move.sort)
            elif move.action is not None:
                run.step(move.action)
        except Exception as error:                     # noqa: BLE001
            result.problem = "%s failed: %s" % (entry.get("action"), error)
            return result
        result.reached = step
    return result
