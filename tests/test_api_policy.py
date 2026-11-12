import asyncio
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from uvicorn import Config
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api_policy import APIAdmission, PublicAPIGuard, ResponseCache, client_key
from app.config import Settings
from app.main import create_app


def scope(path='/api/mints', client='192.0.2.1', headers=(), query=b'', method='GET'):
    return {'type': 'http', 'method': method, 'path': path, 'query_string': query,
            'client': (client, 1234), 'headers': list(headers)}


class Clock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now


def settings(**kw):
    return Settings(indexer_enabled=False, keep_alive_enabled=False, seed_enabled=False,
                    snapshot_restore=False, **kw)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.cfg = settings(api_allowed_origins=['*'])

    def admit_and_release(self, state, request):
        result = state.admit(request)
        if result is None:
            state.release(request['path'])
        return result

    def test_client_window_and_precise_expiry(self):
        state = APIAdmission(replace(self.cfg, api_client_per_minute=2), self.clock)
        request = scope('/api/docs')
        for _ in range(2):
            self.assertIsNone(self.admit_and_release(state, request))
        self.clock.now += 12.1
        self.assertEqual(state.admit(request)[:2], (429, 48))
        self.clock.now = 1060.0
        self.assertIsNone(self.admit_and_release(state, request))

    def test_shared_limit_survives_different_clients_and_headers(self):
        state = APIAdmission(replace(self.cfg, api_global_per_minute=2), self.clock)
        for ip in ('192.0.2.1', '192.0.2.2'):
            self.assertIsNone(self.admit_and_release(state, scope(client=ip)))
        self.assertEqual(state.admit(scope(client='192.0.2.3'))[0], 429)
        state = APIAdmission(replace(self.cfg, api_client_per_minute=1), self.clock)
        self.assertIsNone(self.admit_and_release(state, scope(headers=[(b'x-forwarded-for', b'192.0.2.8')])))
        self.assertEqual(state.admit(scope(headers=[(b'x-forwarded-for', b'192.0.2.9')]))[0], 429)

    def test_ipv6_rotation_and_ipv4_mapped_addresses(self):
        self.assertEqual(client_key(scope(client='2001:db8:1::1')), client_key(scope(client='2001:db8:1::ffff')))
        self.assertNotEqual(client_key(scope(client='2001:db8:1::1')), client_key(scope(client='2001:db8:2::1')))
        self.assertEqual(client_key(scope(client='::ffff:192.0.2.1')), '192.0.2.1')

    def test_limited_client_cannot_exhaust_remaining_shared_quota(self):
        state = APIAdmission(replace(self.cfg, api_client_per_minute=1, api_global_per_minute=2), self.clock)
        self.assertIsNone(self.admit_and_release(state, scope(client='192.0.2.1')))
        for _ in range(10):
            self.assertEqual(state.admit(scope(client='192.0.2.1'))[0], 429)
        self.assertIsNone(self.admit_and_release(state, scope(client='192.0.2.2')))
        self.assertEqual(state.admit(scope(client='192.0.2.3'))[0], 429)

    def test_heavy_and_export_have_separate_global_and_client_quotas(self):
        cfg = replace(self.cfg, api_heavy_per_minute=1, api_export_per_minute=1,
                      api_heavy_global_per_minute=2, api_export_global_per_minute=2)
        state = APIAdmission(cfg, self.clock)
        for path in ('/api/mints/flows', '/api/mints/export.csv'):
            self.assertIsNone(self.admit_and_release(state, scope(path)))
            self.assertEqual(state.admit(scope(path))[0], 429)
            self.assertIsNone(self.admit_and_release(state, scope(path, client='192.0.2.2')))
            self.assertEqual(state.admit(scope(path, client='192.0.2.3'))[0], 429)
        self.assertIsNone(self.admit_and_release(state, scope('/api/docs')))

    def test_full_client_table_does_not_reset_existing_quota(self):
        state = APIAdmission(replace(self.cfg, api_max_clients=2, api_client_per_minute=1), self.clock)
        for ip in ('192.0.2.1', '192.0.2.2'):
            self.assertIsNone(self.admit_and_release(state, scope(client=ip)))
        self.assertEqual(state.admit(scope(client='192.0.2.3'))[0], 429)
        self.assertEqual(len(state.clients), 2)
        self.assertEqual(state.admit(scope(client='192.0.2.1'))[0], 429)
        self.clock.now += 61
        self.assertIsNone(self.admit_and_release(state, scope(client='192.0.2.3')))
        self.assertEqual(len(state.clients), 1)

    def test_origin_query_and_method_reject_before_analytics(self):
        state = APIAdmission(replace(self.cfg, api_allowed_origins=['https://flow.example'], api_max_query_bytes=10), self.clock)
        for headers in ([(b'origin', b'https://foreign.example')], [(b'origin', b'null')],
                        [(b'origin', b'https://flow.example'), (b'origin', b'https://flow.example')]):
            self.assertEqual(state.admit(scope(headers=headers))[0], 403)
        self.assertEqual(state.admit(scope(query=b'x' * 11))[0], 414)
        self.assertEqual(state.admit(scope(method='POST'))[0], 405)
        self.assertEqual(state.active, 0)
        self.assertIsNone(self.admit_and_release(state, scope(headers=[(b'origin', b'https://flow.example')])))
        # Public non-browser clients intentionally remain permitted, under quotas.
        self.assertIsNone(self.admit_and_release(state, scope()))

    def test_settings_fail_on_zero_and_unsafe_origins(self):
        for kw in ({'api_max_concurrent': 0}, {'api_allowed_origins': []},
                   {'api_allowed_origins': ['*', 'https://flow.example']},
                   {'api_allowed_origins': ['https://u:p@flow.example']},
                   {'api_allowed_origins': ['https://flow.example/path']}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                replace(self.cfg, **kw)


class CacheTests(unittest.TestCase):
    def test_lru_entry_and_byte_limits_and_expiry(self):
        clock = Clock()
        cache = ResponseCache(2, 800, clock)
        cache.put('a', {'x': 'a' * 120}, 3)
        cache.put('b', {'x': 'b' * 120}, 3)
        cache.get('a')
        cache.put('c', {'x': 'c' * 120}, 3)
        self.assertIsNone(cache.get('b'))
        self.assertIsNotNone(cache.get('a'))
        self.assertLessEqual(len(cache.entries), 2)
        self.assertLessEqual(cache.bytes_used, 800)
        clock.now += 3
        self.assertIsNone(cache.get('a'))
        self.assertIsNone(cache.get('c'))
        self.assertEqual(cache.bytes_used, 0)
        cache = ResponseCache(10, 450, clock)
        cache.put('a', {'x': 'a' * 120}, 3)
        cache.put('b', {'x': 'b' * 120}, 3)
        self.assertEqual(len(cache.entries), 1)
        self.assertLessEqual(cache.bytes_used, 450)

    def test_oversize_response_is_returned_without_storage_or_keyerror(self):
        cache = ResponseCache(2, 200)
        large = {'value': 'x' * 1000}
        for _ in range(2):
            self.assertEqual(cache.get_or_create('large', 3, lambda: large), large)
        self.assertEqual(cache.bytes_used, 0)
        self.assertEqual(len(cache.entries), 0)

    def test_builder_failure_is_not_cached(self):
        cache = ResponseCache()
        def fail():
            raise RuntimeError('synthetic failure')
        with self.assertRaises(RuntimeError):
            cache.get_or_create('x', 3, fail)
        self.assertEqual(len(cache.entries), 0)


class ApiIntegrationTests(unittest.TestCase):
    def test_cors_errors_limits_health_and_no_origin_public_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = settings(mode='mints', data_dir=Path(tmp), api_heavy_per_minute=2,
                           api_allowed_origins=['https://flow.gonkamarket.eth.limo'])
            with TestClient(create_app(cfg)) as client, patch('app.main.mint_listing', return_value={'items': []}) as query:
                response = client.get('/api/mints', headers={'Origin': 'https://foreign.example'})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(query.call_count, 0)
                self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
                self.assertNotIn('access-control-allow-origin', response.headers)
                for headers in ({}, {'Origin': 'https://flow.gonkamarket.eth.limo'}):
                    self.assertEqual(client.get('/api/mints', headers=headers).status_code, 200)
                response = client.get('/api/mints', headers={'Origin': 'https://flow.gonkamarket.eth.limo'})
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers['access-control-allow-origin'], 'https://flow.gonkamarket.eth.limo')
                self.assertEqual(response.headers['access-control-expose-headers'], 'Retry-After')
                self.assertGreaterEqual(int(response.headers['retry-after']), 1)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(response.headers['x-frame-options'], 'DENY')
                self.assertEqual(client.get('/healthz').status_code, 200)
                self.assertEqual(client.get('/').status_code, 200)
                self.assertEqual(query.call_count, 1)  # Same query reused its cache.
                self.assertEqual(client.app.state.api_admission.active, 0)

    def test_oversize_cache_does_not_break_http_and_invalid_requests_keep_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = settings(mode='mints', data_dir=Path(tmp), api_cache_bytes=100, api_max_query_bytes=100)
            with TestClient(create_app(cfg)) as client, patch('app.main.mint_listing', return_value={'items': ['x' * 200]}) as query:
                for _ in range(2):
                    self.assertEqual(client.get('/api/mints').status_code, 200)
                self.assertEqual(query.call_count, 2)
                self.assertEqual(client.get('/api/mints?' + 'q=' + 'x' * 100).status_code, 414)
                response = client.post('/api/mints')
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers['allow'], 'GET, HEAD')
                self.assertEqual(client.get('/api/mints?limit=-1').status_code, 422)
                self.assertEqual(client.get('/api/disabled').headers['x-content-type-options'], 'nosniff')


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_render_no_tcp_proxy_trust_ignores_forged_forwarded_headers(self):
        # Render's exact trusted proxy chain is not verified. The deployment
        # trusts only a nonexistent Unix-socket literal, never TCP or loopback.
        # Render does not accept an empty environment-variable value.
        async def inner(request_scope, receive, send):
            await PlainTextResponse('ok')(request_scope, receive, send)
        sentinel = 'unix:/run/flow-no-trusted-proxy.sock'
        with patch.dict(os.environ, {'FORWARDED_ALLOW_IPS': sentinel}):
            config = Config(inner)
        self.assertEqual(config.forwarded_allow_ips, sentinel)
        for peer in ('127.0.0.1', '10.0.0.2'):
            state = APIAdmission(settings(api_client_per_minute=1))
            app = ProxyHeadersMiddleware(PublicAPIGuard(inner, state),
                                         trusted_hosts=config.forwarded_allow_ips)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
                                         base_url='http://test') as client:
                first = await client.get('/api/docs', headers={'X-Forwarded-For': '192.0.2.10'})
                second = await client.get('/api/docs', headers={'X-Forwarded-For': '192.0.2.11'})
                self.assertEqual((first.status_code, second.status_code), (200, 429))
                self.assertEqual(list(state.clients), [peer])

    async def test_busy_limit_health_and_cancellation_release(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def inner(scope, receive, send):
            if scope['path'] != '/healthz':
                entered.set()
                await release.wait()
            await PlainTextResponse('ok')(scope, receive, send)
        state = APIAdmission(settings(api_max_concurrent=1))
        guarded = PublicAPIGuard(inner, state)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded), base_url='http://test') as client:
            first = asyncio.create_task(client.get('/api/mints'))
            await entered.wait()
            busy = await client.get('/api/mints')
            self.assertEqual((busy.status_code, busy.headers['retry-after']), (429, '1'))
            self.assertEqual((await client.get('/healthz')).status_code, 200)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertEqual(state.active, 0)
            release.set()
            self.assertEqual((await client.get('/api/mints')).status_code, 200)
            self.assertEqual(state.active, 0)

    async def test_export_stays_single_and_slot_covers_entire_response(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def inner(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'chunk', 'more_body': True})
            started.set()
            await release.wait()
            await send({'type': 'http.response.body', 'body': b'end', 'more_body': False})
        state = APIAdmission(settings())
        guarded = PublicAPIGuard(inner, state)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded), base_url='http://test') as client:
            first = asyncio.create_task(client.get('/api/mints/export.csv'))
            await started.wait()
            self.assertEqual(state.active_exports, 1)
            busy = await client.get('/api/mints/bridge/export.csv')
            self.assertEqual(busy.status_code, 429)
            release.set()
            self.assertEqual((await first).content, b'chunkend')
            self.assertEqual((state.active, state.active_exports), (0, 0))

    async def test_unhandled_error_releases_slot(self):
        async def fail(scope, receive, send):
            raise RuntimeError('synthetic failure')
        state = APIAdmission(settings())
        guard = PublicAPIGuard(fail, state)
        async def receive():
            return {'type': 'http.request', 'body': b''}
        async def send(message):
            pass
        with self.assertRaises(RuntimeError):
            await guard(scope(), receive, send)
        self.assertEqual(state.active, 0)


if __name__ == '__main__':
    unittest.main()
