"""Public WGNK archive and targeted Gonka provenance for disposable/free hosting.

Includes completed public GNK holder snapshots from 10k; never copies labels,
full-chain GNK history, RPC configuration, credentials or private research.
Existing runtime databases are never replaced.
"""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .db import Database
from .mints import initialize, progress
from .flows import analysis, balances, event_rows
from .provenance import save_link, save_balance, GNK, HASH
from .codec import kh
from .redemptions import save_burn_link

META_FIELDS = {"contract", "topic", "epoch", "sender", "recipient", "initiator",
               "initiator_net_raw", "attribution", "attributed", "attributed_net_raw",
               "quote_decimals", "transaction_index"}
DEFAULT_ARCHIVE = Path(__file__).parent / "seed-data" / "wgnk.sqlite3.gz"

LINK_FIELDS = set("tx_hash log_index eth_address eth_height eth_ts request_id epoch_id amount_raw gnk_address gnk_tx_hash gnk_height gnk_ts gnk_block_hash event_index native_request_id verified_at verification".split())
BURN_LINK_FIELDS = set('event_id tx_hash log_index eth_address eth_height eth_ts eth_block_hash receipt_index amount_raw gnk_address request_id epoch_id status verification evidence_url verified_at gnk_tx_hash gnk_ts'.split())
INCOMING_FIELDS = set("address tx_hash event_index height ts src amount_raw kind message_index source_label tx_type self_transfer source".split())
SYNC_FIELDS = set("offset anchor head phase exhausted pages next_check checked_at new_head last_pass_at".split())


def export_provenance(reader,db):
    """Copy allowlisted public records linked to included final mints AND burns."""
    tables={r[0] for r in reader.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'gonka_mint_links' not in tables:return
    addresses=set()
    for row in reader.execute('SELECT value FROM gonka_mint_links'):
        value=json.loads(row[0]);link={k:value[k] for k in LINK_FIELDS}
        mint=db.conn.execute('SELECT * FROM wgnk_mints WHERE tx_hash=? AND log_index=?',
                             (link['tx_hash'],link['log_index'])).fetchone()
        if not mint:continue
        if (any(link[a]!=mint[b] for a,b in [('eth_address','recipient'),('eth_height','height'),
                ('eth_ts','ts'),('request_id','request_id'),('amount_raw','amount_raw'),('epoch_id','epoch_id')])
                or not GNK.fullmatch(link['gnk_address']) or not HASH.fullmatch(link['gnk_tx_hash'])
                or kh(link['native_request_id'])!=link['request_id']
                or link['verification']!='native_block_results_and_bls_request'):
            raise ValueError('Seed bridge link does not match its final Ethereum mint')
        save_link(db,link);addresses.add(link['gnk_address'])
    if 'gonka_burn_links' in tables:
        for row in reader.execute('SELECT value FROM gonka_burn_links'):
            value=json.loads(row[0]);link={k:value[k] for k in BURN_LINK_FIELDS}
            if not db.conn.execute('SELECT 1 FROM events WHERE id=?',(link['event_id'],)).fetchone():continue
            save_burn_link(db,link);addresses.add(link['gnk_address'])
    for address in sorted(addresses):
        if 'gonka_address_balances' in tables:
            row=reader.execute('SELECT value FROM gonka_address_balances WHERE address=?',(address,)).fetchone()
            snapshot=json.loads(row[0]).get('snapshot') if row else None
            if snapshot:
                if snapshot['address']!=address or snapshot['scope']!='bank_balance' or snapshot['source']!='https://rpc.gonka.gg':
                    raise ValueError('Invalid public GNK balance snapshot')
                save_balance(db,address,{'denom':snapshot['denom'],'amount':snapshot['amount_raw']},snapshot['height'],snapshot['checked_at'])
                state=json.loads(db.conn.execute('SELECT value FROM gonka_address_balances WHERE address=?',(address,)).fetchone()[0])
                state['next_check']=0
                db.conn.execute('UPDATE gonka_address_balances SET value=? WHERE address=?',(json.dumps(state),address))
        for row in reader.execute('SELECT value FROM gonka_incoming WHERE address=?',(address,)):
            value=json.loads(row[0]);e={k:value[k] for k in INCOMING_FIELDS}
            if (e['address']!=address or e['source']!='gonkalabs_address_index'
                    or not HASH.fullmatch(e['tx_hash']) or str(int(e['amount_raw']))!=e['amount_raw']
                    or int(e['amount_raw'])<=0 or (e['src'] and not GNK.fullmatch(e['src']))):
                raise ValueError('Malformed targeted incoming transfer in seed')
            db.conn.execute('INSERT INTO gonka_incoming VALUES(?,?,?,?,?,?,?,?,?)',(
                address,e['tx_hash'],e['event_index'],e['height'],e['ts'],e['src'],e['amount_raw'],e['kind'],json.dumps(e)))
        row=reader.execute('SELECT value FROM gonka_address_sync WHERE address=?',(address,)).fetchone()
        if row:
            value=json.loads(row[0]);state={k:v for k,v in value.items() if k in SYNC_FIELDS}
            state.update(error=None,next_check=0)
            db.conn.execute('INSERT INTO gonka_address_sync VALUES(?,?)',(address,json.dumps(state)))
    db.conn.commit()


GNK_HOLDER_FIELDS = ("height", "ts", "checked_at", "supply_raw", "scanned", "source")
GNK_HOLDER_SOURCE = "cosmos.bank.denom_owners; pinned block; supply reconciled"


def public_holder_seed(conn):
    """Allowlist a completed snapshot, excluding staging rows and private metadata."""
    from .public_holders import MINIMUM_RAW
    row = conn.execute("SELECT value FROM kv WHERE key='public_holders:GNK'").fetchone()
    if not row:
        return None, []
    saved = json.loads(row[0])
    snapshot = {k: saved[k] for k in GNK_HOLDER_FIELDS}
    if (snapshot['source'] != GNK_HOLDER_SOURCE
            or any(type(snapshot[k]) is not int or snapshot[k] < 1
                   for k in ('height', 'ts', 'checked_at', 'scanned'))
            or snapshot['checked_at'] < snapshot['ts']):
        raise ValueError('Invalid public GNK holder snapshot')
    supply = int(snapshot['supply_raw'])
    if str(supply) != snapshot['supply_raw'] or supply <= 0:
        raise ValueError('Invalid GNK snapshot supply')
    rows = []
    addresses = set()
    for row in conn.execute('SELECT address,balance_raw FROM public_gnk_holders ORDER BY address'):
        address, raw = row
        if (not GNK.fullmatch(address) or address in addresses
                or str(int(raw)) != raw or int(raw) < MINIMUM_RAW):
            raise ValueError('Invalid public GNK holder record')
        addresses.add(address)
        rows.append((address, raw))
    omitted = snapshot['scanned'] - len(rows)
    remainder = supply - sum(int(raw) for _, raw in rows)
    if omitted < 0 or not omitted <= remainder <= omitted * (MINIMUM_RAW - 1):
        raise ValueError('Public GNK holder subset disagrees with verified census supply/count')
    return snapshot, rows


def export_public_holders(reader, db):
    from .public_holders import initialize as initialize_holders
    snapshot, rows = public_holder_seed(reader)
    if snapshot is None:
        return {"gonka_holders": 0}
    initialize_holders(db)
    with db.conn:
        db.conn.executemany('INSERT INTO public_gnk_holders VALUES(?,?)', rows)
    db.put('public_holders:GNK', snapshot)
    return {"gonka_holders": len(rows), "gonka_holders_height": snapshot['height']}



def export_holder_history(reader, db):
    """Only public graph records; no cached labels, errors, settings or private data."""
    if not reader.execute("SELECT 1 FROM sqlite_master WHERE name='holder_native_targets'").fetchone(): return {}
    from .gnk_holder_history import initialize as init_history, MAX_DEPTH
    from .db import COLS
    init_history(db)
    targets=[]
    for address,depth in reader.execute('SELECT address,depth FROM holder_native_targets'):
        if not GNK.fullmatch(address) or not 0<=depth<=MAX_DEPTH: raise ValueError('Invalid public history target')
        targets.append((address,depth))
    db.conn.executemany('INSERT INTO holder_native_targets VALUES(?,?)',targets)
    checked=set()
    for height,verified in reader.execute('SELECT height,checked_at FROM holder_native_blocks'):
        if type(height) is not int or height<1 or (verified is not None and (type(verified) is not int or verified<1)): raise ValueError('Invalid public history block')
        db.conn.execute('INSERT INTO holder_native_blocks VALUES(?,?,0,NULL)',(height,verified))
        if verified: checked.add(height)
    meta_keys={'event','attributes','mining_participant','destination','request','epoch','evidence','attribution'}
    attr_keys={'sender','recipient','amount','msg_index','participant'}
    for identity,height,value in reader.execute('SELECT id,height,value FROM holder_native_events'):
        e=json.loads(value)
        if height not in checked or e['chain']!='gonka' or e['asset']!='GNK' or e['id']!=identity or e['height']!=height or int(e['amount_raw'])<0:
            raise ValueError('Invalid verified native event')
        clean={k:e[k] for k in COLS};clean['trace_order']=e.get('trace_order',e['idx'])
        clean['meta']={k:v for k,v in e['meta'].items() if k in meta_keys}
        if 'attributes' in clean['meta']: clean['meta']['attributes']={k:v for k,v in clean['meta']['attributes'].items() if k in attr_keys}
        db.conn.execute('INSERT INTO holder_native_events VALUES(?,?,?)',(identity,height,json.dumps(clean,sort_keys=True)))
    # Discovery cursors and queued blocks are exported together from the same read transaction.
    for address,depth in targets:
        row=reader.execute('SELECT value FROM gonka_address_sync WHERE address=?',(address,)).fetchone()
        if row:
            state={k:v for k,v in json.loads(row[0]).items() if k in SYNC_FIELDS}
            state.update(next_check=0,error=None)
            db.conn.execute('INSERT OR REPLACE INTO gonka_address_sync VALUES(?,?)',(address,json.dumps(state)))
    db.conn.commit()
    db.put('holder_history:version',1)
    return {key:db.conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for key,table in HISTORY_TABLES.items()}

HISTORY_TABLES={'gonka_history_targets':'holder_native_targets','gonka_history_blocks':'holder_native_blocks','gonka_history_events':'holder_native_events'}
POWDER_TABLES={name:name for name in ("powder_transfers","powder_done","powder_balances","powder_snapshots")}


def export_seed(source, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    source = Path(source).resolve()
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
        reader.row_factory = sqlite3.Row
        reader.execute("BEGIN")
        def saved(key):
            row = reader.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None
        snapshot, deployment = saved("flow:snapshot"), saved("mints:deployment")
        if not snapshot or not snapshot.get("ledger_verified") or not deployment:
            raise ValueError("A reconciled final snapshot is required")
        start, end = deployment["height"], snapshot["height"]
        with tempfile.TemporaryDirectory(dir=destination) as tmp:
            seed_path = Path(tmp) / "seed.sqlite3"
            db = Database(seed_path)
            try:
                initialize(db)
                for table, clause, args in (
                    ("events", "chain='ethereum' AND finalized=1 AND height BETWEEN ? AND ?", (start, end)),
                    ("blocks", "chain='ethereum' AND height BETWEEN ? AND ?", (start, end)),
                    ("wgnk_mints", "finalized=1 AND height BETWEEN ? AND ?", (start, end)),
                ):
                    rows = reader.execute("SELECT * FROM " + table + " WHERE " + clause, args)
                    for row in rows:
                        values = dict(row)
                        if table == "events":
                            metadata = json.loads(values["meta"])
                            values["meta"] = json.dumps({k:v for k,v in metadata.items() if k in META_FIELDS})
                        marks = ",".join("?" for _ in values)
                        db.conn.execute("INSERT INTO " + table + " VALUES(" + marks + ")", list(values.values()))
                for row in reader.execute("SELECT * FROM ranges WHERE chain IN ('ethereum','wgnk_mints')"):
                    lo, hi = max(start, row["lo"]), min(end, row["hi"])
                    if lo <= hi:
                        db._range(row["chain"], lo, hi)
                # Public dry-podder state so restarts do not rebuild stable history.
                powder_manifest = {}
                for table in ("powder_transfers", "powder_done", "powder_balances", "powder_snapshots"):
                    copied = 0
                    for row in reader.execute("SELECT * FROM " + table):
                        marks = ",".join("?" for _ in row)
                        db.conn.execute("INSERT OR IGNORE INTO " + table + " VALUES(" + marks + ")", list(row))
                        copied += 1
                    powder_manifest[table] = copied
                db.conn.commit()
                db.put("mints:deployment", {k:deployment[k] for k in ("height","hash","ts")})
                db.put("mints:status", {"finalized_height":end, "latest_height":end,
                                      "latest_ts":snapshot["ts"], "checked_at":0})
                db.put("mints:next", end+1)
                db.put("mints:bootstrapped", {"source":"bundled_ethereum_archive"})
                db.put("flow:snapshot", snapshot)
                db.put("flow:status", {"indexed_height":end})
                export_provenance(reader,db)
                holder_manifest = export_public_holders(reader,db)
                holder_manifest.update(export_holder_history(reader,db))
                data = analysis(db)
                if not data["ready"] or not data["coverage"]["complete"] or not progress(db)["complete"]:
                    raise ValueError("Seed history has gaps")
                ledger, minted, burned = balances(event_rows(db,start,end))
                if any(n < 0 for n in ledger.values()) or minted-burned != int(snapshot["supply_raw"]):
                    raise ValueError("Seed supply does not reconcile")
                if any(ledger.get(p["address"],0) != int(p["balance_raw"]) for p in snapshot["pools"]):
                    raise ValueError("Seed pools do not reconcile")
                mint_rows={(r["tx_hash"],r["log_index"]):(r["recipient"],r["amount_raw"],r["height"])
                           for r in db.conn.execute("SELECT * FROM wgnk_mints")}
                event_mints={(r["tx_hash"],r["idx"]):(r["dst"],r["amount_raw"],r["height"])
                             for r in db.conn.execute("SELECT * FROM events WHERE kind='bridge_mint'")}
                if mint_rows != event_mints:
                    raise ValueError("Seed mint and market archives disagree")
                manifest = {**holder_manifest, **powder_manifest, "format":1, "network":"ethereum", "height":end, "ts":snapshot["ts"],
                            "events":db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                            "mints":db.conn.execute("SELECT COUNT(*) FROM wgnk_mints").fetchone()[0],
                            "gonka_links":db.conn.execute("SELECT COUNT(*) FROM gonka_mint_links").fetchone()[0],
                            "gonka_burn_links":db.conn.execute("SELECT COUNT(*) FROM gonka_burn_links").fetchone()[0],
                            "gonka_incoming":db.conn.execute("SELECT COUNT(*) FROM gonka_incoming").fetchone()[0],
                            "gonka_balances":db.conn.execute("SELECT COUNT(*) FROM gonka_address_balances").fetchone()[0],
                            "gonka_scope":"Verified bridge provenance plus public holder transfer graph; targeted history is not chain-complete proof"}
            finally:
                db.close()
            archive = destination / "wgnk.sqlite3.gz"
            with seed_path.open("rb") as src, archive.open("wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
                    shutil.copyfileobj(src, zipped)
            manifest.update(sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                            unpacked_bytes=seed_path.stat().st_size, archive_bytes=archive.stat().st_size)
            (destination / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
            return manifest


def restore_seed(target, archive=DEFAULT_ARCHIVE):
    target, archive = Path(target), Path(archive)
    if target.exists() or not archive.exists():
        return False
    manifest = json.loads((archive.parent / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or manifest.get("network") != "ethereum":
        raise ValueError("Unsupported seed manifest")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("Seed archive checksum mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="wgnk-seed-", suffix=".sqlite3", dir=target.parent)
    staged = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as dst, gzip.open(archive, "rb") as src:
            total = 0
            while chunk := src.read(1024*1024):
                total += len(chunk)
                if total > min(manifest["unpacked_bytes"], 128*1024*1024):
                    raise ValueError("Seed archive exceeds declared size")
                dst.write(chunk)
        if total != manifest["unpacked_bytes"]:
            raise ValueError("Seed size mismatch")
        with closing(sqlite3.connect(staged.resolve().as_uri()+"?mode=ro", uri=True)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Seed integrity check failed")
            if conn.execute("SELECT COUNT(*) FROM events WHERE chain!='ethereum'").fetchone()[0]:
                raise ValueError("Seed contains an out-of-scope chain")
            for key,table in HISTORY_TABLES.items():
                if key in manifest and conn.execute('SELECT count(*) FROM '+table).fetchone()[0]!=manifest[key]:
                    raise ValueError('Native history seed disagrees with manifest')
            for key,table in POWDER_TABLES.items():
                if key in manifest and conn.execute('SELECT count(*) FROM '+table).fetchone()[0]!=manifest[key]:
                    raise ValueError('Dry powder seed disagrees with manifest')
            holder_snapshot, holder_rows = public_holder_seed(conn)
            if (len(holder_rows) != manifest.get('gonka_holders', 0)
                    or (holder_snapshot and holder_snapshot['height'] != manifest.get('gonka_holders_height'))):
                raise ValueError('GNK holder seed disagrees with manifest')
        # Exclusive link creation cannot overwrite a concurrently created runtime DB.
        try:
            os.link(staged, target)
        except FileExistsError:
            return False
        return True
    finally:
        staged.unlink(missing_ok=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export"])
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    print(json.dumps(export_seed(args.source, args.destination)))
