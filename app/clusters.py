"""Probable common-operator groups for rated trading addresses.

Union-find over four evidence rules. A group is a probability, never a proven
owner: every edge carries its public on-chain evidence for the UI to show.
"""
import json
import time
from collections import defaultdict

from .config import ZERO

WGNK_FUND_RAW = 1000 * 10**9     # Direct WGNK transfer that links two addresses.
STABLE_FUND_RAW = 1000 * 10**6   # Stable funding that makes a funder relevant.
CACHE_SECONDS = 300

BRIDGE = "bridge"
WGNK_FUNDING = "funding"
STABLE_FUNDING = "stable_funder"
EXECUTOR = "executor"
# Type labels for the UI.
LABELS = {BRIDGE: "общий мост", WGNK_FUNDING: "перевод WGNK",
          STABLE_FUNDING: "общий финансист", EXECUTOR: "общий исполнитель"}


def _powder_rows(db, sql, args=()):
    """Powder tables may not exist yet in older databases; degrade to no edges."""
    try:
        return db.conn.execute(sql, args).fetchall()
    except Exception:
        return []


class Union:
    def __init__(self):
        self.parent = {}
        self.evidence = []

    def find(self, a):
        self.parent.setdefault(a, a)
        root = a
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[a] != root:
            self.parent[a], a = root, self.parent[a]
        return root

    def union(self, a, b, kind, detail):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[rb] = ra
        self.evidence.append({"type": kind, "detail": detail, "addresses": sorted((a, b))})
        return True


def _bridge_edges(db):
    """eth addresses sharing one verified gonka counterparty."""
    groups = defaultdict(set)
    for table in ("gonka_mint_links", "gonka_burn_links"):
        for row in db.conn.execute(f"SELECT gnk_address,value FROM {table}"):
            link = json.loads(row[-1])
            if link.get("eth_address"):
                groups[(table, row["gnk_address"])].add(link["eth_address"].lower())
    return groups


def _stable_edges(db, pools):
    """Funders feeding several tracked addresses; same-transaction pool payers."""
    by_funder = defaultdict(set)
    for r in _powder_rows(db, "SELECT src,dst,CAST(amount_raw AS INTEGER) a FROM powder_transfers"):
        if r["a"] >= STABLE_FUND_RAW and r["src"] != ZERO and r["src"] not in pools:
            by_funder[r["src"]].add(r["dst"])
    # Payer -> attributed participants it paid for inside one transaction.
    tx_payers = defaultdict(set)
    placeholders = ",".join("?" for _ in pools)
    for r in _powder_rows(db, f"SELECT tx_hash,src FROM powder_transfers WHERE dst IN ({placeholders}) "
                             "AND CAST(amount_raw AS INTEGER)>=?", (*pools, STABLE_FUND_RAW)):
        tx_payers[r["tx_hash"]].add(r["src"])
    payer_for = defaultdict(set)
    if tx_payers:
        rows = db.conn.execute("SELECT tx_hash,actor,meta FROM events "
                               "WHERE chain='ethereum' AND finalized=1 AND kind IN ('buy','sell')")
        for r in rows:
            payers = tx_payers.get(r["tx_hash"], ())
            if not payers:
                continue
            meta = json.loads(r["meta"])
            if meta.get("attribution") not in ("tx_net", "initiator_net"):
                continue
            for payer in payers:
                if payer != r["actor"]:
                    payer_for[payer].add(r["actor"])
    return by_funder, payer_for


def compute(db, addresses, pools=()):
    """Groups among `addresses`; singletons are omitted from the result."""
    addresses = set(addresses)
    if len(addresses) < 2:
        return {"groups": [], "address_group": {}}
    pools = {p.lower() for p in pools}
    union = Union()
    for (_, gnk), members in _bridge_edges(db).items():
        rated = sorted(members & addresses)
        for other in rated[1:]:
            union.union(rated[0], other, BRIDGE, f"общий gonka-адрес {gnk}")
    for r in db.conn.execute(
            "SELECT src,dst,CAST(amount_raw AS INTEGER) a FROM events "
            "WHERE chain='ethereum' AND finalized=1 AND kind='transfer' AND CAST(amount_raw AS INTEGER)>=?",
            (WGNK_FUND_RAW,)):
        src, dst = r["src"], r["dst"]
        if src in addresses and dst in addresses and src != dst:
            union.union(src, dst, WGNK_FUNDING,
                        f"перевод WGNK {r['a'] / 10**9:.0f} между адресами")
    by_funder, payer_for = _stable_edges(db, pools)
    for funder, targets in by_funder.items():
        if funder in addresses:
            continue
        rated = sorted(targets & addresses)
        for other in rated[1:]:
            union.union(rated[0], other, STABLE_FUNDING,
                        f"один финансист {funder[:10]}… переводил стейблкоины обоим адресам")
    # A rated address paying for another rated participant's purchase is strong
    # operator evidence; shared routers pay for everyone and must not merge users.
    for payer, targets in payer_for.items():
        rated_targets = targets & addresses
        if not rated_targets:
            continue
        if payer in addresses:
            for target in rated_targets:
                if target != payer:
                    union.union(payer, target, EXECUTOR,
                                f"адрес {payer[:10]}… оплачивал покупки из тех же транзакций")
        elif len(rated_targets) > 1 and len(targets) <= 2:
            rated = sorted(rated_targets)
            for other in rated[1:]:
                union.union(rated[0], other, EXECUTOR,
                            f"покупки обоих адресов оплачивал один и тот же адрес {payer[:10]}…")
    # Same executor (with own WGNK movement) attributing swaps to two addresses.
    attributions = defaultdict(set)
    for r in db.conn.execute(
            "SELECT meta,actor FROM events WHERE chain='ethereum' AND finalized=1 AND kind IN ('buy','sell')"):
        meta = json.loads(r["meta"])
        initiator = (meta.get("initiator") or "").lower()
        if initiator and initiator != r["actor"] and meta.get("initiator_net_raw", "0") != "0":
            attributions[initiator].add(r["actor"])
    for initiator, targets in attributions.items():
        rated = sorted(targets & addresses)
        for other in rated[1:]:
            union.union(rated[0], other, EXECUTOR,
                        f"один инициатор-исполнитель {initiator[:10]}… с собственным потоком WGNK")
    clusters = defaultdict(list)
    for address in addresses:
        if address in union.parent:
            clusters[union.find(address)].append(address)
    groups, address_group = [], {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        members = sorted(members)
        evidence = [e for e in union.evidence
                    if e["addresses"][0] in members and e["addresses"][1] in members]
        key = "-".join(members)
        for item in evidence:
            item["label"] = LABELS.get(item["type"], item["type"])
        groups.append({"key": key, "addresses": members, "evidence": evidence})
        for address in members:
            address_group[address] = key
    groups.sort(key=lambda g: (-len(g["addresses"]), g["key"]))
    return {"groups": groups, "address_group": address_group}


def cached(db, addresses, pools=()):
    """compute() behind a kv cache keyed by the address set and row counts."""
    fingerprint = (sorted(addresses), sorted(pools),
                   db.conn.execute("SELECT count(*) FROM gonka_mint_links").fetchone()[0],
                   (lambda rows: rows[0][0] if rows else 0)(_powder_rows(db, "SELECT count(*) FROM powder_transfers")),
                   db.conn.execute("SELECT count(*) FROM events WHERE kind='transfer'").fetchone()[0])
    saved = db.get("clusters:cache") or {}
    now = time.time()
    if saved.get("fingerprint") == fingerprint and now - saved.get("checked_at", 0) < CACHE_SECONDS:
        return saved["data"]
    data = compute(db, addresses, pools)
    db.put("clusters:cache", {"fingerprint": fingerprint, "checked_at": now, "data": data})
    return data
