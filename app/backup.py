"""Consistent online SQLite backup. Refuses to overwrite an existing output."""
import sqlite3
import sys
from pathlib import Path
from .config import Settings

if __name__=="__main__":
    target=Path(sys.argv[1]).resolve()
    if target.exists():
        raise SystemExit("Output exists; choose a new backup filename.")
    source=sqlite3.connect((Settings().data_dir/"gonka-flow.sqlite3").resolve().as_uri()+"?mode=ro",uri=True)
    output=sqlite3.connect(target)
    try:
        source.backup(output)
        print("Consistent backup created:",target)
    finally:
        output.close()
        source.close()
