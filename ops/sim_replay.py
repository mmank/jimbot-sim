"""Replay a recording through the Python simulator.

The recordings are the best test data in the project and the simulator has
never been shown one. They are real human runs -- five of them replay against
the engine with no divergence at all -- and every action carries the state the
player was looking at when they chose it. That is hundreds of positions each,
reached by someone actually playing, which is a different distribution from
anything a scripted driver or a policy produces.

    python ops/sim_replay.py recordings/3.json
    python ops/sim_replay.py recordings/5.json --verbose

Card ids are not compared: the simulator numbers its cards its own way and
nothing about the run depends on the numbers matching. What is compared is
what a player would notice -- the money, the chips, the blind, the hands and
discards left, the jokers held and the cards in hand.

Expect it to stop early. Every stop is a thing the simulator does not do yet,
named precisely rather than guessed at.
"""

import argparse
import json
import sys

sys.path.insert(0, "src")

from balatro.game import Action, ActionType, GameState, Phase   # noqa: E402
from balatro.joker_data import JOKER_DATA                       # noqa: E402
from balatro.shop_pool import NAME_BY_CONSUMABLE_KEY            # noqa: E402

KEY_BY_JOKER = {name: key for name, (key, *_r) in JOKER_DATA.items()}
KEY_BY_CONSUMABLE = {name: key
                     for key, name in NAME_BY_CONSUMABLE_KEY.items()}
RANK_CODE = {"Two": "2", "Three": "3", "Four": "4", "Five": "5", "Six": "6",
             "Seven": "7", "Eight": "8", "Nine": "9", "Ten": "10",
             "Jack": "Jack", "Queen": "Queen", "King": "King", "Ace": "Ace"}


def merge_buy_and_use(actions):
    """Fold the shop's buy-and-use click back into one action.

    The button routes through buy_from_shop, which calls use_card itself, so
    recordings made before the recorder knew about it hold two entries for one
    press: a buy, and a use of a card that by then belongs to no area at all
    (`area: "?"`). Nothing settles between them, so the second entry's
    snapshot is the state from before the first -- comparing against it says
    the money was never spent. The engine replayer does the same fold; see
    scripts/record_replay.py.
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
                and (following.get("params") or {}).get("key") == params.get("key"))
        if pair:
            action = dict(action, params=dict(params, buy_and_use=1))
            skip = True
        merged.append(action)
    return merged


def sim_view(game):
    """What the recording's own fingerprint holds, in the same vocabulary."""
    return {
        "dollars": game.money,
        "chips": game.chips_scored,
        "hands_left": game.hands_left,
        "discards_left": game.discards_left,
        "hand_size": len(game.hand),
        "ante": game.ante,
        "jokers": [KEY_BY_JOKER.get(j.name, j.name) for j in game.jokers],
        "consumables": [KEY_BY_CONSUMABLE.get(c.name, c.name)
                        for c in game.consumables],
        "last_hand": game.last_hand,
        "blind": game.blind_name,
        "blind_chips": game.blind_target,
        "round": game.round_number,
    }


# Fields worth comparing, and why the others are not. Card ids are the
# simulator's own numbering. `round` counts differently on the two sides. The
# blind name is compared only while one is being played, since the recording
# reports none between rounds.
# last_hand is the cheapest way to catch the two sides playing different
# cards: the hand ids cannot be compared, since the simulator numbers its own,
# but what the cards *made* is right there in the recording.
# The blind is worth comparing and was not: a run that beats a blind early
# resets its counters, and without the target in view that reads as the
# counters being wrong rather than the round having ended too soon.
# Consumables belong here as much as jokers do, and leaving them out hid a
# real divergence for a while: the two sides held different cards in the
# consumable slots, so "use the first one" used a different card on each side
# and the run carried on looking fine until the effects diverged.
COMPARED = ("dollars", "chips", "hands_left", "discards_left", "hand_size",
            "ante", "jokers", "consumables", "last_hand", "blind",
            "blind_chips", "round")


def differences(recorded, sim):
    out = []
    for field in COMPARED:
        if field not in recorded or recorded[field] is None:
            continue
        want, got = recorded[field], sim.get(field)
        if isinstance(want, list):
            want = list(want)
        if want != got:
            out.append("  %-14s recorded %r\n  %-14s sim      %r"
                       % (field, want, "", got))
    return out


def match_hand_order(game, recorded_ids, deck_index):
    """Put the simulator's hand into the order the recording shows.

    A player can drag cards around, and dragging is not a function call so the
    recorder cannot hook it -- but the order it leaves behind is in every
    snapshot. The engine replay reproduces it by setting the order directly;
    without the same thing here the simulator holds the same cards in a
    different order and plays different ones for the same positions, which is
    how a Two Pair became a Pair.

    The ids line up because the simulator builds its deck in the game's own
    order, so a starting card's place in that deck is the id the game gives
    it. Cards made during a run have no such place and are left where they
    are.
    """
    if not recorded_ids:
        return
    by_id = {}
    made = []                       # cards created during the run
    for card in game.hand:
        index = deck_index.get(id(card))
        if index is None:
            made.append(card)
        else:
            by_id.setdefault(index, []).append(card)

    ordered, leftover = [], list(game.hand)
    for want in recorded_ids:
        pool = by_id.get(want)
        if pool:
            card = pool.pop(0)
        elif made:
            # An id the starting deck does not have belongs to a card the run
            # made -- Certificate's, a Tarot's copy, a card from a Standard
            # pack. Its number cannot be matched, but its *place* can: it is
            # whatever the recording has here that the deck cannot account
            # for, and where it sits decides which cards a position plays.
            card = made.pop(0)
        else:
            continue
        ordered.append(card)
        leftover.remove(card)
    game.hand[:] = ordered + leftover


def match_joker_order(game, recorded_keys):
    """Put the simulator's joker row into the order the recording shows.

    Jokers are dragged as often as cards are, and the order is not cosmetic:
    Blueprint copies the joker to its right, so the same five jokers in a
    different arrangement score differently. Like the hand, the arrangement
    is not a function call and cannot be recorded, but every snapshot carries
    the result.

    Only a reordering is applied. If the two sides hold different jokers that
    is a real divergence and the comparison should see it, so anything that
    does not line up is left where it is.
    """
    if not recorded_keys:
        return
    by_key = {}
    for joker in game.jokers:
        by_key.setdefault(KEY_BY_JOKER.get(joker.name, joker.name),
                          []).append(joker)
    ordered, leftover = [], list(game.jokers)
    for key in recorded_keys:
        pool = by_key.get(key)
        if pool:
            joker = pool.pop(0)
            ordered.append(joker)
            leftover.remove(joker)
    game.jokers[:] = ordered + leftover


def apply(game, action, params, selected):
    """Do to the simulator what the recording says the player did.

    Returns None when it worked, or a reason it could not, which is the
    finding rather than an error.
    """
    name = params.get("key") if isinstance(params, dict) else None
    cards = tuple(i - 1 for i in (params.get("cards") or [])) if params else ()

    if action == "select_blind":
        game.step(Action(ActionType.SELECT_BLIND))
    elif action == "skip_blind":
        game.step(Action(ActionType.SKIP_BLIND))
    elif action == "play_cards_from_highlighted":
        game.step(Action(ActionType.PLAY, cards=cards))
    elif action == "discard_cards_from_highlighted":
        game.step(Action(ActionType.DISCARD, cards=cards))
    elif action == "sort_hand_value":
        game.sort_hand("rank")
    elif action == "sort_hand_suit":
        game.sort_hand("suit")
    elif action == "cash_out":
        pass                       # the simulator cashes out by itself
    elif action == "toggle_shop":
        game.step(Action(ActionType.LEAVE_SHOP))
    elif action == "reroll_shop":
        game.step(Action(ActionType.REROLL))
    elif action == "buy_from_shop":
        return buy(game, params)
    elif action == "sell_card":
        area = params.get("area")
        index = (params.get("index") or 1) - 1
        if area == "jokers":
            game.step(Action(ActionType.SELL_JOKER, index=index))
        elif area == "consumeables":
            game.step(Action(ActionType.SELL_CONSUMABLE, index=index))
        else:
            return "sell from %s is not modelled" % area
    elif action == "use_card":
        area = params.get("area")
        index = (params.get("index") or 1) - 1
        targets = tuple(i - 1 for i in (params.get("targets") or []))
        if area == "consumeables":
            game.step(Action(ActionType.USE_CONSUMABLE, index=index,
                             cards=targets))
        elif area == "shop_booster":
            packs = getattr(game.shop, "packs", None) if game.shop else None
            if not packs or index >= len(packs):
                return ("no booster %d; the simulator's shop has %d"
                        % (index + 1, len(packs or [])))
            game.step(Action(ActionType.BUY_PACK, index=index))
        elif area == "pack_cards":
            game.step(Action(ActionType.PICK_PACK, index=index))
        elif area == "shop_vouchers":
            offered = game.shop.voucher if game.shop else None
            key = getattr(offered, "key", None)
            if key is None:
                return "the simulator's shop offers no voucher"
            if name and key != name:
                return ("the shop offers %s, the recording redeemed %s"
                        % (key, name))
            game.step(Action(ActionType.BUY_VOUCHER))
        else:
            return "using from %s is not modelled" % area
    else:
        return "no mapping for %s (%s)" % (action, name)
    return None


def buy(game, params):
    """Buy what the recording bought, having checked it is the same card.

    The shop is laid out differently on the two sides -- the game has a joker
    row, a voucher slot and a booster row, the simulator one list plus packs --
    so the index has to be translated. And the recording names the card, which
    is worth checking rather than assuming: buying the right slot holding the
    wrong card would look like a purchase and diverge later, somewhere else.
    """
    area, index = params.get("area"), (params.get("index") or 1) - 1
    wanted = params.get("key")
    shop = game.shop
    if shop is None:
        return "the simulator has no shop open"

    if area == "shop_jokers":
        # One press in the game, two things: the card is bought and used
        # without ever reaching a consumable slot. A simulator that only buys
        # it leaves it sitting in a slot the run never had it in.
        if params.get("buy_and_use"):
            if index >= len(shop.slots):
                return "no slot %d to buy and use" % (index + 1)
            game.step(Action(ActionType.BUY, index=index))
            if not game.consumables:
                return "buying %s put nothing in a consumable slot" % wanted
            game.step(Action(ActionType.USE_CONSUMABLE,
                             index=len(game.consumables) - 1))
            return None
        if index >= len(shop.slots):
            return "no slot %d; the simulator's shop has %d" % (index + 1,
                                                                len(shop.slots))
        slot = shop.slots[index]
        # Both sides in the game's vocabulary: the recording names a centre
        # key, the simulator holds an object with a display name.
        holding = (KEY_BY_JOKER.get(slot.joker.name) if slot.joker
                   else KEY_BY_CONSUMABLE.get(
                       getattr(slot.consumable, "name", None)))
        if wanted and holding and wanted != holding:
            return ("shop slot %d holds %s, the recording bought %s"
                    % (index + 1, holding, wanted))
        game.step(Action(ActionType.BUY, index=index))
        return None
    return "buying from %s is not modelled" % area


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("recording")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    payload = json.loads(open(args.recording).read())
    actions = merge_buy_and_use(payload["actions"])
    game = GameState(seed=payload["seed"], deck=payload["deck"])
    if payload.get("money") is not None:
        game.money = payload["money"]

    print("replaying %d actions through the simulator (%s, %s)\n"
          % (len(actions), payload["seed"], payload["deck"]))

    # A starting card's place in the deck is the id the game gives it, and
    # the simulator builds its deck in the same order -- see
    # cards.standard_deck.
    deck_index = {id(card): i for i, card in enumerate(game.full_deck)}

    for i, entry in enumerate(actions, start=1):
        recorded = entry.get("before") or {}
        match_hand_order(game, recorded.get("hand_ids"), deck_index)
        match_joker_order(game, recorded.get("jokers"))

        # Nothing to compare on the cash-out screen: the simulator does not
        # have one. It takes the payout the moment a blind is beaten, so by
        # the time the recording presses the button the simulator has already
        # reset the round -- see GameState._beat_blind and the note in the
        # README about that being deliberate.
        if entry["action"] == "cash_out":
            continue

        problems = differences(recorded, sim_view(game))
        if problems:
            print("step %d, before %s:" % (i, entry["action"]))
            for line in problems:
                print(line)
            print("\nreached step %d of %d" % (i, len(actions)))
            return

        reason = apply(game, entry["action"], entry.get("params") or {}, None)
        if reason is not None:
            print("step %d: %s" % (i, reason))
            print("\nreached step %d of %d" % (i, len(actions)))
            return
        if args.verbose:
            hand = " ".join("%s%s" % (RANK_CODE[c.rank.name.title()],
                                      c.suit.name[0])
                            for c in game.hand)
            print("  %3d %-30s chips %-6d %s"
                  % (i, entry["action"], game.chips_scored, hand))

    print("the simulator followed the whole recording")


if __name__ == "__main__":
    main()
