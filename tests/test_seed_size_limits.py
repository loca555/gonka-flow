import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.seed import MAX_UNPACKED_SEED_BYTES, restore_seed


class SeedSizeLimitTests(unittest.TestCase):
    def fixture(self, root, declared_size, payload=b'not a database'):
        archive = root / 'wgnk.sqlite3.gz'
        archive.write_bytes(gzip.compress(payload))
        (root / 'manifest.json').write_text(json.dumps({
            'format': 1, 'network': 'ethereum', 'unpacked_bytes': declared_size,
            'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        }), encoding='utf-8')
        return archive

    def test_invalid_declared_sizes_rejected_before_creating_output(self):
        for value in [0, -1, True, '1024', None, MAX_UNPACKED_SEED_BYTES + 1]:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = self.fixture(root, value)
                with patch('app.seed.tempfile.mkstemp') as create:
                    with self.assertRaisesRegex(ValueError, 'size limit'):
                        restore_seed(root / 'restored.sqlite3', archive)
                    create.assert_not_called()
                self.assertFalse((root / 'restored.sqlite3').exists())

    def test_stream_must_not_exceed_declared_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = self.fixture(root, 512, b'x' * 1024)
            with self.assertRaisesRegex(ValueError, 'exceeds declared size'):
                restore_seed(root / 'restored.sqlite3', archive)
            self.assertFalse((root / 'restored.sqlite3').exists())

    def test_existing_runtime_database_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'runtime.sqlite3'
            target.write_bytes(b'existing runtime data')
            self.assertFalse(restore_seed(target, root / 'missing-archive.gz'))
            self.assertEqual(target.read_bytes(), b'existing runtime data')
