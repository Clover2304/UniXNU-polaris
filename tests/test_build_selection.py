"""Keep the hardware platform build independent of the optional IPSW stage."""
import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('platform_build', ROOT / 'scripts/build-platform-kernel.py')
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class BuildSelectionTests(unittest.TestCase):
    def test_overlapping_io_overlay_survives_two_baseline_passes(self):
        # Earlier patches cannot reverse-check while a later overlay changes
        # their context. Exercise the baseline loop with real overlapping diffs.
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / 'patches/xnu').mkdir(parents=True)
            source = work / '.cache/xnu-work'
            source.mkdir(parents=True)
            (source / 'node.c').write_text('int state = 0;\n')
            for name, old, new in [('mockfs-test.patch', 0, 1), ('polaris-io.patch', 1, 2), ('polaris-process.patch', 2, 3), ('polaris-tmp.patch', 3, 4), ('polaris-usb.patch', 4, 5)]:
                (work / 'patches/xnu' / name).write_text(
                    '--- a/node.c\n+++ b/node.c\n@@ -1 +1 @@\n'
                    f'-int state = {old};\n+int state = {new};\n')
            script = work / 'baseline.sh'
            script.write_text('''#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")" && pwd)
cd "$ROOT"
for LATE_PATCH in "$ROOT/patches/xnu/zzz-boot-progress.patch" "$ROOT/patches/xnu/zzy-native-gpu-addon-mount.patch"; do
  if git -C .cache/xnu-work apply --reverse --check "$LATE_PATCH" >/dev/null 2>&1; then
    git -C .cache/xnu-work apply --reverse "$LATE_PATCH"
  fi
done
for PATCH in "$ROOT"/patches/xnu/*.patch; do
  if ! git -C .cache/xnu-work apply --reverse --check "$PATCH" >/dev/null 2>&1; then
    git -C .cache/xnu-work apply --check "$PATCH"
    git -C .cache/xnu-work apply "$PATCH"
  fi
done
''')
            BUILD.install_late_io_patch_hook(script)
            once = script.read_text()
            BUILD.install_late_io_patch_hook(script)
            self.assertEqual(script.read_text(), once)
            for _ in range(2):
                result = subprocess.run(['bash', script], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((source / 'node.c').read_text(), 'int state = 5;\n')

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
