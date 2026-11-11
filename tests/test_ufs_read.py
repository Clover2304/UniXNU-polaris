"""Bounded native-sector transport and independent GPT/reference qualification."""
import hashlib
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('read_usb',ROOT/'scripts/check-polaris-usb.py')
usb=importlib.util.module_from_spec(spec);spec.loader.exec_module(usb)


def fixture():
    data=bytearray(6*4096);data[510:512]=b'\x55\xaa';data[450]=0xee
    entries=bytearray(4096);entries[:16]=b'T'*16;entries[16:32]=b'U'*16
    struct.pack_into('<2Q',entries,32,6,90);name='userdata'.encode('utf-16-le');entries[56:56+len(name)]=name
    data[8192:12288]=entries
    h=bytearray(4096);h[:8]=b'EFI PART';struct.pack_into('<4I',h,8,0x10000,92,0,0)
    struct.pack_into('<4Q',h,24,1,99,6,94);h[56:72]=b'D'*16
    struct.pack_into('<Q3I',h,72,2,32,128,zlib.crc32(entries))
    struct.pack_into('<I',h,16,zlib.crc32(h[:92]));data[4096:8192]=h
    return bytes(data)


def wire(build,reference,pa):
    rows=[f'POLARIS_UFS_READ build={build} state=ok count=6 writes=18 doorbells=6 smmu_unchanged=1 prdt_entries=1 sector_commands=6 media_writes=0 smmu_writes=0 retained=1 pa={pa:016x}']
    upiu=(b'\x21'+bytes(33)).hex();prdt=struct.pack('<QII',pa+8192,0,4095).hex()
    for n in range(6):
        rows += [f'POLARIS_UFS_READ_CMD n={n} lba={n} opcode=28 lun=00 blocks=1 state=ok ocs=0 response=00 status=00 flags=00 bytes=4096 residual=0 segment=0 sense_len=0 guards=1 stopped=1 polls=2 run=00000000 db=00000000 is=00000401 upiu={upiu}',
                 f'POLARIS_UFS_READ_DMA n={n} data_pa={pa+8192:016x} prdt={prdt}']
        for at in range(0,4096,32):rows.append(f'POLARIS_UFS_READ_DATA n={n} offset={at} hex={reference[n*4096+at:n*4096+at+32].hex()}')
    rows+=['POLARIS_UFS_READ_FAULT global_before=00000000 bank_before=00000400 global_after=00000000 bank_after=00000400','POLARIS_UFS_READ_END']
    return ('\n'.join(rows)+'\n').encode()


class UfsReadTests(unittest.TestCase):
    def test_actual_read_transport_golden_wire_and_noncoherent_dma(self):
        with tempfile.TemporaryDirectory() as directory:
            binary=Path(directory)/'read-test'
            r=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror',
                '-fsanitize=address,undefined','-I',ROOT/'drivers/PolarisUsb',ROOT/'tests/ufs-read-model.cpp','-o',binary],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([binary],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    def test_sector_reference_identity_boundaries_status_and_truncation(self):
        build='a'*40;pa=0x102000000;reference=fixture();raw=wire(build,reference,pa)
        prior=dict(source_commit=build,ufs_scsi_capacity_verified=True,ufs_prdt_payload_verified=True,
                   ufs_nop_dma={'pa':pa},ufs_scsi_capacity=dict(last_lba=99,block_bytes=4096))
        result=usb.verify_ufs_read(build,raw,prior,reference)
        self.assertTrue(result['ufs_native_media_read_verified']);self.assertTrue(result['primary_gpt_verified'])
        self.assertFalse(result['backup_gpt_verified']);self.assertFalse(result['media_write_verified'])
        self.assertEqual(len(result['ufs_read_sectors']),6)
        self.assertEqual(result['ufs_read_sectors'][0]['sha256'],hashlib.sha256(reference[:4096]).hexdigest())
        self.assertLess(len(raw),131072)
        for broken in (raw.replace(build.encode(),b'b'*40),raw.replace(b'lba=5',b'lba=6'),
            raw.replace(b'writes=18',b'writes=19'),raw.replace(b'media_writes=0',b'media_writes=1'),
            raw.replace(b'status=00',b'status=02'),raw.replace(b'guards=1',b'guards=0'),
            raw.replace(b'residual=0',b'residual=4'),raw.replace(b'offset=4064',b'offset=4032'),
            raw.replace(struct.pack('<QII',pa+8192,0,4095).hex().encode(),struct.pack('<QII',pa+8192,0,511).hex().encode()),
            raw.replace(b'bank_after=00000400',b'bank_after=00000402'),raw[:-40],
            wire(build,reference[:-32]+bytes([0xd3])*32,pa),wire(build,bytes([0xd3])*len(reference),pa)):
            with self.assertRaises(ValueError):usb.verify_ufs_read(build,broken,prior,reference)
        with self.assertRaises(ValueError):usb.verify_ufs_read(build,raw,{**prior,'ufs_scsi_capacity_verified':False},reference)
        with self.assertRaises(ValueError):usb.verify_ufs_read(build,raw,prior,reference[:-1])

    def test_gpt_crc_capacity_partition_overlap_and_fixed_read_limits(self):
        reference=fixture();capacity=dict(last_lba=99,block_bytes=4096)
        self.assertTrue(usb.verify_primary_gpt(reference,capacity)['primary_gpt_verified'])
        def reseal(data):
            table,count,stride=struct.unpack_from('<Q2I',data,4096+72)
            if table<6 and table*4096+count*stride<=len(data):
                struct.pack_into('<I',data,4096+88,zlib.crc32(data[table*4096:table*4096+count*stride]))
            data[4096+16:4096+20]=bytes(4)
            struct.pack_into('<I',data,4096+16,zlib.crc32(data[4096:4096+92]))
        cases=[]
        for offset in (510,4096,4096+16,8192):
            d=bytearray(reference);d[offset]^=1;cases.append(d)
        for offset,value,fmt in ((4096+32,98,'<Q'),(4096+72,6,'<Q'),(4096+80,129,'<I'),(8192+32,2,'<Q')):
            d=bytearray(reference);struct.pack_into(fmt,d,offset,value);reseal(d);cases.append(d)
        d=bytearray(reference);d[8192+128:8192+256]=d[8192:8192+128];d[8192+128+16:8192+128+32]=b'V'*16;reseal(d);cases.append(d)
        for broken in cases:
            with self.assertRaises(ValueError):usb.verify_primary_gpt(bytes(broken),capacity)
