import gzip
import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from app import snapshots
from app.db import Database

REAL_CLIENT = httpx.AsyncClient


def fixture(folder, height):
    folder.mkdir(parents=True, exist_ok=True)
    db = Database(folder / 'source.sqlite3')
    db.put('test:height', height)
    db.close()
    data = (folder / 'source.sqlite3').read_bytes()
    archive = gzip.compress(data, mtime=0)
    manifest = dict(format=1, network='ethereum', height=height,
                    unpacked_bytes=len(data), archive_bytes=len(archive),
                    sha256=hashlib.sha256(archive).hexdigest())
    (folder / 'wgnk.sqlite3.gz').write_bytes(archive)
    (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest, archive


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest, self.archive = fixture(self.root / 'export', 102)
        fixture(self.root / 'bundle', 100)
        self.cfg = SimpleNamespace(snapshot_repo='loca555/test-snapshots',
                                   snapshot_token='synthetic-fixture-token', snapshot_restore=True)
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def client(self, handler):
        def handle(request):
            self.calls.append(request)
            self.assertNotEqual(request.method, 'DELETE', 'Old snapshots must never be deleted')
            return handler(request)
        return patch('app.snapshots.httpx.AsyncClient',
                     side_effect=lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handle), **kw))

    def export(self, source, dest):
        for name in ('manifest.json', 'wgnk.sqlite3.gz'):
            shutil.copyfile(self.root / 'export' / name, Path(dest) / name)

    def publisher(self, fail_asset=None, bad_digest=False, draft_after_publish=False):
        def handle(request):
            if request.url.host == 'uploads.github.com':
                name = request.url.params['name']
                if name == fail_asset:
                    return httpx.Response(503)
                return httpx.Response(201, json=dict(id=201, name=name, state='uploaded',
                    size=len(request.content), digest='sha256:' + (
                        '0'*64 if bad_digest else hashlib.sha256(request.content).hexdigest())))
            if request.method == 'POST':
                self.assertTrue(json.loads(request.content)['draft'])
                return httpx.Response(201, json={'id': 9001, 'draft': True})
            if request.method == 'PATCH':
                self.assertEqual(json.loads(request.content), {'draft': False})
                return httpx.Response(200, json={'id': 9001, 'draft': draft_after_publish})
            raise AssertionError('Unexpected request')
        return handle

    async def test_publish_only_after_both_assets_are_verified_and_preserves_old_releases(self):
        with patch('app.snapshots.export_seed', side_effect=self.export), self.client(self.publisher()):
            self.assertEqual(await snapshots.publish_snapshot(self.cfg, 'unused.sqlite3', 101), 102)
        self.assertEqual([r.method for r in self.calls], ['POST', 'POST', 'POST', 'PATCH'])
        self.assertEqual([r.url.params['name'] for r in self.calls[1:3]], ['wgnk.sqlite3.gz', 'manifest.json'])
        self.assertTrue(all(r.url.host in ('api.github.com', 'uploads.github.com') for r in self.calls))
        self.assertTrue(all('9001' in r.url.path for r in self.calls[1:]))

    async def test_first_or_second_upload_failure_never_publishes_or_deletes_old_copy(self):
        for name in ('wgnk.sqlite3.gz', 'manifest.json'):
            with self.subTest(asset=name):
                self.calls.clear()
                with patch('app.snapshots.export_seed', side_effect=self.export), self.client(self.publisher(fail_asset=name)):
                    with self.assertRaises(httpx.HTTPStatusError):
                        await snapshots.publish_snapshot(self.cfg, 'unused', 102)
                self.assertNotIn('PATCH', [r.method for r in self.calls])

    async def test_wrong_remote_asset_hash_never_publishes(self):
        with patch('app.snapshots.export_seed', side_effect=self.export), self.client(self.publisher(bad_digest=True)):
            with self.assertRaisesRegex(ValueError, 'asset was not verified'):
                await snapshots.publish_snapshot(self.cfg, 'unused', 102)
        self.assertNotIn('PATCH', [r.method for r in self.calls])

    async def test_unconfirmed_publish_is_not_reported_as_success(self):
        with patch('app.snapshots.export_seed', side_effect=self.export), self.client(self.publisher(draft_after_publish=True)):
            with self.assertRaisesRegex(ValueError, 'still a draft'):
                await snapshots.publish_snapshot(self.cfg, 'unused', 102)

    async def test_older_or_corrupt_export_fails_before_contacting_github(self):
        for mode in ('older', 'corrupt'):
            with self.subTest(mode=mode):
                self.calls.clear()
                def export(source, dest):
                    self.export(source, dest)
                    if mode == 'corrupt':
                        (Path(dest) / 'wgnk.sqlite3.gz').write_bytes(b'broken')
                with patch('app.snapshots.export_seed', side_effect=export), self.client(self.publisher()):
                    with self.assertRaises(ValueError):
                        await snapshots.publish_snapshot(self.cfg, 'unused', 103 if mode == 'older' else 102)
                self.assertEqual(self.calls, [])

    async def test_missing_token_or_wrong_repository_rejected_before_export(self):
        for repo, token in [('loca555/test-snapshots', ''), ('other-owner/repo', 'synthetic'), ('loca555/..','synthetic')]:
            with patch('app.snapshots.export_seed') as export:
                cfg = SimpleNamespace(snapshot_repo=repo, snapshot_token=token)
                with self.assertRaises((ValueError, RuntimeError)):
                    await snapshots.publish_snapshot(cfg, 'unused', 1)
                export.assert_not_called()

    def releases(self, heights=(102,), legacy=False, corrupt=None, oversize=False,
                 manifest_size_attack=False, on_archive=None, invalid_sqlite=None):
        rows, bodies = [], {}
        for index, height in enumerate(heights):
            manifest, archive = fixture(self.root / ('remote-' + str(height)), height)
            if height == invalid_sqlite:
                raw = b'not a sqlite database'
                archive = gzip.compress(raw, mtime=0)
                manifest.update(unpacked_bytes=len(raw), archive_bytes=len(archive),
                                sha256=hashlib.sha256(archive).hexdigest())
            tag = snapshots.TAG if legacy else f'flow-snapshot-{height}-' + manifest['sha256'][:12]
            mid, aid = index*2+1, index*2+2
            rows.append(dict(tag_name=tag, draft=False, assets=[
                dict(name='manifest.json', id=mid, url='https://untrusted.invalid/ignored'),
                dict(name='wgnk.sqlite3.gz', id=aid, url='https://untrusted.invalid/ignored')]))
            if oversize:
                manifest['unpacked_bytes'] = snapshots.MAX_UNPACKED_SEED_BYTES + 1
            bodies[mid] = b'x'*(snapshots.MAX_MANIFEST_BYTES+1) if manifest_size_attack else json.dumps(manifest).encode()
            bodies[aid] = b'corrupt' if height == corrupt else archive
        def handle(request):
            self.assertNotIn('authorization', request.headers, 'Readers must not use the writer credential')
            if request.url.host == 'github.com' and request.url.path.endswith('/releases.atom'):
                return httpx.Response(503)
            self.assertEqual(request.url.host, 'api.github.com')
            if request.url.path.endswith('/tags/auto-snapshot'):
                return httpx.Response(404)
            if request.url.path.endswith('/releases'):
                return httpx.Response(200, json=rows)
            identity = int(request.url.path.rsplit('/', 1)[1])
            if identity % 2 == 0 and on_archive:
                on_archive()
            return httpx.Response(200, content=bodies[identity])
        return handle

    async def restore(self, handler, target=None):
        target = target or self.root / 'runtime' / 'restored.sqlite3'
        with patch('app.snapshots.DEFAULT_ARCHIVE', self.root/'bundle'/'wgnk.sqlite3.gz'), self.client(handler):
            result = await snapshots.restore_fresh(target, self.cfg)
        with sqlite3.connect(target) as db:
            height = json.loads(db.execute("SELECT value FROM kv WHERE key='test:height'").fetchone()[0])
        self.assertFalse(any(target.parent.glob('gonka-snapshot-*')), 'Temporary restore data must be cleaned')
        return result, height

    async def test_restore_selects_newest_valid_and_uses_only_constructed_github_urls(self):
        self.assertEqual(await self.restore(self.releases(heights=(101, 103, 102))), (True, 103))

    async def test_broken_newest_download_falls_back_to_older_verified_snapshot(self):
        self.assertEqual(await self.restore(self.releases(heights=(103, 102), corrupt=103)), (True, 102))

    async def test_public_feed_uses_direct_downloads_and_skips_invalid_archives(self):
        repo = self.cfg.snapshot_repo
        releases = {}
        for height in (102, 103, 104):
            manifest, archive = fixture(self.root / f'feed-{height}', height)
            if height == 103:
                archive = archive[:-4]
                manifest.update(sha256=hashlib.sha256(archive).hexdigest(), archive_bytes=len(archive))
            tag = f'flow-snapshot-{height}-{manifest["sha256"][:12]}'
            if height == 104:
                manifest['height'] = 105
            releases[tag] = (manifest, archive)
        entry = '<entry><link rel="alternate" href="{}"/></entry>'
        url = f'https://github.com/{repo}/releases/tag/'
        links = [url + tag for tag in releases]
        links += [url + 'flow-snapshot-999-' + '0'*12 + '/x',
                  'https://untrusted.invalid/' + repo + '/releases/tag/flow-snapshot-999-' + '0'*12]
        atom = '<feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(entry.format(u) for u in links) + '</feed>'

        def handle(request):
            self.assertNotIn('authorization', request.headers)
            self.assertEqual(request.url.host, 'github.com')
            path = request.url.path
            if path == f'/{repo}/releases.atom':
                return httpx.Response(200, text=atom)
            tag, name = path.removeprefix(f'/{repo}/releases/download/').split('/')
            manifest, archive = releases[tag]
            if name == 'manifest.json':
                return httpx.Response(200, json=manifest)
            self.assertNotEqual(manifest['height'], 105, 'Manifest that disagrees with tag must be rejected')
            return httpx.Response(200, content=archive)

        self.assertEqual(await self.restore(handle), (True, 102))

    async def test_unavailable_legacy_tag_lookup_keeps_valid_api_candidates(self):
        regular = self.releases(heights=(102,))
        def handle(request):
            if request.url.path.endswith('/tags/auto-snapshot'):
                return httpx.Response(403)
            return regular(request)
        self.assertEqual(await self.restore(handle), (True, 102))

    async def test_legacy_snapshot_remains_readable(self):
        self.assertEqual(await self.restore(self.releases(legacy=True)), (True, 102))

    async def test_hash_valid_but_invalid_database_falls_back_without_breaking_startup(self):
        self.assertEqual(await self.restore(self.releases(heights=(103, 102), invalid_sqlite=103)), (True, 102))

    async def test_malformed_release_metadata_falls_back_to_bundle(self):
        def handle(request):
            if '/tags/' in request.url.path:
                return httpx.Response(404)
            return httpx.Response(200,json=[None,{'tag_name':None},
                {'tag_name':'flow-snapshot-102-'+'0'*12,'draft':False,'assets':None}])
        self.assertEqual(await self.restore(handle), (True, 100))

    async def test_missing_writer_is_explicitly_disabled_without_claiming_a_backup_exists(self):
        from app.mints import MintIndexer
        db=Database(self.root/'status.sqlite3')
        cfg=SimpleNamespace(snapshot_token='',snapshot_repo='loca555/test-snapshots')
        try:
            with patch('app.snapshots.publish_snapshot') as publish:
                self.assertEqual(await MintIndexer.auto_snapshot(SimpleNamespace(cfg=cfg,db=db)),3600)
                publish.assert_not_called()
            self.assertEqual(db.get('snapshot:configuration')['enabled'],False)
            self.assertIsNone(db.get('snapshot:last'))
        finally:
            db.close()

    async def test_existing_db_is_not_read_from_network_or_replaced(self):
        target = self.root / 'existing.sqlite3'
        target.write_bytes(b'existing runtime data')
        with self.client(lambda request: self.fail('Network accessed')):
            self.assertFalse(await snapshots.restore_fresh(target, self.cfg))
        self.assertEqual(target.read_bytes(), b'existing runtime data')

    async def test_oversized_manifest_rejected_before_archive_download(self):
        self.assertEqual(await self.restore(self.releases(oversize=True)), (True, 100))
        self.assertFalse(any(r.url.path.endswith('/assets/2') for r in self.calls))

    async def test_manifest_response_size_is_bounded(self):
        self.assertEqual(await self.restore(self.releases(manifest_size_attack=True)), (True, 100))

    async def test_remote_network_failure_falls_back_to_local_bundle(self):
        self.assertEqual(await self.restore(lambda r: httpx.Response(503)), (True, 100))

    async def test_download_bound_prevents_oversized_response(self):
        with self.client(lambda r: httpx.Response(200, content=b'x'*20)):
            async with snapshots.httpx.AsyncClient() as client:
                with self.assertRaisesRegex(ValueError, 'size limit'):
                    await snapshots._download(client, 'https://api.github.com/test', self.root/'partial', '0'*64, 10)

    async def test_concurrent_db_creation_is_preserved_after_download(self):
        target = self.root / 'runtime' / 'restored.sqlite3'
        def create():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'concurrent runtime')
        with patch('app.snapshots.DEFAULT_ARCHIVE', self.root/'bundle'/'wgnk.sqlite3.gz'), self.client(self.releases(on_archive=create)):
            self.assertFalse(await snapshots.restore_fresh(target, self.cfg))
        self.assertEqual(target.read_bytes(), b'concurrent runtime')
