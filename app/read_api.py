"""Serialize CPU-heavy public reads away from the ASGI event loop.

Each job owns a read-only SQLite transaction. The indexer's connection is never
used from this thread; cancellation cannot start a second concurrent calculation.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3

from .db import Database, IntSum


class ReadView(Database):
    def _readonly(self, *args, **kwargs):
        # Fail before Database.put's transaction manager could roll back and
        # accidentally unpin the request's read transaction.
        raise sqlite3.OperationalError('read-only database view')

    put = save_batch = snapshot = _range = _readonly


@contextmanager
def read_view(db, *, shared_state=None, memo=None):
    before = db.revision
    path = db.path
    # Database uses a file-backed WAL; no schema changes or bootstrap in readers.
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
    conn.row_factory = sqlite3.Row
    conn.create_aggregate('sumint', 1, IntSum)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        conn.execute('SELECT count(*) FROM kv').fetchone()  # pin the WAL snapshot
        reader = ReadView.__new__(ReadView)
        reader.conn, reader.path = conn, path
        after = db.revision
        reader.revision = before if before == after else object()
        reader.analysis_memo = {} if memo is None else memo
        if shared_state is not None:
            reader._leader_base = shared_state
        yield reader
    finally:
        conn.close()


class ReadAPI:
    def __init__(self, db, cache, max_pending=4):
        import threading
        self.db, self.cache = db, cache
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='flow-public-read')
        self.leader_state = {'lock': threading.Lock(), 'entry': None}
        self.memo = {}
        self.max_pending, self.pending = max_pending, 0

    async def run(self, build, *, key=None, ttl=0, as_response=True):
        from fastapi import HTTPException
        if self.pending >= self.max_pending:
            raise HTTPException(429, 'Очередь расчётов заполнена. Повторите позже.',
                                headers={'Retry-After': '1'})
        def calculate():
            from fastapi.responses import JSONResponse, Response
            def produce():
                with read_view(self.db, shared_state=self.leader_state, memo=self.memo) as reader:
                    result = build(reader)
                    return result
            result = self.cache.get_or_create(key, ttl, produce) if key is not None else produce()
            # Serialization also belongs in this worker; JSONResponse is pure data.
            return result if not as_response or isinstance(result, Response) else JSONResponse(result)
        loop = asyncio.get_running_loop()
        self.pending += 1
        try:
            future = self.executor.submit(calculate)
        except BaseException:
            self.pending -= 1
            raise
        # Count the actual worker future, not the cancelled HTTP waiter. A
        # disconnected client cannot free capacity while its job is still running.
        future.add_done_callback(lambda _: loop.call_soon_threadsafe(self._finished))
        return await asyncio.wrap_future(future)

    def _finished(self):
        self.pending -= 1

    async def close(self):
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=True)
        self.leader_state['entry'] = None
        self.memo.clear()
