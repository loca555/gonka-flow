"""Deterministic up/down rules from verified data plus the dry-powder picture.

Every rule, weight and raw value is returned so the UI can show the reasoning.
This is arithmetic over public chain data, not financial advice.
"""
import time
from collections import defaultdict

from .db import tokens
from .powder import aggregate

OPENBROKER = "https://api.openbroker.gonka.gg/v1/chat/completions"
MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"

WEIGHTS = {"powder": .30, "flow": .30, "bridge": .15, "reload": .10, "momentum": .15}


def _price(db):
    row = db.conn.execute(
        "SELECT quote_raw,amount_raw FROM events WHERE chain='ethereum' AND finalized=1 "
        "AND kind IN ('buy','sell') ORDER BY ts DESC,height DESC,idx DESC LIMIT 1").fetchone()
    if not row or int(row["amount_raw"]) == 0:
        return None
    return int(row["quote_raw"]) * 10**21 // (int(row["amount_raw"]) * 10**6)


def _flow_windows(db, now):
    result = {}
    for hours in (24, 168):
        since = now - hours * 3600
        rows = db.conn.execute(
            "SELECT kind, SUM(CAST(quote_raw AS INTEGER)) q FROM events "
            "WHERE chain='ethereum' AND finalized=1 AND kind IN ('buy','sell') AND ts>=? GROUP BY kind",
            (since,)).fetchall()
        buy = sum(r["q"] for r in rows if r["kind"] == "buy")
        sell = sum(r["q"] for r in rows if r["kind"] == "sell")
        result[hours] = (buy, sell)
    return result


def _bridge_windows(db, now):
    result = {}
    for hours in (24, 168):
        since = now - hours * 3600
        rows = db.conn.execute(
            "SELECT kind, SUM(CAST(amount_raw AS INTEGER)) v FROM events "
            "WHERE chain='ethereum' AND finalized=1 AND kind IN ('bridge_mint','bridge_burn') AND ts>=? GROUP BY kind",
            (since,)).fetchall()
        minted = sum(r["v"] for r in rows if r["kind"] == "bridge_mint")
        burned = sum(r["v"] for r in rows if r["kind"] == "bridge_burn")
        result[hours] = (minted, burned)
    return result


def _reload_delta(db, now):
    rows = db.conn.execute(
        "SELECT ts, buy_own_raw, buy_chain_raw FROM powder_snapshots ORDER BY ts DESC LIMIT 120").fetchall()
    if not rows:
        return None
    latest = int(rows[0]["buy_own_raw"]) + int(rows[0]["buy_chain_raw"])
    for row in rows:
        if now - row["ts"] >= 24 * 3600:
            return latest - (int(row["buy_own_raw"]) + int(row["buy_chain_raw"]))
    oldest = rows[-1]
    if rows[0]["ts"] - oldest["ts"] >= 12 * 3600:
        return latest - (int(oldest["buy_own_raw"]) + int(oldest["buy_chain_raw"]))
    return None


def _momentum(db, now):
    row = db.conn.execute(
        "SELECT ts, quote_raw, amount_raw FROM events WHERE chain='ethereum' AND finalized=1 "
        "AND kind IN ('buy','sell') AND ts<=? ORDER BY ts DESC LIMIT 1", (now - 24 * 3600,)).fetchone()
    price = _price(db)
    if not row or price is None or int(row["amount_raw"]) == 0:
        return None
    then = int(row["quote_raw"]) * 10**21 // (int(row["amount_raw"]) * 10**6)
    if then == 0:
        return None
    return (price - then) * 10000 // then  # basis points


def snapshot(db):
    """The full rule set and verdict at the current verified state."""
    from .flows import market_packet
    now = int(time.time())
    packet = market_packet(db)
    snap = packet["snapshot"] if packet else db.get("flow:snapshot")
    result = {"ready": False, "now": now, "snapshot": snap, "rules": [], "verdict": None,
              "weights": WEIGHTS, "price_raw": None, "powder": None,
              "disclaimer": "Арифметика по публичным данным блокчейна. Не финансовый совет."}
    if not snap or not snap.get("ledger_verified"):
        return result
    price = _price(db)
    result["price_raw"] = str(price) if price is not None else None
    powder = aggregate(db)
    result["powder"] = powder
    if price is None:
        return result
    rules = []

    def add(key, name, score, explanation):
        rules.append({"key": key, "name": name, "weight": WEIGHTS[key],
                      "score": round(score, 3),
                      "direction": "up" if score > 0 else "down" if score < 0 else "flat",
                      "explanation": explanation})

    # 1. Dry powder versus seller overhang at the current price.
    if not powder.get("ready"):
        add("powder", "Порох против запасов", 0, "Порох ещё не собран")
    else:
        totals = powder["totals"]
        coverage = powder.get("balance_coverage") or {}
        covered, qualified = coverage.get("covered", 0), coverage.get("qualified", 0)
        powder_raw = int(totals["buy_own_raw"]) + int(totals["buy_chain_raw"])
        overhang = int(totals["sell_wgnk_raw"]) + int(totals["escrow_raw"]) + int(totals["sell_gnk_raw"])
        overhang_raw = overhang * price // 10**15  # 1e9-raw GNK x 1e12-raw price -> 1e6-raw USDT
        if qualified and (covered < 3 or covered * 2 < qualified):
            add("powder", "Порох против запасов", 0,
                f"Балансы держателей обновляются ({covered} из {qualified}) — правило пока не голосует")
        elif not overhang_raw:
            add("powder", "Порох против запасов", 0,
                f"Порох {tokens(powder_raw, 6)} USDT-экв; запасы продавцов не оценены")
        else:
            ratio = powder_raw / overhang_raw
            score = 1 if ratio >= 2 else .5 if ratio >= 1 else -.5 if ratio >= .5 else -1
            add("powder", "Порох против запасов", score,
                f"Порох {tokens(powder_raw, 6)} USDT-экв против запасов ≈{tokens(overhang_raw, 6)} "
                f"USDT-экв (отношение {ratio:.2f})")

    # 2. Net buy pressure in verified swaps.
    flows = _flow_windows(db, now)
    buy, sell = flows[24]
    total = buy + sell
    share = (buy - sell) / total if total else 0
    weekly = flows[168]
    weekly_share = (weekly[0] - weekly[1]) / (weekly[0] + weekly[1]) if weekly[0] + weekly[1] else 0
    add("flow", "Давление покупок 24ч/7д", max(-1, min(1, (share + weekly_share) / .6)),
        f"24ч: покупки {tokens(buy, 6)} против продаж {tokens(sell, 6)} (перевес {share:+.0%}); "
        f"7д перевес {weekly_share:+.0%}")

    # 3. Bridge direction: mints add sell supply, burns are accumulation.
    mints = _bridge_windows(db, now)
    minted, burned = mints[24]
    if minted or burned:
        share = (burned - minted) / (minted + burned)
        add("bridge", "Направление моста 24ч", max(-1, min(1, share / .5)),
            f"Выпущено {tokens(minted)} WGNK, выведено {tokens(burned)} WGNK за 24ч")
    else:
        add("bridge", "Направление моста 24ч", 0, "За 24ч мост не двигался")

    # 4. Dry powder change over the last day.
    delta = _reload_delta(db, now)
    if delta is None:
        add("reload", "Изменение пороха за 24ч", 0, "Снимков пока недостаточно")
    else:
        add("reload", "Изменение пороха за 24ч", .5 if delta > 0 else -.5 if delta < 0 else 0,
            f"Порох покупателей изменился на {tokens(delta, 6)} USDT-экв")

    # 5. Price momentum, capped at ±20%.
    change = _momentum(db, now)
    if change is None:
        add("momentum", "Цена за 24ч", 0, "Нет сделки сутки назад")
    else:
        add("momentum", "Цена за 24ч", max(-1, min(1, change / 2000)),
            f"{change / 100:+.2f}% за 24 часа")

    score = sum(rule["score"] * rule["weight"] for rule in rules)
    verdict = "up" if score >= .15 else "down" if score <= -.15 else "flat"
    labels = {"up": "рост", "down": "падение", "flat": "нейтрально"}
    result.update(ready=True, rules=rules,
                  verdict={"direction": verdict, "label": labels[verdict],
                           "score": round(score, 3)})
    return result


def model_prompt(data):
    """Compact numeric context for an optional external model call."""
    rules = "; ".join(f"{r['name']}={r['score']:+.2f}" for r in data.get("rules") or [])
    verdict = (data.get("verdict") or {})
    powder = (data.get("powder") or {})
    totals = powder.get("totals") or {}
    price = tokens(int(data["price_raw"]), 12) if data.get("price_raw") else "?"
    return (
        "Ты аналитик рынка токена WGNK (Gonka). По нижеприведённым проверенным данным "
        "ответь одной строкой: ВЕРДИКТ: рост или падение или нейтрально — и одно предложение "
        "объяснения на русском.\n"
        f"Цена сейчас: {price} USDT. Правила (оценка от -1 до 1): {rules}. "
        f"Итог правил: {verdict.get('label', '?')} ({verdict.get('score', 0):+.2f}). "
        f"Порох покупателей: собственный {totals.get('buy_own_raw', '?')} и в цепочках "
        f"{totals.get('buy_chain_raw', '?')} (сырые единицы 1e-6). Запасы продавцов WGNK "
        f"{totals.get('sell_wgnk_raw', '?')} (1e-9), GNK {totals.get('sell_gnk_raw', '?')} "
        f"(1e-9), эскроу {totals.get('escrow_raw', '?')} (1e-9).")
