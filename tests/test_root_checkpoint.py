"""Recognize the real XNU panic macro format without accepting other panics."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('platform_check', ROOT / 'scripts/check-platform-kernel.py')
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class RootCheckpointTests(unittest.TestCase):
    def test_xnu_appends_location_to_the_compiled_format_string(self):
        fmt = 'POLARIS_ROOT_MOUNTED mockfs @%s:%d'
        self.assertTrue(CHECK.root_checkpoint_present(b'other\0' + fmt.encode() + b'\0'))
        self.assertTrue(CHECK.root_checkpoint_matches(fmt))

    def test_plain_checkpoint_is_also_recognized(self):
        fmt = 'POLARIS_ROOT_MOUNTED mockfs'
        self.assertTrue(CHECK.root_checkpoint_present(fmt.encode() + b'\0'))
        self.assertTrue(CHECK.root_checkpoint_matches(fmt))

    def test_failure_and_prefix_collisions_are_not_success(self):
        for fmt in ('POLARIS_ROOT_CHECK_FAILED: expected mockfs ramdisk @%s:%d',
                    'POLARIS_ROOT_MOUNTED mockfs_not_mounted @%s:%d',
                    'Failed to mount root device @%s:%d'):
            self.assertFalse(CHECK.root_checkpoint_present(fmt.encode() + b'\0'))
            self.assertFalse(CHECK.root_checkpoint_matches(fmt))
