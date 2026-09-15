"""One-time re-attribution of stored swaps using whole-transaction WGNK net flows.

Rows indexed before the tx_net scheme keep their initiator-only labels even though
the archived events already contain every WGNK transfer of the transaction. This
migration replays the same rule as app.codec.parse_eth_log over the stored rows,
so restored seed archives and older databases agree with freshly indexed events.
"""
import json
from collections import defaultdict

SCHEME = 2
RECOMPUTE = ("initiator_only", "tx_net")


def _pools(db):
    pools = {p.get("address") for p in (db.get("flow:snapshot") or {}).get("pools", [])}
    for key in ("flow:pools", "verified_pools"):
        pools |= set((db.get(key) or {}).keys())
    return pools


def _nets(flows):
    nets = defaultdict(int)
    for e in flows:
        quantity = int(e["amount_raw"])
        if e["kind"] == "bridge_mint":
            nets[e["dst"]] += quantity
        elif e["kind"] == "bridge_burn":
            nets[e["src"]] -= quantity
        else:
            nets[e["src"]] -= quantity
            nets[e["dst"]] += quantity
    return nets


def migrate(db):
    """Returns the number of rewritten swap rows; idempotent via a kv marker."""
    if db.get("attribution:scheme") == SCHEME:
        return 0
    from .config import ZERO
    pools = _pools(db)
    swaps = [dict(r) for r in db.conn.execute(
        "SELECT id,tx_hash,kind,actor,pool,meta FROM events "
        "WHERE chain='ethereum' AND finalized=1 AND kind IN ('buy','sell')")]
    flows = defaultdict(list)
    for e in db.conn.execute(
            "SELECT tx_hash,kind,src,dst,amount_raw FROM events "
            "WHERE chain='ethereum' AND finalized=1 AND kind IN ('transfer','bridge_mint','bridge_burn')"):
        flows[e["tx_hash"]].append(e)
    changed = 0
    updates = []
    by_tx = defaultdict(list)
    for row in swaps:
        by_tx[row["tx_hash"]].append(row)
    for tx, group in by_tx.items():
        nets = _nets(flows.get(tx, ()))
        tx_pools = pools | {g["pool"] for g in group}
        for g in group:
            meta = json.loads(g["meta"])
            if meta.get("attribution") not in RECOMPUTE:
                continue
            initiator = meta.get("initiator") or g["actor"]
            if not initiator:
                continue
            net = nets.get(initiator, 0)
            if (g["kind"] == "buy" and net > 0) or (g["kind"] == "sell" and net < 0):
                actor, attribution = initiator, "initiator_net"
                meta.setdefault("initiator_net_raw", str(net))
            else:
                wanted = {a: n for a, n in nets.items()
                          if a != initiator and a not in tx_pools and a != ZERO
                          and ((g["kind"] == "buy" and n > 0) or (g["kind"] == "sell" and n < 0))}
                if wanted:
                    actor = max(sorted(wanted), key=lambda a: (abs(wanted[a]), a))
                    attribution = "tx_net"
                    meta.update(attributed=actor, attributed_net_raw=str(wanted[actor]))
                else:
                    actor, attribution = initiator, "initiator_only"
            if actor != g["actor"] or attribution != meta.get("attribution"):
                meta["attribution"] = attribution
                updates.append((actor, json.dumps(meta, ensure_ascii=False), g["id"]))
    with db.conn:
        db.conn.executemany("UPDATE events SET actor=?, meta=? WHERE id=?", updates)
    changed = len(updates)
    db.put("attribution:scheme", SCHEME)
    return changed
