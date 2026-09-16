"""Targeted stablecoin "dry powder" tracking for known GNK market participants.

Only public chain data: USDT/USDC transfers and balances of addresses that
actually traded WGNK, plus their stablecoin funders. Public exchange hot-wallet
labels exclude exchange money from the totals. Nothing identifies people.
"""
import asyncio
import json
import time
from collections import defaultdict

from .config import ZERO, USDT
from .db import tokens

STABLES = {"USDT": (USDT, 6), "USDC": ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6)}
# Verified swap volume that makes an address worth tracking.
TRACK_QUOTE_RAW = 1000 * 10**6
TRACK_AMOUNT_RAW = 5000 * 10**9
# Transfers below this do not matter for funding chains.
FUND_RAW = 1000 * 10**6
# A sender reaching very many tracked addresses behaves like an exchange router.
HUB_RECIPIENTS = 50
MAX_BALANCE_ADDRESSES = 500
SNAPSHOT_SECONDS = 1800
SNAPSHOT_KEEP = 512
# Public archive RPCs refuse deep filtered getLogs; recent transfers are what
# the funding chains need (balances are always current via balanceOf).
LOOKBACK_BLOCKS = 216_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS powder_transfers(
 id TEXT PRIMARY KEY, token TEXT NOT NULL, height INTEGER NOT NULL, ts INTEGER NOT NULL,
 tx_hash TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL, amount_raw TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS powder_dst ON powder_transfers(dst, token);
CREATE INDEX IF NOT EXISTS powder_src ON powder_transfers(src, token);
CREATE TABLE IF NOT EXISTS powder_done(
 address TEXT PRIMARY KEY, height INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS powder_balances(
 address TEXT NOT NULL, token TEXT NOT NULL, balance_raw TEXT NOT NULL,
 height INTEGER NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY(address, token));
CREATE TABLE IF NOT EXISTS powder_snapshots(
 ts INTEGER PRIMARY KEY, buy_own_raw TEXT NOT NULL, buy_chain_raw TEXT NOT NULL,
 sell_wgnk_raw TEXT NOT NULL, sell_gnk_raw TEXT NOT NULL, escrow_raw TEXT NOT NULL,
 buy_addresses INTEGER NOT NULL, chain_addresses INTEGER NOT NULL);
"""

# Widely published hot-wallet labels, used only to exclude exchange money.
EXCHANGES = {
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance 14",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance 15",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance 16",
    "0x9696f59e4d72e237be84ffd425dcad154bf96976": "Binance 18",
    "0x8f6cc511dddbb1fcf4f9293a2440dce6ad6a2295": "OKX",
    "0x6cc5f688a315f3dc28a7781717a9a798a59f8576": "OKX hot",
    "0x5041ed759dd4afc3a72b8192c143f72f4724081a": "Bybit",
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": "Bybit hot",
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": "Coinbase 2",
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": "Coinbase 3",
    "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase 4",
    "0x3cfea73f8ac707922f30ddfd685d80c5a2efe8b7": "Coinbase 5",
}

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def initialize(db):
    db.conn.executescript(SCHEMA)


CONFIRMED_ATTR = ("initiator_net", "tx_net")
# A buyer must still hold this share of net confirmed purchases; round-trip
# arbitrage and MEV retention is ~0 and drops out.
RETENTION_SHARE_NUM, RETENTION_SHARE_DEN = 1, 2
# Dust below this stable amount contributes nothing to the powder.
POWDER_ROW_RAW = 1000 * 10**6


def _confirmed(db):
    """Per-address confirmed buy/sell totals; executor round-trips excluded."""
    result = defaultdict(lambda: {"buy_quote_raw": 0, "sell_quote_raw": 0,
                                  "buy_raw": 0, "sell_raw": 0})
    for r in db.conn.execute("""
            SELECT actor, kind, SUM(CAST(quote_raw AS INTEGER)) q, SUM(CAST(amount_raw AS INTEGER)) v
            FROM events WHERE chain='ethereum' AND finalized=1 AND kind IN ('buy','sell')
            AND json_extract(meta,'$.attribution') IN ('initiator_net','tx_net')
            GROUP BY actor, kind"""):
        row = result[r["actor"]]
        side = "buy" if r["kind"] == "buy" else "sell"
        row[side + "_quote_raw"] = r["q"]
        row[side + "_raw"] = r["v"]
    return result


def tracked_addresses(db):
    """Confirmed participants whose swaps reached the tracking thresholds."""
    rows = _confirmed(db)
    return {actor: {"quote_raw": s["buy_quote_raw"] + s["sell_quote_raw"],
                    "volume_raw": s["buy_raw"] + s["sell_raw"]}
            for actor, s in rows.items()
            if actor and actor != ZERO
            and (s["buy_quote_raw"] + s["sell_quote_raw"] >= TRACK_QUOTE_RAW
                 or s["buy_raw"] + s["sell_raw"] >= TRACK_AMOUNT_RAW)}


def side_split(db):
    return _confirmed(db)


def hubs(db):
    """Senders distributing to very many tracked addresses (exchange-like)."""
    counts = defaultdict(set)
    for r in db.conn.execute("SELECT src,dst FROM powder_transfers"):
        counts[r["src"]].add(r["dst"])
    return {src for src, dsts in counts.items() if len(dsts) >= HUB_RECIPIENTS}


def excluded_senders(db):
    return set(EXCHANGES) | hubs(db) | {ZERO}


def funders(db, buyers):
    """Stablecoin funding edges into the given addresses, above the threshold."""
    edges = defaultdict(lambda: {"raw": 0, "txs": 0})
    for r in db.conn.execute("SELECT src,dst,CAST(amount_raw AS INTEGER) a FROM powder_transfers"):
        if r["dst"] in buyers and r["src"] not in buyers and r["a"] >= FUND_RAW:
            row = edges[(r["src"], r["dst"])]
            row["raw"] += r["a"]
            row["txs"] += 1
    result = defaultdict(dict)
    for (src, dst), row in edges.items():
        result[dst][src] = row
    return result


def stable_balances(db, addresses):
    out = defaultdict(dict)
    for r in db.conn.execute("SELECT address,token,balance_raw,height FROM powder_balances"):
        if r["address"] in addresses:
            out[r["address"]][r["token"]] = (int(r["balance_raw"]), r["height"])
    return out


def wgnk_ledger(db):
    from .flows import market_packet, market_rows, history_end, balances as ledger_of
    packet = market_packet(db)
    snapshot = packet["snapshot"] if packet else db.get("flow:snapshot")
    start = db.get("mints:deployment", {}).get("height")
    if not snapshot or not start or not snapshot.get("ledger_verified"):
        return None, None
    if not packet and history_end(db, start, snapshot["height"]) != snapshot["height"]:
        return None, None
    ledger, _, _ = ledger_of(market_rows(db, start, snapshot, packet))
    return ledger, snapshot


def bridge_map(db):
    """eth_address -> every verified gonka counterparty, mints and burns alike;
    buyers accumulate on the burn side, sellers on the mint side."""
    links = defaultdict(set)
    for table in ("gonka_mint_links", "gonka_burn_links"):
        for row in db.conn.execute(f"SELECT value FROM {table}"):
            link = json.loads(row[0])
            if link.get("eth_address") and link.get("gnk_address"):
                links[link["eth_address"]].add(link["gnk_address"])
    return links


def native_gnk(db):
    """Latest verified bank-balance snapshots per gonka address."""
    native = {}
    for row in db.conn.execute("SELECT address,value FROM gonka_address_balances"):
        snap = (json.loads(row[1]).get("snapshot") or {})
        if snap.get("amount_raw"):
            native[row[0]] = int(snap["amount_raw"])
    return native


def qualified_split(db):
    """Per-address qualified buyers (still holding) and sellers, with the ledger."""
    sides = side_split(db)
    ledger, snapshot = wgnk_ledger(db)
    if ledger is None:
        return None
    # Burns move bought WGNK into the buyer's own Gonka custody; that is
    # holding too. Mints are not subtracted: imported coins still kept are
    # holdings, and flippers are already excluded by the positive net filter.
    burned_out = defaultdict(int)
    for r in db.conn.execute("SELECT src,CAST(amount_raw AS INTEGER) v FROM events "
                             "WHERE chain='ethereum' AND finalized=1 AND kind='bridge_burn'"):
        burned_out[r["src"]] += r["v"]
    # Dry powder belongs to buyers who HOLD Gonka: confirmed purchases only,
    # net accumulation positive, and at least half of the net purchase still
    # held in either network. Arbitrageurs and MEV round-trips drop out.
    buyers = set()
    for a, s in sides.items():
        if not (s["buy_quote_raw"] >= TRACK_QUOTE_RAW or s["buy_raw"] >= TRACK_AMOUNT_RAW):
            continue
        net = s["buy_raw"] - s["sell_raw"]
        if net <= 0:
            continue
        if (ledger.get(a, 0) + burned_out.get(a, 0)) * RETENTION_SHARE_DEN >= net * RETENTION_SHARE_NUM:
            buyers.add(a)
    sellers = {a for a, s in sides.items()
               if s["sell_quote_raw"] >= TRACK_QUOTE_RAW or s["sell_raw"] >= TRACK_AMOUNT_RAW}
    return {"sides": sides, "ledger": ledger, "snapshot": snapshot,
            "burned_out": burned_out, "buyers": buyers, "sellers": sellers}


def participant_mains(db, qualified):
    """Collapse probable one-participant groups to the main address per side:
    the member with the largest confirmed quote volume. One address per
    participant keeps the analysis and the RPC balance load small."""
    from .clusters import cached
    from .config import SEED_POOLS
    sides, buyers, sellers = qualified["sides"], qualified["buyers"], qualified["sellers"]
    rated = buyers | sellers
    data = (cached(db, sorted(rated), SEED_POOLS) if len(rated) > 1
            else {"groups": [], "address_group": {}})

    def collapse(side_set, score):
        groups = defaultdict(list)
        for a in side_set:
            groups[data["address_group"].get(a, a)].append(a)
        mains, sizes = set(), {}
        for members in groups.values():
            main = max(members, key=lambda a: (score(a), a))
            mains.add(main)
            sizes[main] = len(members)
        return mains, sizes
    buyer_mains, buyer_sizes = collapse(buyers, lambda a: sides[a]["buy_quote_raw"])
    seller_mains, seller_sizes = collapse(sellers, lambda a: sides[a]["sell_quote_raw"])
    return buyer_mains, buyer_sizes, seller_mains, seller_sizes


def aggregate(db, qualified=None):
    """The whole powder picture at the latest verified balances."""
    tracked = tracked_addresses(db)
    if not tracked:
        return {"ready": False, "reason": "no_tracked_addresses"}
    qualified = qualified or qualified_split(db)
    if qualified is None:
        return {"ready": False, "reason": "snapshot_not_verified"}
    sides, ledger, snapshot = qualified["sides"], qualified["ledger"], qualified["snapshot"]
    links, native = bridge_map(db), native_gnk(db)
    excluded = excluded_senders(db)
    buyers, buyer_sizes, sellers, seller_sizes = participant_mains(db, qualified)
    overloaded = {(("0x" + topic[-40:]) if topic.startswith("0x") else topic)
                  for topic in (db.get("powder:skipped") or {})}
    funding = funders(db, buyers)
    chain = {src for dst in buyers for src in funding[dst]
             if src not in excluded and src not in overloaded}
    stable = stable_balances(db, set(tracked) | chain)

    def powder_of(address):
        return sum(max(0, v[0]) for v in stable.get(address, {}).values())

    # Exchange and hub senders are distribution infrastructure even when they
    # trade; their balances must not count as personal dry powder.
    own_buyers = buyers - excluded
    buy_rows = []
    for address in own_buyers:
        own = powder_of(address)
        # A funder's dry-powder contribution is capped by what it actually sent
        # to this buyer: a whale's one-off transfer must not add its whole balance.
        rows = [(src, funding[address][src]["raw"], min(powder_of(src), funding[address][src]["raw"]))
                for src in funding[address] if src not in excluded]
        buy_rows.append({"address": address, "own_raw": str(own),
                         "chain_raw": str(sum(row[2] for row in rows)),
                         "group_size": buyer_sizes.get(address, 1),
                         "funders": [{"address": src, "sent_raw": str(sent), "balance_raw": str(capped)}
                                     for src, sent, capped in sorted(rows, key=lambda r: -r[2])[:12]]})
    sell_rows = [{"address": address, "wgnk_raw": str(ledger.get(address, 0)),
                  "gnk_raw": str(sum(native.get(g, 0) for g in links.get(address, ())))}
                 for address in sellers]
    for r in sell_rows:
        r["group_size"] = seller_sizes.get(r["address"], 1)
    escrow_raw = int((db.get("escrow") or {}).get("data", {}).get("amount", 0))
    buy_rows = [r for r in buy_rows
                if int(r["own_raw"]) + int(r["chain_raw"]) >= POWDER_ROW_RAW]
    buy_own = sum(int(r["own_raw"]) for r in buy_rows)
    buy_chain = sum(int(r["chain_raw"]) for r in buy_rows)
    buy_rows = sorted(buy_rows, key=lambda r: -(int(r["own_raw"]) + int(r["chain_raw"])))
    sell_wgnk = sum(int(r["wgnk_raw"]) for r in sell_rows)
    sell_gnk = sum(int(r["gnk_raw"]) for r in sell_rows)
    heights = {h for by_address in stable.values() for _, h in by_address.values()}
    covered = sum(1 for r in buy_rows if stable.get(r["address"]))
    return {"ready": True, "height": snapshot["height"], "ts": snapshot.get("ts"),
            "balance_coverage": {"covered": covered, "qualified": len(buyers)},
            "stable_height": max(heights) if heights else None,
            "buyers": buy_rows,
            "sellers": sorted(sell_rows, key=lambda r: -(int(r["wgnk_raw"]) + int(r["gnk_raw"]))),
            "exchanges": sorted(EXCHANGES.items()),
            "totals": {"buy_own_raw": str(buy_own), "buy_chain_raw": str(buy_chain),
                       "sell_wgnk_raw": str(sell_wgnk), "sell_gnk_raw": str(sell_gnk),
                       "escrow_raw": str(escrow_raw)},
            "tokens": {name: contract for name, (contract, _) in STABLES.items()},
            "thresholds": {"track_quote": tokens(TRACK_QUOTE_RAW, 6),
                           "track_amount": tokens(TRACK_AMOUNT_RAW),
                           "funding": tokens(FUND_RAW, 6)},
            "note": ("Покупатели — подтверждённые сделки (исполнители-круговики MEV и арбитраж "
                     "не учитываются) с положительным чистым накоплением, удерживающие не менее "
                     "половины купленного: баланс WGNK плюс чистый вывод через мост в своё "
                     "хранение. Порох — стейблкоины USDT/USDC на основных адресах покупателей "
                     "и их прямых финансистов; пыль до 1 000 USDT, кошельки бирж и инфраструктура "
                     "исключены. Запасы продавцов — WGNK/GNK и эскроу моста. Для каждого участника "
                     "считается только основной адрес вероятной группы — это снижает нагрузку на "
                     "RPC; остальные адреса группы не суммируются. Связи по переводам — "
                     "вероятность, не владелец.")}


def save_snapshot(db, picture):
    if not picture.get("ready"):
        return
    totals = picture["totals"]
    now = int(time.time())
    last = db.conn.execute("SELECT MAX(ts) FROM powder_snapshots").fetchone()[0]
    if last and now - last < SNAPSHOT_SECONDS:
        return
    with db.conn:
        db.conn.execute("INSERT OR REPLACE INTO powder_snapshots VALUES(?,?,?,?,?,?,?,?)", (
            now, totals["buy_own_raw"], totals["buy_chain_raw"], totals["sell_wgnk_raw"],
            totals["sell_gnk_raw"], totals["escrow_raw"],
            len(picture["buyers"]), sum(len(r["funders"]) for r in picture["buyers"])))
        db.conn.execute(
            "DELETE FROM powder_snapshots WHERE ts NOT IN (SELECT ts FROM powder_snapshots ORDER BY ts DESC LIMIT ?)",
            (SNAPSHOT_KEEP,))
    db.revision += 1


class PowderCollector:
    """Paced background indexer: one shared windowed scan for all tracked
    addresses (topic arrays), per-address catch-up for late joiners, then
    periodic balance refreshes and totals snapshots."""

    WINDOW = 10_000        # Public archive plans cap getLogs block ranges.
    TOPIC_CHUNK = 64       # Providers reject very large topic arrays.

    def __init__(self, owner):
        self.owner, self.db, self.net = owner, owner.db, owner.net
        initialize(self.db)

    async def _store(self, events):
        """Timestamps are intentionally not fetched: one block call per log is
        what stalled the whole collector on retail-dense windows."""
        if not events:
            return
        with self.db.conn:
            for name, height, tx, idx, src, dst, raw in events:
                self.db.conn.execute(
                    "INSERT OR IGNORE INTO powder_transfers VALUES(?,?,?,?,?,?,?,?)",
                    (f"ethereum:{tx}:{idx}", name, height, 0, tx, src, dst, raw))
        self.db.revision += 1

    async def scan_chunk(self, contract, name, padded, lo, hi, depth=0):
        """Incoming stable transfers for a set of addresses; overloaded chunks
        are split recursively, hyper-active addresses are skipped for the window."""
        if not padded:
            return
        topics = [TRANSFER, None, padded]
        try:
            items = await self.net.eth_logs({"address": contract, "fromBlock": hex(lo),
                                             "toBlock": hex(hi), "topics": topics}, archive=True)
        except Exception:
            if len(padded) == 1:
                skipped = self.db.get("powder:skipped") or {}
                skipped[padded[0]] = lo
                self.db.put("powder:skipped", skipped)
                return
            middle = len(padded) // 2
            await self.scan_chunk(contract, name, padded[:middle], lo, hi, depth+1)
            await self.scan_chunk(contract, name, padded[middle:], lo, hi, depth+1)
            return
        events = []
        for item in items:
            if item.get("removed"):
                continue
            raw = int(item["data"], 16)
            if raw < FUND_RAW:
                continue  # Retail dust is irrelevant to funding chains.
            events.append((name, int(item["blockNumber"], 16),
                           item["transactionHash"].lower(), int(item["logIndex"], 16),
                           "0x" + item["topics"][1][-40:], "0x" + item["topics"][2][-40:],
                           str(raw)))
        await self._store(events)

    async def scan_window(self, addresses, lo, hi):
        """Incoming stable transfers for all addresses in [lo, hi]; outgoing
        flows are not needed for funding chains and overload public RPCs.
        Hyper-active infrastructure wallets are skipped after their first
        overload; their balances still refresh via cheap balanceOf calls."""
        skipped = set(self.db.get("powder:skipped") or {})
        padded = ["0x" + a[2:].zfill(64) for a in sorted(addresses) if a[2:].zfill(64) not in {s[-64:] for s in skipped}]
        for name, (contract, _) in STABLES.items():
            for offset in range(0, len(padded), self.TOPIC_CHUNK):
                await self.scan_chunk(contract, name, padded[offset:offset+self.TOPIC_CHUNK], lo, hi)

    async def scan_address(self, address, start, head):
        await self.scan_window({address}, start, min(head, start + self.WINDOW - 1))

    async def run(self):
        if not getattr(self.owner.cfg, "powder_enabled", True):
            return 600
        deployment = (self.db.get("mints:deployment") or {}).get("height")
        head = self.db.get("mints:status", {}).get("finalized_height")
        if not deployment or not head:
            return 10
        start = max(deployment, head - LOOKBACK_BLOCKS)
        tracked = sorted(tracked_addresses(self.db))
        cursor = self.db.get("powder:cursor") or start - 1
        bulk_done = bool(self.db.get("powder:bulk_done"))

        async def shared_window(lo_limit):
            hi = min(head, cursor + self.WINDOW)
            if hi < lo_limit:
                return None
            await self.scan_window(set(tracked), cursor + 1, hi)
            self.db.put("powder:cursor", hi)
            with self.db.conn:
                for address in tracked:
                    self.db.conn.execute("INSERT OR REPLACE INTO powder_done VALUES(?,?)", (address, hi))
            self.db.revision += 1
            return hi

        if not bulk_done:
            if cursor < head:
                await shared_window(cursor + 1)
                return .5
            self.db.put("powder:bulk_done", True)
        elif cursor < head:
            await shared_window(cursor + 1)
            return .5
        else:
            # Late joiners: personal catch-up over history the shared scan
            # completed before they reached the tracking thresholds.
            done = {r[0]: r[1] for r in self.db.conn.execute("SELECT address,height FROM powder_done")}
            todo = [a for a in tracked if done.get(a, 0) < cursor]
            if todo:
                address = todo[0]
                lo = max(start, done.get(address, start))
                await self.scan_address(address, lo, cursor)
                with self.db.conn:
                    self.db.conn.execute("INSERT OR REPLACE INTO powder_done VALUES(?,?)",
                                         (address, min(cursor, lo + self.WINDOW - 1)))
                return .5
        # Balances and snapshots served only the removed forecast tab; the
        # transfers scan stays because leaders clusters use funding evidence.
        return 600

    async def refresh_balances(self, limit=6):
        tracked = set(tracked_addresses(self.db))
        excluded = excluded_senders(self.db)
        qualified = qualified_split(self.db)
        # Balance calls are the RPC-heavy part: refresh main participant
        # addresses and their funders only, never the whole tracked set.
        if qualified:
            buyer_mains, _, seller_mains, _ = participant_mains(self.db, qualified)
            base = buyer_mains | seller_mains
        else:
            base = set()
        if not base:
            base = set(tracked)
        wanted = set(base)
        for dst, srcs in funders(self.db, base).items():
            wanted |= {src for src in srcs if src not in excluded}
        wanted -= excluded
        head = self.db.get("mints:status", {}).get("finalized_height")
        if not head or not wanted:
            return
        rows = {r[0]: r[1] for r in self.db.conn.execute(
            "SELECT address, MAX(updated_at) FROM powder_balances GROUP BY address")}
        # Tracked participants (the holders themselves) refresh before funders.
        order = sorted(wanted, key=lambda a: (a not in base, a not in tracked, rows.get(a, 0)))[:MAX_BALANCE_ADDRESSES]
        now = int(time.time())
        for address in order[:limit]:
            for name, (contract, _) in STABLES.items():
                try:
                    raw = str(int(await self.net.call(contract, "balanceOf(address)",
                                                      address[2:].zfill(64), tag=hex(head)), 16))
                except Exception:
                    continue
                with self.db.conn:
                    self.db.conn.execute("INSERT OR REPLACE INTO powder_balances VALUES(?,?,?,?,?)",
                                         (address, name, raw, head, now))
        self.db.revision += 1
