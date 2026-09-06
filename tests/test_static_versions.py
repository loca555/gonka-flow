import re
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.config import Settings
from app.main import STATIC, create_app, versioned_page


class StaticVersionTests(unittest.TestCase):
    def test_script_and_style_urls_match_content_and_keep_load_order(self):
        for filename in ("mints.html", "index.html"):
            original = re.findall(r'(?:src|href)="(/static/[^"?]+\.(?:js|css))"',
                                  (STATIC / filename).read_text(encoding="utf-8"))
            assets = re.findall(r'(?:src|href)="(/static/[^"?]+\.(?:js|css))\?v=([a-f0-9]{16})"',
                                versioned_page(filename))
            self.assertTrue(assets)
            self.assertEqual([url for url, digest in assets], original)
            for url, digest in assets:
                self.assertEqual(digest, sha256((STATIC / url.removeprefix("/static/")).read_bytes()).hexdigest()[:16])

    def test_same_size_edit_changes_url_without_relying_on_file_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test.html").write_text('<script src="/static/chart.js" defer></script>', encoding="utf-8")
            script = root / "chart.js"
            script.write_text("version=1", encoding="utf-8")
            with patch("app.main.STATIC", root):
                before = versioned_page("test.html")
                script.write_text("version=2", encoding="utf-8")
                self.assertNotEqual(before, versioned_page("test.html"))

    def test_html_and_asset_cache_headers_with_conditional_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(mode="mints", data_dir=Path(tmp), indexer_enabled=False, seed_enabled=False)
            with TestClient(create_app(cfg)) as client:
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                self.assertEqual(page.headers["cache-control"], "no-cache")
                self.assertIn("text/html", page.headers["content-type"])
                asset = re.search(r'src="(/static/chart\.js\?v=[a-f0-9]{16})"', page.text)[1]
                response = client.get(asset)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-cache")
                self.assertIn("function renderAddress", response.text)
                cached = client.get(asset, headers={"If-None-Match": response.headers["etag"]})
                self.assertEqual(cached.status_code, 304)
                self.assertEqual(cached.headers["cache-control"], "no-cache")
                self.assertEqual(client.get("/static/chart.js").headers["cache-control"], "no-cache")
                self.assertEqual(client.get("/api/mints/address/0x" + "f" * 40).headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
