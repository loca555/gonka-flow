import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.api_policy import ResponseCache
from app.config import Settings
from app.db import Database
from app.flows import FlowCollector, history_end
from app.main import create_app
from app.read_api import ReadAPI, read_view
import test_flows as fixtures


class ReadIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'runtime.sqlite3')
        self.api = ReadAPI(self.db, ResponseCache())

    async def asyncTearDown(self):
        await self.api.close()
        self.db.close()
        self.temp.cleanup()

    async def test_reader_is_pinned_read_only_and_writer_keeps_progressing(self):
        self.db.put('cursor', 10)
        entered, resume = threading.Event(), threading.Event()
        def read(db):
            first = db.get('cursor')
            entered.set()
            if not resume.wait(2): raise TimeoutError('test did not release reader')
            with self.assertRaises(sqlite3.OperationalError): db.put('forbidden', True)
            return {'first': first, 'second': db.get('cursor')}
        task = asyncio.create_task(self.api.run(read))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            self.db.put('cursor', 11)
        finally:
            resume.set()
        self.assertEqual(json.loads((await task).body), {'first': 10, 'second': 10})
        fresh = await self.api.run(lambda db: {'cursor': db.get('cursor')})
        self.assertEqual(json.loads(fresh.body), {'cursor': 11})
        self.assertIsNone(self.db.get('forbidden'))

    async def test_cancelled_request_cannot_create_parallel_analyses(self):
        entered, resume, second = threading.Event(), threading.Event(), threading.Event()
        def slow(db):
            entered.set()
            if not resume.wait(2): raise TimeoutError('test did not release worker')
            return {}
        first = asyncio.create_task(self.api.run(slow))
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError): await first
        next_task = asyncio.create_task(self.api.run(lambda db: (second.set() or {})))
        try:
            await asyncio.sleep(.03)
            self.assertFalse(second.is_set())
        finally:
            resume.set()
        await next_task
        self.assertTrue(second.is_set())

    async def test_exception_is_not_a_cached_zero_and_reader_is_closed(self):
        def fail(db):
            raise ValueError('provider unavailable')
        with self.assertRaisesRegex(ValueError, 'provider unavailable'):
            await self.api.run(fail, key=('same',), ttl=30)
        data = await self.api.run(lambda db: {'ready': True}, key=('same',), ttl=30)
        self.assertEqual(json.loads(data.body), {'ready': True})

    async def test_busy_capacity_counts_the_running_job_after_http_cancellation(self):
        from fastapi import HTTPException
        self.api.max_pending = 1
        entered, resume = threading.Event(), threading.Event()
        def slow(db):
            entered.set()
            if not resume.wait(2): raise TimeoutError('test did not release worker')
            return {}
        first = asyncio.create_task(self.api.run(slow))
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError): await first
        try:
            with self.assertRaises(HTTPException) as failure:
                await self.api.run(lambda db: {})
            self.assertEqual(failure.exception.status_code, 429)
            self.assertEqual(failure.exception.headers['Retry-After'], '1')
        finally:
            resume.set()

    async def test_leader_base_is_shared_across_isolated_views_and_invalidated(self):
        from app.leaders import base_analysis
        with patch('app.leaders.analysis', return_value={'ready': True}) as build:
            await self.api.run(base_analysis)
            await self.api.run(base_analysis)
            self.assertEqual(build.call_count, 1)
            self.db.put('flow:snapshot', {'height': 99})
            await self.api.run(base_analysis)
            self.assertEqual(build.call_count, 2)

    async def test_group_cache_works_on_read_only_views_without_writing_runtime(self):
        from app.clusters import cached
        from app.mints import initialize
        initialize(self.db)
        expected = {'groups': [], 'address_group': {}}
        with patch('app.clusters.compute', return_value=expected) as compute:
            for _ in range(2):
                result = await self.api.run(lambda db: cached(db, [fixtures.A], []))
                self.assertEqual(json.loads(result.body), expected)
            self.assertEqual(compute.call_count, 1)
            self.assertIsNone(self.db.get('clusters:cache'))
            cached(self.db, [fixtures.A], [])
            cached(self.db, [fixtures.A], [])
            self.assertEqual(compute.call_count, 2)  # KV's JSON list is reusable too.


class HealthResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_heavy_endpoint_and_serialization_do_not_block_health_or_indexer(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(mode='mints', data_dir=Path(tmp), indexer_enabled=False,
                           keep_alive_enabled=False, seed_enabled=False, snapshot_restore=False,
                           history_from='')
            app = create_app(cfg)
            entered, resume = threading.Event(), threading.Event()
            event_thread = threading.get_ident()
            def slow(db, *args, **kw):
                self.assertNotEqual(threading.get_ident(), event_thread)
                self.assertIsNot(db.conn, app.state.db.conn)
                entered.set()
                if not resume.wait(2): raise TimeoutError('test did not release analysis')
                return {'ready': True, 'snapshot': {'height': db.get('cursor')}}
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    with patch('app.main.flow_analysis', side_effect=slow):
                        task = asyncio.create_task(client.get('/api/mints/flows'))
                        try:
                            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                            start = time.monotonic()
                            health = await asyncio.wait_for(client.get('/healthz'), .5)
                            self.assertLess(time.monotonic()-start, .5)
                            self.assertEqual(health.status_code, 200)
                            app.state.db.put('cursor', 100)
                        finally:
                            resume.set()
                        self.assertEqual((await task).status_code, 200)


class CatchupCheckpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.FlowTests.setUp(self)
        self.db.put('flow:snapshot', {**self.db.get('flow:snapshot'), 'checked_at': 0})
        self.db.put('mints:status', {'finalized_height': 100, 'latest_height': 102})
        ready = asyncio.Event(); ready.set()
        async def call(contract, signature, args='', **kw):
            return hex((95 if signature == 'totalSupply()' else 50)*fixtures.UNIT)
        self.owner = SimpleNamespace(db=self.db, ready=ready,
            net=SimpleNamespace(call=AsyncMock(side_effect=call)),
            cfg=SimpleNamespace(eth_history_batch=10),
            block=AsyncMock(side_effect=lambda height, **kw: fixtures.block(height)),
            status=lambda key, **v: self.db.put(key, {**self.db.get(key, {}), **v}))
        self.collector = self.new_collector()

    def new_collector(self):
        self.owner.db = self.db
        collector = FlowCollector(self.owner)
        collector.pools = {fixtures.POOL: self.metadata}
        async def read(lo, hi, **kw):
            return [], [fixtures.block(lo), fixtures.block(hi)]
        collector.read_batch = AsyncMock(side_effect=read)
        return collector

    def tearDown(self):
        fixtures.FlowTests.tearDown(self)

    async def test_verified_prefix_advances_before_tip_and_survives_reopen_and_rpc_failure(self):
        self.assertEqual(await self.collector.run(), .5)
        saved = self.db.get('flow:snapshot')
        self.assertEqual(saved['height'], 22)
        self.assertTrue(saved['ledger_verified'])
        self.assertEqual(history_end(self.db, 1, 100), 22)
        self.assertLess(saved['height'], 100)
        path = self.db.path
        self.db.close(); self.db = Database(path)
        collector = self.new_collector()
        collector.read_batch.side_effect = RuntimeError('RPC unavailable')
        with self.assertRaisesRegex(RuntimeError, 'RPC unavailable'): await collector.run()
        self.assertEqual(collector.read_batch.call_args.args, (23, 32))
        self.assertEqual(history_end(self.db, 1, 100), 22)
        self.assertEqual(self.db.get('flow:snapshot'), saved)
        collector = self.new_collector()
        await collector.run()
        self.assertEqual(collector.read_batch.call_args.args, (23, 32))
        self.assertEqual(history_end(self.db, 1, 100), 32)

    async def test_failed_prefix_verification_preserves_prior_snapshot(self):
        saved = self.db.get('flow:snapshot')
        self.owner.net.call = AsyncMock(return_value=hex(999*fixtures.UNIT))
        with self.assertRaisesRegex(ValueError, 'supply does not reconcile'): await self.collector.run()
        self.assertEqual(self.db.get('flow:snapshot'), saved)
        self.assertEqual(history_end(self.db, 1, 100), 22)
