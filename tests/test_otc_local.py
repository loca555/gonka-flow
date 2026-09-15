import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app


class OtcIsolationTests(unittest.TestCase):
    def test_otc_flag_defaults_to_disabled(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertFalse(Settings().otc_local_enabled)

    def test_helper_allowed_only_when_explicitly_enabled_and_host_is_local(self):
        for enabled, host, expected in [(False, '127.0.0.1', False),
                                        (True, '127.0.0.1', True),
                                        (True, 'localhost', True),
                                        (True, 'gonka-flow.onrender.com', False)]:
            with self.subTest(enabled=enabled, host=host), tempfile.TemporaryDirectory() as tmp:
                cfg = Settings(mode='mints', data_dir=Path(tmp), indexer_enabled=False,
                               seed_enabled=False, otc_local_enabled=enabled)
                with TestClient(create_app(cfg), base_url='http://' + host) as client:
                    page = client.get('/')
                    self.assertEqual(page.status_code, 200)
                    self.assertEqual('data-otc-enabled="true"' in page.text, expected)
                    self.assertEqual('http://127.0.0.1:8791' in page.headers['content-security-policy'], expected)
                    self.assertEqual('http://127.0.0.1:8794' in page.headers['content-security-policy'], expected)
                    self.assertEqual('http://127.0.0.1:8793' in page.headers['content-security-policy'], expected)
                    self.assertNotIn("'unsafe-eval'", page.headers['content-security-policy'])
                    self.assertEqual(client.post('/api/otc/rpc', json={}).status_code, 410)

    def test_otc_assets_versioned_with_other_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(mode='mints', data_dir=Path(tmp), indexer_enabled=False, seed_enabled=False)
            with TestClient(create_app(cfg)) as client:
                page = client.get('/')
                self.assertIn('/static/otc.js?v=', page.text)
                self.assertIn('/static/otc.css?v=', page.text)
                self.assertEqual(client.get('/static/otc.js').status_code, 200)


if __name__ == '__main__':
    unittest.main()
