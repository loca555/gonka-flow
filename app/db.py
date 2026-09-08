import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
 id TEXT PRIMARY KEY, chain TEXT NOT NULL, height INTEGER NOT NULL,
 block_hash TEXT NOT NULL, ts INTEGER NOT NULL, tx_hash TEXT NOT NULL,
 idx INTEGER NOT NULL, kind TEXT NOT NULL, src TEXT NOT NULL DEFAULT '',
 dst TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT '',
 amount_raw TEXT NOT NULL, asset TEXT NOT NULL, quote_raw TEXT NOT NULL DEFAULT '0',
 quote_asset TEXT NOT NULL DEFAULT '', pool TEXT NOT NULL DEFAULT '',
 finalized INTEGER NOT NULL, request_key TEXT NOT NULL DEFAULT '', meta TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_time ON events(ts DESC, id);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, ts DESC);
CREATE INDEX IF NOT EXISTS events_src ON events(src, ts DESC);
CREATE INDEX IF NOT EXISTS events_dst ON events(dst, ts DESC);
CREATE INDEX IF NOT EXISTS events_actor ON events(actor, ts DESC);
CREATE INDEX IF NOT EXISTS events_request ON events(request_key);
CREATE INDEX IF NOT EXISTS events_height ON events(chain, height);
CREATE INDEX IF NOT EXISTS events_trade_page ON events(ts DESC,height DESC,idx DESC,tx_hash DESC,pool,kind)
 WHERE chain='ethereum' AND finalized=1 AND kind IN ('buy','sell');
CREATE TABLE IF NOT EXISTS ranges(
 chain TEXT NOT NULL, lo INTEGER NOT NULL, hi INTEGER NOT NULL,
 PRIMARY KEY(chain, lo)
);
CREATE TABLE IF NOT EXISTS blocks(
 chain TEXT NOT NULL, height INTEGER NOT NULL, hash TEXT NOT NULL, ts INTEGER NOT NULL,
 PRIMARY KEY(chain, height)
);
CREATE TABLE IF NOT EXISTS bridge_receipts(
 event_id TEXT PRIMARY KEY, value TEXT NOT NULL, checked_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS labels(
 address TEXT PRIMARY KEY, label TEXT NOT NULL, role TEXT NOT NULL,
 source TEXT NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS holder_balances(
 asset TEXT NOT NULL, address TEXT NOT NULL, balance_raw TEXT NOT NULL,
 balance_digits INTEGER NOT NULL, height INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 source TEXT NOT NULL, PRIMARY KEY(asset,address)
);
CREATE INDEX IF NOT EXISTS holders_balance ON holder_balances(asset,balance_digits DESC,balance_raw DESC,address);
CREATE TABLE IF NOT EXISTS native_holder_scan(
 address TEXT PRIMARY KEY, balance_raw TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holder_dirty(
 address TEXT PRIMARY KEY, height INTEGER NOT NULL
);
"""
COLS = ("id chain height block_hash ts tx_hash idx kind src dst actor amount_raw "
        "asset quote_raw quote_asset pool finalized request_key meta").split()

class IntSum:
    def __init__(self):
        self.value = 0
    def step(self, value):
        self.value += int(value or 0)
    def finalize(self):
        return str(self.value)

def tokens(raw, decimals=9):
    value = int(raw)
    whole, fraction = divmod(abs(value), 10 ** decimals)
    result = str(whole)
    if fraction:
        result += "." + str(fraction).zfill(decimals).rstrip("0")
    return ("-" if value < 0 else "") + result

class Database:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.create_aggregate("sumint", 1, IntSum)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.revision = 0

    def close(self):
        self.conn.close()

    def get(self, key, default=None):
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO kv VALUES(?,?)",
                              (key, json.dumps(value, ensure_ascii=False)))
        self.revision += 1

    def snapshot(self, key, data):
        self.put(key, {"updated_at": int(time.time()), "data": data})

    def _range(self, chain, lo, hi):
        rows = self.conn.execute(
            "SELECT lo,hi FROM ranges WHERE chain=? AND hi>=? AND lo<=?",
            (chain, lo - 1, hi + 1)).fetchall()
        if rows:
            lo = min(lo, *(r["lo"] for r in rows))
            hi = max(hi, *(r["hi"] for r in rows))
            self.conn.executemany("DELETE FROM ranges WHERE chain=? AND lo=?",
                                  [(chain, r["lo"]) for r in rows])
        self.conn.execute("INSERT OR REPLACE INTO ranges VALUES(?,?,?)", (chain, lo, hi))

    def save_batch(self, chain, lo, hi, events, blocks, finalized=True, replace_pending=False):
        """A failed batch never advances coverage. Provisional ETH is atomically replaced."""
        with self.conn:
            if replace_pending:
                self.conn.execute("DELETE FROM events WHERE chain=? AND finalized=0", (chain,))
            if finalized:
                self.conn.execute("DELETE FROM events WHERE chain=? AND height BETWEEN ? AND ?",
                                  (chain, lo, hi))
            for b in blocks:
                old = self.conn.execute("SELECT hash FROM blocks WHERE chain=? AND height=?",
                                        (chain, b["height"])).fetchone()
                if finalized and old and old[0] != b["hash"]:
                    # Never silently accept a conflicting finalized provider response.
                    raise ValueError(f"Finalized block conflict: {chain} {b['height']}")
                self.conn.execute("INSERT OR REPLACE INTO blocks VALUES(?,?,?,?)",
                                  (chain, b["height"], b["hash"], b["ts"]))
            for e in events:
                row = dict(e)
                row["meta"] = json.dumps(row.get("meta", {}), ensure_ascii=False)
                self.conn.execute(
                    f"INSERT OR REPLACE INTO events({','.join(COLS)}) VALUES({','.join('?' for _ in COLS)})",
                    [row[k] for k in COLS])
            if finalized:
                self._range(chain, lo, hi)
        self.revision += 1

    def covered(self, chain, height):
        return bool(self.conn.execute(
            "SELECT 1 FROM ranges WHERE chain=? AND lo<=? AND hi>=?", (chain, height, height)).fetchone())

    def coverage(self, chain):
        rows = [dict(r) for r in self.conn.execute(
            "SELECT lo,hi FROM ranges WHERE chain=? ORDER BY lo", (chain,))]
        count = sum(r["hi"] - r["lo"] + 1 for r in rows)
        if not rows:
            return {"ranges": [], "blocks": 0, "first_time": None, "last_time": None}
        times = self.conn.execute(
            "SELECT MIN(ts),MAX(ts) FROM blocks WHERE chain=? AND height BETWEEN ? AND ?",
            (chain, rows[0]["lo"], rows[-1]["hi"])).fetchone()
        return {"ranges": rows, "blocks": count, "first_time": times[0], "last_time": times[1]}

    def label(self, address, label, role, source):
        self.conn.execute("INSERT OR REPLACE INTO labels VALUES(?,?,?,?,?)",
                          (address.lower(), label, role, source, int(time.time())))

    def labels(self):
        return {r["address"]: dict(r) for r in self.conn.execute("SELECT * FROM labels")}

    @staticmethod
    def event(row):
        e = dict(row)
        e["meta"] = json.loads(e["meta"])
        e["amount"] = tokens(e["amount_raw"])
        e["quote_amount"] = tokens(e["quote_raw"], int(e["meta"].get("quote_decimals", 6)))
        e["finalized"] = bool(e["finalized"])
        return e

    def events(self, since=0, kind="", chain="", address="", limit=100, offset=0, min_raw=0):
        where, values = ["ts>=?"], [since]
        if kind:
            kinds = kind.split(",")
            where.append("kind IN (" + ",".join("?" for _ in kinds) + ")")
            values += kinds
        if chain:
            where.append("chain=?")
            values.append(chain)
        if address:
            where.append("(src=? OR dst=? OR actor=?)")
            values += [address.lower()] * 3
        # Length/lexicographic comparison is exact even above SQLite's int64 range.
        if min_raw:
            s = str(min_raw)
            where.append("(length(amount_raw)>? OR (length(amount_raw)=? AND amount_raw>=?))")
            values += [len(s), len(s), s]
        clause = " AND ".join(where)
        total = self.conn.execute(f"SELECT count(*) FROM events WHERE {clause}", values).fetchone()[0]
        rows = self.conn.execute(f"SELECT * FROM events WHERE {clause} ORDER BY ts DESC,height DESC,idx DESC,id LIMIT ? OFFSET ?",
                                 values + [limit, offset]).fetchall()
        return {"items": [self.event(r) for r in rows], "total": total, "offset": offset,
                "has_more": offset + len(rows) < total}
