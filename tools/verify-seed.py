"""Restore a bundled public seed into a disposable DB and verify its scope and balances."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db import Database
from app.flows import analysis
from app.mints import initialize,progress
from app.seed import restore_seed,DEFAULT_ARCHIVE

archive=Path(sys.argv[1]) if len(sys.argv)>1 else DEFAULT_ARCHIVE
with tempfile.TemporaryDirectory(prefix='gonka-seed-check-') as tmp:
    target=Path(tmp)/'seed.sqlite3'
    assert restore_seed(target,archive)
    db=Database(target)
    try:
        initialize(db)
        data=analysis(db,side='all',limit=1)
        assert data['ready'] and data['coverage']['complete'] and progress(db)['complete']
        assert db.conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.conn.execute("SELECT COUNT(*) FROM events WHERE chain!='ethereum' OR finalized!=1").fetchone()[0]==0
        tables={r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'labels' in tables:assert db.conn.execute('SELECT COUNT(*) FROM labels').fetchone()[0]==0
        keys=[r[0] for r in db.conn.execute('SELECT key FROM kv')]
        assert all(key in {'mints:deployment','mints:status','mints:next','mints:bootstrapped','flow:snapshot','flow:status'} for key in keys),keys
        assert not restore_seed(target,archive),'Existing runtime database must not be overwritten'
        print(json.dumps({'ok':True,'height':data['snapshot']['height'],'ts':data['snapshot']['ts'],
            'coverage':data['coverage'],'swaps':data['total'],'last_trade':data['latest_trade'],
            'public_only':True,'existing_db_preserved':True}))
    finally:db.close()
