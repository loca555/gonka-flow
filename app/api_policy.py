"""Bounded, single-worker API admission and response cache. No external store."""
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
import json
import math
import time

from starlette.responses import JSONResponse

WINDOW = 60.0


def client_key(scope):
    """Use the ASGI peer only; forwarded headers belong to the trusted proxy layer."""
    host = (scope.get('client') or ('unknown', 0))[0]
    try:
        address = ip_address(host)
        if address.version == 6:
            if address.ipv4_mapped:
                return str(address.ipv4_mapped)
            return str(ip_network(f'{address}/64', strict=False))
        return str(address)
    except ValueError:
        return str(host)[:128]


@dataclass
class Bucket:
    general: deque = field(default_factory=deque)
    heavy: deque = field(default_factory=deque)
    export: deque = field(default_factory=deque)
    last_seen: float = 0


class APIAdmission:
    """No awaits during admission: atomic within the application's one event loop."""
    def __init__(self, cfg, clock=time.monotonic):
        self.cfg, self.clock = cfg, clock
        self.clients = OrderedDict()
        self.global_bucket = Bucket()
        self.active = self.active_exports = 0
        self.origins = frozenset(cfg.api_allowed_origins)

    @staticmethod
    def kind(path):
        if path.endswith('.csv'):
            return 'export'
        if path == '/api/mints/tx' or path.startswith('/api/mints/tx/') or path == '/api/docs':
            return 'general'
        # All analytical routes, including full-mode queries, receive the heavy quota.
        return 'heavy'

    @staticmethod
    def trim(queue, now):
        while queue and queue[0] <= now - WINDOW:
            queue.popleft()

    @staticmethod
    def retry(queue, now):
        return max(1, math.ceil(queue[0] + WINDOW - now))

    def admit(self, scope):
        cfg, now = self.cfg, self.clock()
        general = self.global_bucket.general
        self.trim(general, now)
        if len(general) >= cfg.api_global_per_minute:
            return 429, self.retry(general, now), 'Too many requests. Try again later.'
        while self.clients and next(iter(self.clients.values())).last_seen <= now - WINDOW:
            self.clients.popitem(last=False)
        key = client_key(scope)
        bucket = self.clients.get(key)
        if bucket is None:
            if len(self.clients) >= cfg.api_max_clients:
                return 429, 60, 'Too many active clients. Try again later.'
            bucket = self.clients[key] = Bucket(last_seen=now)
        bucket.last_seen = now
        self.clients.move_to_end(key)
        self.trim(bucket.general, now)
        if len(bucket.general) >= cfg.api_client_per_minute:
            return 429, self.retry(bucket.general, now), 'Too many requests. Try again later.'
        bucket.general.append(now)
        # A client already over its own quota cannot spend everyone else's quota.
        general.append(now)
        if len(scope.get('query_string', b'')) > cfg.api_max_query_bytes:
            return 414, None, 'Request is too long.'
        if scope['method'] not in ('GET', 'HEAD'):
            return 405, None, 'Only read requests are available.'
        origins = [v.decode('latin1') for k, v in scope.get('headers', []) if k.lower() == b'origin']
        if '*' not in self.origins and origins and (len(origins) != 1 or origins[0] not in self.origins):
            return 403, None, 'This browser origin is not allowed.'
        kind = self.kind(scope['path'])
        if kind != 'general':
            local_queue = getattr(bucket, kind)
            global_queue = getattr(self.global_bucket, kind)
            self.trim(local_queue, now)
            self.trim(global_queue, now)
            limit = cfg.api_export_per_minute if kind == 'export' else cfg.api_heavy_per_minute
            global_limit = cfg.api_export_global_per_minute if kind == 'export' else cfg.api_heavy_global_per_minute
            if len(local_queue) >= limit:
                return 429, self.retry(local_queue, now), 'Analytics or export limit reached. Try again later.'
            if len(global_queue) >= global_limit:
                return 429, self.retry(global_queue, now), 'Analytics or export limit reached. Try again later.'
            local_queue.append(now)
            global_queue.append(now)
        if self.active >= cfg.api_max_concurrent or (kind == 'export' and self.active_exports >= 1):
            return 429, 1, 'Service is busy. Try again later.'
        self.active += 1
        if kind == 'export':
            self.active_exports += 1
        return None

    def release(self, path):
        self.active -= 1
        if self.kind(path) == 'export':
            self.active_exports -= 1


class PublicAPIGuard:
    def __init__(self, app, admission):
        self.app, self.admission = app, admission

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or not (path == '/api' or path.startswith('/api/')):
            return await self.app(scope, receive, send)
        rejection = self.admission.admit(scope)
        if rejection:
            status, retry, detail = rejection
            headers = {'Cache-Control': 'no-store'}
            if retry is not None:
                headers['Retry-After'] = str(retry)
            if status == 405:
                headers['Allow'] = 'GET, HEAD'
            return await JSONResponse({'detail': detail}, status_code=status, headers=headers)(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            self.admission.release(path)


class ResponseCache:
    """LRU bounded by entries and JSON bytes; large responses are served uncached.

    JSON size is a predictable accounting budget, not an exact Python heap limit.
    No exceptions or RPC errors are cached by this class unless explicitly provided.
    """
    def __init__(self, max_entries=128, max_bytes=8 * 1024 * 1024, clock=time.monotonic):
        self.max_entries, self.max_bytes, self.clock = max_entries, max_bytes, clock
        self.entries = OrderedDict()
        self.bytes_used = 0

    def get(self, key):
        entry = self.entries.get(key)
        if entry is None:
            return None
        expires, data, size = entry
        if expires <= self.clock():
            self.entries.pop(key)
            self.bytes_used -= size
            return None
        self.entries.move_to_end(key)
        return data

    def put(self, key, data, ttl):
        old = self.entries.pop(key, None)
        if old:
            self.bytes_used -= old[2]
        try:
            size = len(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            size += len(repr(key).encode('utf-8')) + 128
        except (TypeError, ValueError):
            return
        if size > self.max_bytes:
            return
        while self.entries and (len(self.entries) >= self.max_entries or self.bytes_used + size > self.max_bytes):
            _, (_, _, removed) = self.entries.popitem(last=False)
            self.bytes_used -= removed
        self.entries[key] = (self.clock() + ttl, data, size)
        self.bytes_used += size

    def get_or_create(self, key, ttl, build):
        data = self.get(key)
        if data is None:
            data = build()
            self.put(key, data, ttl)
        return data
