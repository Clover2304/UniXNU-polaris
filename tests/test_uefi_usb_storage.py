import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

spec = importlib.util.spec_from_file_location('uefi_usb_storage', Path(__file__).resolve().parents[1] / 'scripts/check-uefi-usb-storage.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
spec_update = importlib.util.spec_from_file_location('uefi_usb_update', Path(__file__).resolve().parents[1] / 'scripts/update-uefi-usb-esp.py')
update = importlib.util.module_from_spec(spec_update)
spec_update.loader.exec_module(update)


def fixture(names=('userdata',), overlap=False, damage=None):
    sector = 4096; size = 512 * 1024 * 1024; last = size // sector - 1
    table = bytearray(128 * 128)
    for n, name in enumerate(names):
        at = n * 128; table[at:at + 16] = bytes([1]) * 16; table[at + 16:at + 32] = bytes([n + 2]) * 16
        lo = 1024 if overlap else 1024 + n * 32768
        struct.pack_into('<2Q', table, at + 32, lo, lo + 32767)
        encoded = name.encode('utf-16le'); table[at + 56:at + 56 + len(encoded)] = encoded
    table_crc = zlib.crc32(table)
    def header(here, other, entries):
        raw = bytearray(sector); raw[:8] = b'EFI PART'
        struct.pack_into('<4I4Q', raw, 8, 0x10000, 92, 0, 0, here, other, 6, last - 5)
        raw[56:72] = bytes([42]) * 16
        struct.pack_into('<Q3I', raw, 72, entries, 128, 128, table_crc)
        struct.pack_into('<I', raw, 16, zlib.crc32(raw[:92]))
        return bytes(raw)
    blocks = {sector: header(1, last, 2), last * sector: header(last, 1, last - 4),
              2 * sector: bytes(table), (last - 4) * sector: bytes(table)}
    if damage == 'header':
        raw = bytearray(blocks[sector]); raw[56] ^= 1; blocks[sector] = bytes(raw)
    elif damage == 'table':
        raw = bytearray(table); raw[60] ^= 1; blocks[2 * sector] = bytes(raw)
    elif damage == 'backup':
        raw = bytearray(blocks[last * sector]); raw[56] ^= 1
        raw[16:20] = bytes(4); struct.pack_into('<I', raw, 16, zlib.crc32(raw[:92])); blocks[last * sector] = bytes(raw)
    return lambda at, length: blocks.get(at, b'')[:length], size, sector


class StoragePreflightTests(unittest.TestCase):
    def test_both_gpt_copies_select_userdata_extent(self):
        part, digest = m.userdata_gpt(*fixture())
        self.assertEqual(part, dict(number=1, offset=4194304, bytes=m.ESP_BYTES))
        self.assertEqual(len(digest), 64)

    def test_rejects_corruption_missing_duplicate_and_overlap(self):
        cases = [fixture(damage=x) for x in ('header', 'table', 'backup')]
        cases += [fixture(names=('cache',)), fixture(names=('userdata', 'userdata')),
                  fixture(names=('userdata', 'cache'), overlap=True)]
        for case in cases:
            with self.subTest(case=case[1:]):
                with self.assertRaises(ValueError):
                    m.userdata_gpt(*case)

    def test_rejects_host_disk_and_whole_device(self):
        with self.assertRaises(ValueError):
            m.inspect_device(Path('/dev/null'))

    def test_boot_preparation_requires_explicit_partition_name(self):
        source = fixture(names=('boot',))
        with self.assertRaises(ValueError):
            m.userdata_gpt(*source)
        part, _ = m.userdata_gpt(*source, partition_name='boot', minimum_bytes=64 * 1024 * 1024)
        self.assertEqual(part['number'], 1)

    def test_sector_diff_is_bounded_and_aligned(self):
        before = bytes(m.ESP_BYTES); after = bytearray(before); after[12345] = 1
        changes = m.changed_ranges(before, bytes(after), 4096)
        self.assertEqual([at for at, _ in changes], [12288])
        self.assertEqual(len(changes[0][1]), 4096)
        after[:65537] = b'\1' * 65537
        with self.assertRaisesRegex(ValueError, 'write limit'):
            m.changed_ranges(before, bytes(after), 4096)
        with self.assertRaises(ValueError):
            m.changed_ranges(before[:-1], before, 4096)

    def test_payload_path_rejects_traversal_and_external_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'tree'; (root / 'EFI').mkdir(parents=True)
            good = root / 'EFI/test'; good.write_bytes(b'payload')
            self.assertEqual(update.file_path(root, 'EFI/test'), good)
            self.assertEqual(update.source_files(root), {'EFI/test'})
            outside = Path(directory) / 'outside'; outside.write_bytes(b'private')
            (root / 'EFI/link').symlink_to(outside)
            for path in ('../outside', '/etc/passwd', 'EFI/../../outside', 'EFI/link'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    update.file_path(root, path)
            with self.assertRaises(ValueError):
                update.source_files(root)
            (root / 'EFI/link').unlink()
            (root / 'EFI/dirlink').symlink_to(Path(directory), target_is_directory=True)
            with self.assertRaises(ValueError):
                update.source_files(root)

    def test_title_edit_preserves_kernel_root_and_arguments(self):
        raw = b'version = 1\n[boot]\ntimeout = 3\n[[entry]]\ntitle = "Original"\npath = "kernel.macho"\nargs = "cpus=1 rd=md0"\n'
        changed = update.title_override(raw, 'USB "roundtrip"')
        parsed = update.tomllib.loads(changed.decode())
        self.assertEqual(parsed['entry'][0]['title'], 'USB "roundtrip"')
        self.assertEqual(parsed['entry'][0]['path'], 'kernel.macho')
        self.assertEqual(parsed['entry'][0]['args'], 'cpus=1 rd=md0')
        self.assertEqual(parsed['boot']['timeout'], 3)


if __name__ == '__main__':
    unittest.main()
