"""Verify the signed resident process and its two-file RAM-disk contract."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('fixture_io',ROOT/'scripts/build-polaris-init.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)


class IoFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temporary.cleanup)
        cls.out=Path(cls.temporary.name)
        subprocess.run([sys.executable,ROOT/'scripts/build-polaris-init.py','--mode','io','--out',cls.out],check=True,capture_output=True)

    def test_container_keeps_signed_init_and_fixed_ram_file_separate(self):
        image=(self.out/'launchd.macho').read_bytes();disk=(self.out/'root.img').read_bytes()
        identity=fixture.inspect(image)
        self.assertEqual(identity['mode'],'io')
        self.assertTrue(identity['c_stack_entry_aligned'])
        self.assertEqual(set(identity['io_symbols']),fixture.IO_WANTED)
        magic,version,count,launch,size,state,capacity=struct.unpack_from('<8sII4Q',disk)
        self.assertEqual((magic,version,count,launch,capacity),(b'N4KROOT\0',2,2,4096,4096))
        self.assertEqual(disk[launch:launch+size],image)
        self.assertEqual(state%4096,0)
        self.assertGreaterEqual(state,launch+size)
        self.assertEqual(len(disk),state+capacity)
        self.assertEqual(disk[state:state+len(fixture.IO_SEED)],fixture.IO_SEED)
        self.assertEqual(fixture.ramdisk(image),disk)
        self.assertEqual(identity['signature_flags'],0x302)

    def test_io_code_tampering_is_rejected_before_root_assembly(self):
        image=bytearray((self.out/'launchd.macho').read_bytes());identity=fixture.inspect(image)
        image[identity['code_file_offset']]^=1
        with self.assertRaisesRegex(ValueError,'page hash mismatch'):
            fixture.ramdisk(image)

    def test_valid_signature_does_not_qualify_an_unaligned_c_entry(self):
        identity=fixture.inspect((self.out/'launchd.macho').read_bytes())
        unsigned=bytearray((self.out/'unsigned.macho').read_bytes())
        struct.pack_into('<I',unsigned,identity['code_file_offset'],0xd503201f)
        # Re-sign our own fixture: integrity alone cannot prove its startup ABI.
        signed=fixture.static_and_sign(unsigned)
        self.assertFalse(fixture.inspect(signed)['c_stack_entry_aligned'])
