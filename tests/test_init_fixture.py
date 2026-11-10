"""Verify the actual cross-built static executable and its native-4K hashes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('init_fixture', ROOT / 'scripts/build-polaris-init.py')
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


class InitFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.out = Path(cls.temporary.name)
        subprocess.run([sys.executable, ROOT / 'scripts/build-polaris-init.py', '--out', cls.out], check=True, capture_output=True)
        cls.image = (cls.out / 'launchd.macho').read_bytes()

    def test_real_link_has_a_signed_static_entry_and_padded_ramdisk(self):
        report = FIXTURE.inspect(self.image)
        saved = json.loads((self.out / 'init.json').read_text())
        self.assertEqual(report['cdhash'], saved['cdhash'])
        self.assertEqual(report['signature_flags'], 0x302)
        self.assertEqual(report['code_signature_page_bytes'], 4096)
        ramdisk = (self.out / 'root.img').read_bytes()
        self.assertEqual(len(ramdisk) % 4096, 0)
        self.assertTrue(ramdisk.startswith(self.image))
        self.assertNotEqual(report['addresses']['_start'], report['addresses']['_checkpoint'])

    def test_modified_code_is_rejected_without_resigning(self):
        report = FIXTURE.inspect(self.image)
        damaged = bytearray(self.image)
        damaged[report['code_file_offset']] ^= 1
        with self.assertRaisesRegex(ValueError, 'page hash mismatch'):
            FIXTURE.inspect(damaged)

    def test_modified_writable_initial_data_is_also_rejected(self):
        damaged = bytearray(self.image)
        for kind, _, raw in FIXTURE.commands(damaged):
            if kind == 0x19 and raw[8:24].rstrip(b'\0') == b'__DATA':
                import struct
                offset = struct.unpack_from('<Q', raw, 40)[0]
                damaged[offset] ^= 1
                break
        else:
            self.fail('missing actual data segment')
        with self.assertRaisesRegex(ValueError, 'page hash mismatch'):
            FIXTURE.inspect(damaged)
