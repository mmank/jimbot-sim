"""Exercise every player action against the real game and prove it worked.

Each check asserts an observable consequence -- money moved, a level rose, a
card changed, the order changed the score -- rather than that a call returned
without raising. A silent no-op is the failure mode that matters here: the
game's own button callbacks refuse invalid actions quietly.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from balatro_headless.run import HeadlessRun  # noqa: E402
from balatro_headless.runtime import HeadlessBalatro  # noqa: E402

SEED = "ABCDEFGH"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str):
    def decorator(fn):
        def wrapper():
            try:
                detail = fn() or ""
                RESULTS.append((name, True, str(detail)))
            except AssertionError as exc:
                RESULTS.append((name, False, f"assertion: {exc}"))
            except Exception as exc:  # noqa: BLE001
                line = str(exc).splitlines()[0][-90:]
                RESULTS.append((name, False, f"{type(exc).__name__}: {line}"))
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


def fresh(jokers=(), seed=SEED, select=True) -> HeadlessRun:
    run = HeadlessRun(seed=seed, game=HeadlessBalatro().boot())
    run.start()
    for joker in jokers:
        run.game.execute(f'add_joker("{joker}")')
    if select:
        run.select_blind()
    return run


def to_shop(run: HeadlessRun) -> HeadlessRun:
    """Beat the current blind and cash out into a stocked shop."""
    for _ in range(6):
        if run.snapshot()["state_name"] != "SELECTING_HAND":
            break
        best, _ = run.best_play()
        run.play(best or [1])
    run.pump(200)
    if run.snapshot()["state_name"] == "ROUND_EVAL":
        run.cash_out()
    return run


def grant(run: HeadlessRun, key: str, area: str = "consumeables") -> None:
    """Put a specific card straight into an area, so consumable behaviour can be
    tested deterministically instead of waiting for the right shop roll.

    create_card wants the card's own set ("Tarot", "Planet"), not a generic
    type -- passing the wrong one yields a card with no center.
    """
    card_set = run.game.eval(f"G.P_CENTERS['{key}'].set")
    run.game.execute(f'''
        local card = create_card('{card_set}', G.{area}, nil, nil, nil, nil, '{key}')
        card:add_to_deck()
        G.{area}:emplace(card)
    ''')
    run.pump(30)


# ---------------------------------------------------------------- playing

@check("select blind deals a hand")
def t_select():
    run = fresh()
    s = run.snapshot()
    assert s["state_name"] == "SELECTING_HAND", s["state_name"]
    assert s["hand_size"] == 8, s["hand_size"]
    return f"{s['blind_name']}, need {s['blind_chips']}, 8 cards"


@check("play scores chips")
def t_play():
    run = fresh()
    before = run.snapshot()["chips"]
    scored = run.play([2, 3, 4, 5])
    assert scored > before, f"{before} -> {scored}"
    return f"Two Pair scored {scored}"


@check("discard replaces cards")
def t_discard():
    run = fresh()
    before_ids = [c["id"] for c in run.hand()][:3]
    before_discards = run.snapshot()["discards_left"]
    run.discard([1, 2, 3])
    after = run.snapshot()
    assert after["discards_left"] == before_discards - 1
    assert after["hand_size"] == 8, after["hand_size"]
    now_ids = [c["id"] for c in run.hand()]
    return f"discards {before_discards}->{after['discards_left']}, hand refilled to 8"


@check("sort hand by value and by suit")
def t_sort():
    run = fresh()
    before = [f"{c['rank']}{c['suit'][:1]}" for c in run.hand()]
    run.sort_hand("value")
    by_value = [f"{c['rank']}{c['suit'][:1]}" for c in run.hand()]
    run.sort_hand("suit")
    by_suit = [f"{c['rank']}{c['suit'][:1]}" for c in run.hand()]
    assert by_value != by_suit, "sorting by value and suit gave the same order"
    assert sorted(by_value) == sorted(by_suit) == sorted(before), "sort lost cards"
    return f"value {' '.join(by_value[:4])}... | suit {' '.join(by_suit[:4])}..."


# ---------------------------------------------------------------- ordering

@check("reorder jokers changes their order")
def t_reorder():
    run = fresh(("j_joker", "j_duo", "j_baseball"))
    before = [j["key"] for j in run._lua("api.jokers()").values()]
    run.move_joker(1, 3)
    after = [j["key"] for j in run._lua("api.jokers()").values()]
    assert after == before[1:] + before[:1], f"{before} -> {after}"
    run.reorder_jokers([3, 1, 2])
    back = [j["key"] for j in run._lua("api.jokers()").values()]
    assert back == before, f"reorder did not restore: {back}"
    return f"{before} -> {after} -> restored"


@check("joker order changes the score")
def t_order_matters():
    # +4 Mult then X2 is (2+4)*2; X2 then +4 is (2*2)+4. Same jokers.
    a = fresh(("j_joker", "j_duo")).play([2, 3, 4, 5])
    b = fresh(("j_duo", "j_joker")).play([2, 3, 4, 5])
    assert a != b, f"order made no difference: {a} vs {b}"

    # And reordering at runtime must produce the same score as buying in order.
    run = fresh(("j_duo", "j_joker"))
    run.reorder_jokers([2, 1])
    c = run.play([2, 3, 4, 5])
    assert c == a, f"reordered to joker,duo expected {a}, got {c}"
    return f"joker,duo={a}  duo,joker={b}  reordered={c}"


# ---------------------------------------------------------------- consumables

@check("use planet from consumable area levels a hand")
def t_planet():
    run = fresh()
    grant(run, "c_mercury")  # Mercury levels Pair
    before = run.game.eval("G.GAME.hands['Pair'].level")
    held = run.consumables()
    assert held, "planet was not granted"
    run.use_consumable(held[0]["index"])
    after = run.game.eval("G.GAME.hands['Pair'].level")
    assert after == before + 1, f"Pair level {before} -> {after}"
    assert not run.consumables(), "planet was not consumed"
    return f"Mercury: Pair level {before} -> {after}"


@check("use targeted tarot enhances the selected card")
def t_tarot():
    run = fresh()
    grant(run, "c_heirophant")  # the game's own spelling; Bonus cards
    held = run.consumables()
    assert held, "tarot was not granted"
    before = run.hand()[0]["enhancement"]
    run.use_consumable(held[0]["index"], cards=[1, 2])
    after = run.hand()[0]["enhancement"]
    assert after != before, f"card unchanged ({before})"
    assert after == "m_bonus", f"expected m_bonus, got {after}"
    return f"The Hierophant: card 1 {before} -> {after}"


@check("planet level actually raises the score")
def t_planet_scores():
    plain = fresh().play([2, 3, 4, 5])
    run = fresh()
    grant(run, "c_uranus")  # Uranus levels Two Pair
    run.use_consumable(run.consumables()[0]["index"])
    levelled = run.play([2, 3, 4, 5])
    assert levelled > plain, f"{plain} -> {levelled}"
    return f"Two Pair {plain} -> {levelled} after one Uranus"


# ---------------------------------------------------------------- shop

@check("buy a joker")
def t_buy_joker():
    run = to_shop(fresh(("j_baseball", "j_duo", "j_trio")))
    items = run.shop_contents()
    joker = next((i for i in items if i["set"] == "Joker" and i["buyable"]), None)
    if joker is None:
        return "SKIPPED: no affordable joker in this shop"
    money, count = run.snapshot()["dollars"], run.snapshot()["joker_count"]
    run.buy(joker["area"], joker["index"])
    after = run.snapshot()
    assert after["dollars"] == money - joker["cost"], f"${money} -> ${after['dollars']}"
    assert after["joker_count"] == count + 1
    return f"{joker['key']} for ${joker['cost']}, ${money} -> ${after['dollars']}"


@check("buy a consumable from the shop")
def t_buy_consumable():
    run = to_shop(fresh(("j_baseball", "j_duo", "j_trio")))
    for _ in range(6):
        item = next((i for i in run.shop_contents()
                     if i["set"] in ("Planet", "Tarot", "Spectral") and i["buyable"]),
                    None)
        if item:
            money = run.snapshot()["dollars"]
            held = run.snapshot()["consumable_count"]
            run.buy(item["area"], item["index"])
            after = run.snapshot()
            assert after["dollars"] == money - item["cost"]
            assert after["consumable_count"] == held + 1
            return f"{item['key']} for ${item['cost']}"
        if not run._lua("api.can_reroll()"):
            break
        run.reroll()
    return "SKIPPED: no affordable consumable after rerolls"


@check("reroll costs money and changes the shop")
def t_reroll():
    run = to_shop(fresh(("j_baseball", "j_duo", "j_trio")))
    run.game.execute("ease_dollars(20, true)")
    before_items = [i["key"] for i in run.shop_contents()]
    money = run.snapshot()["dollars"]
    cost = run.snapshot()["reroll_cost"]
    run.reroll()
    after = run.snapshot()
    after_items = [i["key"] for i in run.shop_contents()]
    assert after["dollars"] == money - cost, f"${money} - {cost} != ${after['dollars']}"
    assert after_items != before_items, "shop contents unchanged after reroll"
    return f"${cost} reroll: {before_items[:2]} -> {after_items[:2]}"


@check("buy a voucher and have it register")
def t_voucher():
    run = to_shop(fresh(("j_baseball", "j_duo", "j_trio")))
    run.game.execute("ease_dollars(30, true)")
    items = run.shop_contents()
    voucher = next((i for i in items if i["set"] == "Voucher"), None)
    if voucher is None:
        return "SKIPPED: no voucher in this shop"
    money = run.snapshot()["dollars"]
    run.buy(voucher["area"], voucher["index"])
    used = run.game.eval(f"G.GAME.used_vouchers['{voucher['key']}'] and true or false")
    assert used, f"{voucher['key']} not registered in used_vouchers"
    assert run.snapshot()["dollars"] == money - voucher["cost"]
    return f"{voucher['key']} redeemed for ${voucher['cost']}"


@check("buy a booster pack, open it and take a card")
def t_pack():
    run = to_shop(fresh(("j_baseball", "j_duo", "j_trio")))
    run.game.execute("ease_dollars(30, true)")
    pack = next((i for i in run.shop_contents() if i["set"] == "Booster"), None)
    if pack is None:
        return "SKIPPED: no booster pack in this shop"
    run.buy(pack["area"], pack["index"])
    run.pump(200)
    state = run.snapshot()["state_name"]
    assert state.endswith("_PACK"), f"buying a pack left state {state}"
    contents = run.pack_contents()
    assert contents, "pack opened empty"
    before = run.snapshot()
    run.pick_pack(contents[0]["index"])
    run.pump(120)
    after = run.snapshot()
    gained = (after["consumable_count"] > before["consumable_count"]
              or after["joker_count"] > before["joker_count"]
              or run.game.eval("#G.playing_cards") > 52
              or after["state_name"] != state)
    assert gained, "picking from the pack changed nothing"
    return f"{pack['key']} -> {len(contents)} options, took {contents[0]['key']}"


@check("leave shop returns to blind select")
def t_leave():
    run = to_shop(fresh(("j_baseball", "j_duo", "j_trio")))
    run.leave_shop()
    s = run.snapshot()
    assert s["state_name"] == "BLIND_SELECT", s["state_name"]
    assert s["blind_on_deck"] == "Big", s["blind_on_deck"]
    return "back at blind select, Big Blind on deck"


# ---------------------------------------------------------------- selling

@check("sell a joker")
def t_sell_joker():
    run = fresh(("j_joker", "j_duo"))
    jokers = run._lua("api.jokers()")
    first = dict(jokers[1])
    money = run.snapshot()["dollars"]
    run.sell("jokers", 1)
    after = run.snapshot()
    assert after["dollars"] == money + first["sell_cost"], \
        f"${money} + {first['sell_cost']} != ${after['dollars']}"
    assert after["joker_count"] == 1
    return f"sold {first['key']} for ${first['sell_cost']}"


@check("sell a consumable")
def t_sell_consumable():
    run = fresh()
    grant(run, "c_mercury")
    held = run.consumables()[0]
    money = run.snapshot()["dollars"]
    run.sell("consumeables", held["index"])
    after = run.snapshot()
    assert after["consumable_count"] == 0, "consumable still held"
    assert after["dollars"] == money + held["sell_cost"], \
        f"${money} + {held['sell_cost']} != ${after['dollars']}"
    return f"sold {held['key']} for ${held['sell_cost']}"


@check("eternal jokers cannot be sold")
def t_eternal():
    run = fresh(("j_joker", "j_duo"))
    run.game.execute("G.jokers.cards[2].ability.eternal = true")
    jokers = [dict(j) for j in run._lua("api.jokers()").values()]
    assert jokers[0]["sellable"] is True, "normal joker reported unsellable"
    assert jokers[1]["sellable"] is False, "eternal joker reported sellable"
    assert run.can_sell("jokers", 1) is True
    assert run.can_sell("jokers", 2) is False
    money = run.snapshot()["dollars"]
    try:
        run.sell("jokers", 2)
        raise AssertionError("selling an eternal joker was allowed")
    except Exception as exc:
        if "cannot sell" not in str(exc):
            raise
    assert run.snapshot()["dollars"] == money, "money moved on a refused sale"
    assert run.snapshot()["joker_count"] == 2, "eternal joker was removed"
    return "refused, joker kept and money unchanged"


# ---------------------------------------------------------------- blinds

@check("skip a blind and take the tag")
def t_skip():
    run = fresh(select=False)
    s = run.snapshot()
    assert s["skippable"], "small blind reported unskippable"
    offered = s["offered_tag"]
    assert offered, "no tag offered"
    before_tags = run.game.eval("#G.GAME.tags")
    run.skip_blind()
    after = run.snapshot()
    got = run.game.eval("#G.GAME.tags")
    assert got == before_tags + 1, f"tags {before_tags} -> {got}"
    assert after["blind_on_deck"] == "Big", after["blind_on_deck"]
    key = run.game.eval("G.GAME.tags[1].key")
    assert key == offered, f"offered {offered} but received {key}"
    return f"skipped Small, took {key}, Big now on deck"


@check("boss blind cannot be skipped")
def t_no_skip_boss():
    run = fresh(select=False)
    run.skip_blind()
    run.skip_blind()
    s = run.snapshot()
    assert s["blind_on_deck"] == "Boss", s["blind_on_deck"]
    assert not s["skippable"], "boss blind reported skippable"
    return "Boss on deck and correctly unskippable"


@check("cash out pays the blind reward")
def t_cash_out():
    run = fresh(("j_baseball", "j_duo", "j_trio"))
    for _ in range(6):
        if run.snapshot()["state_name"] != "SELECTING_HAND":
            break
        best, _ = run.best_play()
        run.play(best or [1])
    run.pump(200)
    before = run.snapshot()["dollars"]
    owed = run.game.eval("G.GAME.current_round.dollars")
    after = run.cash_out()
    assert after == before + owed, f"${before} + {owed} != ${after}"
    assert run.snapshot()["state_name"] == "SHOP"
    return f"paid ${owed}: ${before} -> ${after}"


CHECKS = [t_select, t_play, t_discard, t_sort, t_reorder, t_order_matters,
          t_planet, t_tarot, t_planet_scores, t_buy_joker, t_buy_consumable,
          t_reroll, t_voucher, t_pack, t_leave, t_sell_joker, t_sell_consumable,
          t_eternal, t_skip, t_no_skip_boss, t_cash_out]


def main() -> None:
    for fn in CHECKS:
        fn()
    width = max(len(name) for name, _, _ in RESULTS)
    failed = skipped = 0
    for name, ok, detail in RESULTS:
        if detail.startswith("SKIPPED"):
            mark, skipped = "SKIP", skipped + 1
        elif ok:
            mark = "PASS"
        else:
            mark, failed = "FAIL", failed + 1
        print(f"  [{mark}] {name:<{width}}  {detail}")
    passed = len(RESULTS) - failed - skipped
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
