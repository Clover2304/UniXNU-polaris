"""Check the EFI/ADT RAM-disk contract and reject unsupported kernel inputs.

The tiny Mach-O below is symbol-table test data, never an executable kernel.
"""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import tomllib
import unittest

from test_adt import decode

ROOT = Path(__file__).resolve().parents[1]


def symbol_fixture(mockfs=True):
    names = [b'_mockfs_mountroot' if mockfs else b'_other', b'_rootvnode']
    strings, entries = bytearray(b'\0'), []
    for index, name in enumerate(names):
        entries.append(struct.pack('<IBBHQ', len(strings), 0x0f, 1, 0, 0xfffffff007004000 + index * 8))
        strings += name + b'\0'
    header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 2, 1, 24, 0, 0)
    command = struct.pack('<6I', 2, 24, 56, len(entries), 56 + 16 * len(entries), len(strings))
    return header + command + b''.join(entries) + strings + b'POLARIS_ROOT_MOUNTED mockfs @%s:%d\0'


class RootPayloadTests(unittest.TestCase):
    def inputs(self, root, *, enabled=True, mockfs_symbol=True):
        kernel = root / 'kernel.macho'
        data = symbol_fixture(mockfs=mockfs_symbol)
        kernel.write_bytes(data)
        kernel.with_suffix('.json').write_text(json.dumps({
            'kernel_sha256': hashlib.sha256(data).hexdigest(),
            'final_configured_link_passed': True, 'original_components_prelinked': False,
            'source_mockfs': enabled, 'root_checkpoint_available': enabled,
        }))
        for relative in ('efi/Build/Out/AARCH64-DEBUG/UniXNU.efi',
                         'loader/build/DarwinBoot/DEBUG_GCC/AARCH64/DarwinBoot.efi'):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'EFI test input')
        ramdisk = root / 'probe.img'
        subprocess.run([sys.executable, ROOT / 'scripts/make-root-probe.py', '--output', ramdisk],
                       check=True, capture_output=True)
        return kernel, ramdisk

    def assemble(self, root, kernel, ramdisk=None):
        command = [sys.executable, ROOT / 'scripts/make-payload-esp.py',
                   '--kernel', kernel, '--efi', root, '--out', root / 'esp', '--no-probe']
        if ramdisk is not None:
            command += ['--initrd', ramdisk]
        return subprocess.run(command, capture_output=True, text=True)

    def test_root_layout_connects_initrd_arguments_and_zero_adt_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel, ramdisk = self.inputs(root)
            result = self.assemble(root, kernel, ramdisk)
            self.assertEqual(result.returncode, 0, result.stderr)
            esp = root / 'esp'
            entry = tomllib.loads((esp / 'EFI/UniXNU/unixnu.toml').read_text())['entry'][0]
            self.assertEqual(entry['initrd'], 'Payloads/root.img')
            self.assertIn('rd=md0', entry['args'].split())
            self.assertIn('polaris-root-check=1', entry['args'].split())
            self.assertNotIn('polaris-ufs-probe=1', entry['args'].split())
            self.assertNotIn('polaris-ufs-inventory=1', entry['args'].split())
            self.assertNotIn('polaris-ufs-nop=1', entry['args'].split())
            payloads = esp / 'EFI/UniXNU/Payloads'
            nodes, _ = decode((payloads / 'kernel.macho.adt').read_bytes())
            self.assertEqual(nodes['/chosen/memory-map']['RAMDisk'], bytes(16))
            self.assertEqual((payloads / 'root.img').read_bytes(), ramdisk.read_bytes())
            report = json.loads((esp / 'esp.json').read_text())
            self.assertEqual(report['ramdisk']['expected_checkpoint'], 'POLARIS_ROOT_MOUNTED mockfs')
            self.assertFalse(report['ramdisk']['userland_booted'])

    def test_platform_layout_has_no_ramdisk_or_root_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel, _ = self.inputs(root, enabled=False)
            result = self.assemble(root, kernel)
            self.assertEqual(result.returncode, 0, result.stderr)
            esp = root / 'esp'
            entry = tomllib.loads((esp / 'EFI/UniXNU/unixnu.toml').read_text())['entry'][0]
            self.assertNotIn('initrd', entry)
            self.assertNotIn('rd=md0', entry['args'].split())
            self.assertFalse((esp / 'EFI/UniXNU/Payloads/root.img').exists())

    def test_ufs_requires_explicit_usb_and_source_probe_support(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel, _ = self.inputs(root)
            result = subprocess.run([sys.executable, ROOT / 'scripts/make-payload-esp.py',
                '--kernel', kernel, '--efi', root, '--out', root / 'esp', '--ufs-probe'],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('UFS register probe requires', result.stderr)
            self.assertFalse((root / 'esp').exists())

    def test_ufs_inventory_requires_explicit_probe_and_source_support(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel, _ = self.inputs(root)
            result = subprocess.run([sys.executable, ROOT / 'scripts/make-payload-esp.py',
                '--kernel', kernel, '--efi', root, '--out', root / 'esp', '--ufs-inventory'],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('UFS inventory requires', result.stderr)
            self.assertFalse((root / 'esp').exists())

    def test_ufs_nop_requires_explicit_inventory_and_source_support(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel, _ = self.inputs(root)
            result = subprocess.run([sys.executable, ROOT / 'scripts/make-payload-esp.py',
                '--kernel', kernel, '--efi', root, '--out', root / 'esp', '--ufs-nop'],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('UFS NOP requires', result.stderr)
            self.assertFalse((root / 'esp').exists())

    def test_old_platform_kernel_is_rejected_before_replacing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel, ramdisk = self.inputs(root, enabled=False)
            (root / 'esp').mkdir()
            marker = root / 'esp/preserve.txt'
            marker.write_text('existing output')
            result = self.assemble(root, kernel, ramdisk)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('mockfs/checkpoint kernel', result.stderr)
            self.assertEqual(marker.read_text(), 'existing output')

    def test_metadata_alone_cannot_make_a_kernel_support_mockfs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel, ramdisk = self.inputs(root, mockfs_symbol=False)
            result = self.assemble(root, kernel, ramdisk)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('_mockfs_mountroot', result.stderr)

    def test_qemu_root_check_rejects_the_platform_kernel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel, ramdisk = self.inputs(root, enabled=False)
            result = subprocess.run([
                sys.executable, ROOT / 'scripts/check-platform-kernel.py', '--kernel', kernel,
                '--efi', root, '--initrd', ramdisk, '--out', root / 'qemu'],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('mockfs/checkpoint kernel', result.stderr)
