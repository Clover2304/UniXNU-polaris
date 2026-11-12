"""Actual mtools FAT32 fixtures, malformed metadata and transport range checks."""
from pathlib import Path
import importlib.util
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('fat_usb',ROOT/'scripts/check-polaris-usb.py')
usb=importlib.util.module_from_spec(spec);spec.loader.exec_module(usb)


class UfsFatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.work=Path(cls.tmp.name);cls.binary=cls.work/'fat-test'
        p=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
            '-I',ROOT/'drivers/PolarisUsb',ROOT/'tests/ufs-fat-model.cpp','-o',cls.binary],capture_output=True,text=True)
        if p.returncode:raise AssertionError(p.stdout+p.stderr)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def run_model(self,image,ok,*args):
        p=subprocess.run([self.binary,image,str(int(ok)),*map(str,args)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr);return p.stdout

    def fixture(self,d):
        image=d/'esp.img'
        with image.open('wb') as f:f.truncate(128*1024*1024)
        def command(*args):subprocess.run(args,capture_output=True,check=True)
        command('mformat','-i',str(image),'-F','-c','2','::')
        command('mmd','-i',str(image),'::/EFI','::/EFI/UniXNU')
        payload=d/'READTEST.BIN';payload.write_bytes(bytes((i*37+(i>>8)*11+0x51)&255 for i in range(8193)))
        command('mcopy','-i',str(image),str(payload),'::/EFI/UniXNU/READTEST.BIN')
        with image.open('rb') as f:b=f.read(512)
        spc=b[13];res=struct.unpack_from('<H',b,14)[0];fat=struct.unpack_from('<I',b,36)[0]
        data=(res+2*fat)*512;clusterBytes=spc*512
        def offset(c):return data+(c-2)*clusterBytes
        def find(cluster,name):
            with image.open('rb') as f:f.seek(offset(cluster));raw=f.read(clusterBytes)
            for at in range(0,len(raw),32):
                e=raw[at:at+32]
                if e[:11]==name:return offset(cluster)+at,(struct.unpack_from('<H',e,20)[0]<<16)|struct.unpack_from('<H',e,26)[0]
            raise AssertionError('fixture directory entry missing')
        _,efi=find(2,b'EFI        ');_,unixnu=find(efi,b'UNIXNU     ');entry,first=find(unixnu,b'READTESTBIN')
        def get(c):
            with image.open('rb') as f:f.seek(res*512+c*4);return struct.unpack('<I',f.read(4))[0]&0xfffffff
        chain=[];c=first
        while c<0xffffff8:chain.append(c);c=get(c)
        def put(c,value,both=True):
            with image.open('r+b') as f:
                for n in range(2 if both else 1):f.seek((res+n*fat)*512+c*4);f.write(struct.pack('<I',value))
        return image,entry,chain,put,offset,clusterBytes,efi

    def test_real_fat32_and_fragmented_file_match_mtools(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);image,entry,chain,put,offset,size,efi=self.fixture(d)
            self.run_model(image,True)
            moved=100000
            with image.open('r+b') as f:
                f.seek(offset(chain[4]));block=f.read(size);f.seek(offset(moved));f.write(block)
            put(chain[3],moved);put(moved,chain[5]);put(chain[4],0)
            out=subprocess.check_output(['mtype','-i',str(image),'::/EFI/UniXNU/READTEST.BIN'])
            self.assertEqual(out,(d/'READTEST.BIN').read_bytes());self.run_model(image,True)
            self.run_model(image,False,1);self.run_model(image,False,4);self.run_model(image,False,0,'budget')

    def test_corrupt_bpb_fat_chain_directory_and_file_bounds(self):
        for mode in ('signature','sector','spc','total','fat-size','root','mirror','loop','bad','short','extra','size','duplicate','dir-loop'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as td:
                image,entry,chain,put,offset,size,efi=self.fixture(Path(td))
                with image.open('r+b') as f:
                    if mode=='signature':f.seek(510);f.write(b'xx')
                    if mode=='sector':f.seek(11);f.write(struct.pack('<H',4096))
                    if mode=='spc':f.seek(13);f.write(b'\x03')
                    if mode=='total':f.seek(32);f.write(struct.pack('<I',0xffffffff))
                    if mode=='fat-size':f.seek(36);f.write(struct.pack('<I',0xffffffff))
                    if mode=='root':f.seek(44);f.write(struct.pack('<I',0xffffff7))
                    if mode=='size':f.seek(entry+28);f.write(struct.pack('<I',8192))
                    if mode=='duplicate':f.seek(entry);e=f.read(32);f.seek(entry+32);f.write(e)
                    if mode=='dir-loop':
                        f.seek(offset(efi));f.write((b'\xe5'+bytes(31))*(size//32))
                if mode=='mirror':put(chain[0],0xfffffff,False)
                if mode=='loop':put(chain[0],chain[0])
                if mode=='bad':put(chain[0],0xffffff7)
                if mode=='short':put(chain[0],0xfffffff)
                if mode=='extra':put(chain[-1],100000);put(100000,0xfffffff)
                if mode=='dir-loop':put(efi,efi)
                self.run_model(image,False)

    def test_gpt_is_checked_before_esp_range_is_authorized(self):
        data=bytearray(24576);data[510:512]=b'\x55\xaa';data[450]=0xee
        h=memoryview(data)[4096:8192];h[:8]=b'EFI PART';struct.pack_into('<4I',h,8,0x10000,92,0,0)
        struct.pack_into('<4Q',h,24,1,499999,6,499994);struct.pack_into('<Q2I',h,72,2,32,128)
        table=memoryview(data)[8192:12288];table[:16]=b'T'*16;table[16:32]=b'U'*16
        struct.pack_into('<2Q',table,32,393216,450000);table[56:72]='userdata'.encode('utf-16-le')
        def seal():
            struct.pack_into('<I',h,88,zlib.crc32(table));struct.pack_into('<I',h,16,0);struct.pack_into('<I',h,16,zlib.crc32(h[:92]))
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'gpt.bin';seal();path.write_bytes(data);self.run_model(path,True,'gpt')
            for offset in (510,4096+16,8192):
                bad=bytearray(data);bad[offset]^=1;path.write_bytes(bad);self.run_model(path,False,'gpt')
            struct.pack_into('<Q',table,32,393215);seal();path.write_bytes(data);self.run_model(path,False,'gpt')

    def test_usb_evidence_matches_independent_image_chain_and_file_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);image,_,chain,*_=self.fixture(d);trace=self.run_model(image,True)
            blocks=[(int(a),b) for a,b in re.findall(r'BLOCK (\d+) ([0-9a-f]{8})',trace)]
            build='a'*40;pa=0x102000000;n=len(blocks)
            rows=[f'POLARIS_UFS_ESP build={build} state=ok error=9 count={n} writes={3*n} doorbells={n} gpt=1 smmu_unchanged=1 start=393216 blocks=32768 media_writes=0 smmu_writes=0 retained=1 pa={pa:016x}']
            with image.open('rb') as f:b=f.read(512)
            spc=b[13];res=struct.unpack_from('<H',b,14)[0];fat=struct.unpack_from('<I',b,36)[0];data=res+2*fat
            rows += [f'POLARIS_UFS_ESP_FAT sector_bytes=512 spc={spc} reserved={res} fats=2 fat_sectors={fat} total_sectors=262144 data_sector={data} root=2 clusters={(262144-data)//spc}',
                     f'POLARIS_UFS_ESP_FILE path=EFI/UniXNU/READTEST.BIN bytes=8193 clusters={len(chain)}']
            rows += [f'POLARIS_UFS_ESP_CHAIN n={i} cluster={c}' for i,c in enumerate(chain)]
            upiu=(b'\x21'+bytes(33)).hex();prdt=struct.pack('<QII',pa+8192,0,4095).hex()
            for i,(block,crc) in enumerate(blocks):
                rows += [f'POLARIS_UFS_ESP_CMD n={i} lba={393216+block} opcode=28 lun=00 blocks=1 state=ok ocs=0 response=00 status=00 flags=00 bytes=4096 residual=0 segment=0 sense_len=0 guards=1 stopped=1 polls=2 run=00000000 db=00000000 is=00000401 upiu={upiu}',
                         f'POLARIS_UFS_ESP_DMA n={i} data_pa={pa+8192:016x} prdt={prdt} crc32={crc}']
            payload=(d/'READTEST.BIN').read_bytes()
            rows += [f'POLARIS_UFS_ESP_DATA offset={at} hex={payload[at:at+32].hex()}' for at in range(0,8193,32)]
            rows += ['POLARIS_UFS_ESP_FAULT global_before=00000000 bank_before=00000400 global_after=00000000 bank_after=00000400','POLARIS_UFS_ESP_END']
            raw=('\n'.join(rows)+'\n').encode()
            prior=dict(source_commit=build,ufs_bounded_read_verified=True,primary_gpt_verified=True,
                       ufs_nop_dma={'pa':pa},gpt_partitions=[dict(name='userdata',first_lba=393216,bytes=128*1024*1024)])
            result=usb.verify_ufs_esp(build,raw,prior,image)
            self.assertTrue(result['ufs_fat32_file_verified']);self.assertFalse(result['ufs_filesystem_mount_verified'])
            self.assertEqual(result['ufs_esp_chain'],chain);self.assertLess(len(raw),131072)
            for broken in (raw.replace(build.encode(),b'b'*40),raw.replace(b'lba=393216',b'lba=425984'),
                raw.replace(b'guards=1',b'guards=0'),raw.replace(b'status=00',b'status=02'),
                raw.replace(b'crc32='+blocks[0][1].encode(),b'crc32=00000000'),
                raw.replace(b'offset=8192 hex='+payload[-1:].hex().encode(),b'offset=8192 hex=d3'),
                raw.replace(b'bank_after=00000400',b'bank_after=00000402'),raw[:-30]):
                with self.assertRaises(ValueError):usb.verify_ufs_esp(build,broken,prior,image)
            with self.assertRaises(ValueError):usb.verify_ufs_esp(build,raw,{**prior,'primary_gpt_verified':False},image)
