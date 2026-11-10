"""Probe placement must reserve a whole zero extent inside the retained mapping."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('entry_probe', ROOT / 'scripts/patch-xnu-entry-probe.py')
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class ProbeWindowTests(unittest.TestCase):
    def test_combined_reservation_skips_a_smaller_early_hole(self):
        PROBE.SEGMENTS[:] = [(0x1000, 0x200, 0, 0x200)]
        data = bytearray(b'X' * 0x200)
        data[0x10:0x30] = bytes(0x20)
        data[0x80:0x100] = bytes(0x80)
        self.assertEqual(PROBE.find_zero_window(data, 0x1000, 0x20, 0x1200), 0x1010)
        self.assertEqual(PROBE.find_zero_window(data, 0x1000, 0x70, 0x1200), 0x1080)

    def test_no_spill_into_another_segment_or_unbacked_memory(self):
        PROBE.SEGMENTS[:] = [(0x1000, 0x400, 0, 0x100), (0x1400, 0x100, 0x100, 0x100)]
        data = b'X' * 0x100 + bytes(0x100)
        with self.assertRaisesRegex(SystemExit, 'no zero window'):
            PROBE.find_zero_window(data, 0x1000, 0x80, 0x1100)
        self.assertEqual(PROBE.find_zero_window(data, 0x1000, 0x80, 0x1500), 0x1400)

