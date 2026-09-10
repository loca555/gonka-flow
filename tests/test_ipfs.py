import re
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from tools.build_ipfs import build, api_origin, DEFAULT_API


class IpfsExportTests(unittest.TestCase):
    def test_complete_reproducible_frontend_without_server_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first", Path(tmp) / "second"
            manifest = build(first)
            self.assertEqual(build(second), manifest)
            paths = sorted(p.relative_to(first).as_posix() for p in first.rglob('*') if p.is_file())
            self.assertEqual(set(paths), set(manifest['files']) | {'ipfs-build.json'})
            self.assertEqual(len(paths), 14)
            for name in paths:
                content = (first / name).read_bytes()
                self.assertEqual(content, (second / name).read_bytes())
                self.assertNotRegex(content.decode(), r'''["'`]/(?:api|static)(?:[/?"'`])''')
                if name in manifest['files']:
                    self.assertEqual(sha256(content).hexdigest(), manifest['files'][name])
            index = (first / 'index.html').read_text(encoding='utf-8')
            self.assertIn('href="#trading"', index)
            self.assertIn('href="./api-docs.html"', index)
            self.assertIn('href="./"', index)
            self.assertIn('connect-src', index)
            for name, digest in re.findall(r'\./(static/[^"?]+)\?v=([a-f0-9]{16})', index):
                self.assertEqual(digest, manifest['files'][name][:16])
            for script in ('mints', 'flows', 'gonka', 'address-trades', 'leaders'):
                self.assertIn(DEFAULT_API + '/api/mints', (first / f'static/{script}.js').read_text(encoding='utf-8'))
            (first / 'keep.txt').write_text('keep')
            with self.assertRaises(ValueError):
                build(first)
            self.assertEqual((first / 'keep.txt').read_text(), 'keep')

    def test_origin_rejects_credentials_and_unsafe_schemes(self):
        for value in ('https://u:secret@example.com', 'http://example.com', 'javascript:alert(1)',
                      'https://example.com/path', 'https://example.com/?token=secret',
                      'https://example.com/#x', 'https://example.com\"', 'https://example.com bad'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                api_origin(value)
        self.assertEqual(api_origin(DEFAULT_API + '/'), DEFAULT_API)
        self.assertEqual(api_origin('http://127.0.0.1:8799'), 'http://127.0.0.1:8799')


class PublicApiCorsTests(unittest.TestCase):
    def test_ipfs_origins_can_read_data_and_errors_without_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(mode='mints', data_dir=Path(tmp), indexer_enabled=False, seed_enabled=False)
            with TestClient(create_app(cfg)) as client:
                for origin in ('https://flow.gonkamarket.eth.limo', 'https://bafy-example.ipfs.example'):
                    for url, status in (('/api/mints?minimum=0', 200),
                                        ('/api/mints/trades', 422), ('/api/disabled', 410)):
                        response = client.get(url, headers={'Origin': origin})
                        self.assertEqual(response.status_code, status)
                        self.assertEqual(response.headers.get('access-control-allow-origin'), '*')
                        self.assertNotIn('access-control-allow-credentials', response.headers)
                headers = {'Origin': 'https://flow.gonkamarket.eth.limo',
                           'Access-Control-Request-Method': 'GET'}
                self.assertEqual(client.options('/api/mints', headers=headers).status_code, 200)
                headers['Access-Control-Request-Method'] = 'POST'
                self.assertEqual(client.options('/api/mints', headers=headers).status_code, 400)
                for _ in range(181):
                    response = client.get('/api/mints?minimum=0', headers={'Origin': 'https://flow.gonkamarket.eth.limo'})
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers.get('access-control-allow-origin'), '*')


if __name__ == '__main__':
    unittest.main()
