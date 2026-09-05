"""Merge a previously collected SQLite archive without replacing live cursors.

Usage: python -m app.import_history /path/to/archive.sqlite3
The input must be a trusted Gonka Flow database, not an arbitrary uploaded file.
"""
import sqlite3
import sys
from pathlib import Path
from .config import Settings
from .db import Database

def merge(source_path, destination):
    source_path=Path(source_path).resolve()
    source=sqlite3.connect(source_path.as_uri()+"?mode=ro",uri=True)
    source.row_factory=sqlite3.Row
    inserted=0
    try:
        for r in source.execute("SELECT * FROM ranges ORDER BY chain,lo"):
            chain,lo,hi=r["chain"],r["lo"],r["hi"]
            rows=source.execute("SELECT * FROM events WHERE chain=? AND height BETWEEN ? AND ? AND finalized=1",
                                (chain,lo,hi)).fetchall()
            events=[Database.event(x) for x in rows]
            blocks=[dict(x) for x in source.execute(
                "SELECT height,hash,ts FROM blocks WHERE chain=? AND height BETWEEN ? AND ?",(chain,lo,hi))]
            destination.save_batch(chain,lo,hi,events,blocks)
            inserted+=len(events)
        return inserted
    finally:
        source.close()

if __name__=="__main__":
    db=Database(Settings().data_dir/"gonka-flow.sqlite3")
    try:
        print("Merged verified historical events:",merge(sys.argv[1],db))
    finally:
        db.close()
