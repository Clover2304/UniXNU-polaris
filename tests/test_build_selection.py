"""Keep the hardware platform build independent of the optional IPSW stage."""
import importlib.util
from pathlib import Path
import re
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('platform_build', ROOT / 'scripts/build-platform-kernel.py')
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class BuildSelectionTests(unittest.TestCase):
    def selection(self, filename):
        text = (ROOT / 'configs' / filename).read_text()
        # The pinned UniXNU parser removes CONFIG_ and unquotes string values.
        value = re.search(r'^CONFIG_XNU_CRYPTO_BOOTSTRAP="([^"]+)"$', text, re.M).group(1)
        return {'XNU_CRYPTO_BOOTSTRAP': value}

    def test_platform_stays_source_only_with_prelink_metadata_present(self):
        board = tomllib.loads((ROOT / 'board.toml').read_text())
        self.assertTrue(board['kernel']['prelink'])
        self.assertFalse(BUILD.prelink_requested(self.selection('polaris_defconfig'), board))

    def test_prelink_configuration_selects_the_optional_stage(self):
        board = tomllib.loads((ROOT / 'board.toml').read_text())
        self.assertTrue(BUILD.prelink_requested(self.selection('polaris_prelink_defconfig'), board))

    def test_ramdisk_configuration_retains_the_source_provider(self):
        board = tomllib.loads((ROOT / 'board.toml').read_text())
        self.assertFalse(BUILD.prelink_requested(self.selection('polaris_ramdisk_defconfig'), board))
        text = (ROOT / 'configs/polaris_ramdisk_defconfig').read_text()
        self.assertIn('CONFIG_XNU_SOURCE_MOCKFS=y', text)

    def test_prelink_configuration_requires_its_inputs(self):
        with self.assertRaisesRegex(ValueError, r'\[kernel.prelink\]'):
            BUILD.prelink_requested(self.selection('polaris_prelink_defconfig'), {})
