"""Fixed-file mapping and cancelled request lifetime, with real fragmented FAT."""
from pathlib import Path
import shutil,subprocess,tempfile,unittest
import test_ufs_fat
ROOT=Path(__file__).resolve().parents[1]
class UfsNodeTests(unittest.TestCase):
    def test_queue_and_fresh_physical_reads_on_fragmented_file(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);binary=d/'node-test'
            result=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                '-I',ROOT/'drivers/PolarisUsb',ROOT/'tests/ufs-node-model.cpp','-o',binary],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([binary],check=True,capture_output=True)
            image,entry,chain,put,offset,size,efi=test_ufs_fat.UfsFatTests.fixture(self,d)
            subprocess.run([binary,image],check=True,capture_output=True)
            with image.open('r+b') as f:
                f.seek(offset(chain[4]));data=f.read(size);f.seek(offset(100000));f.write(data)
            put(chain[3],100000);put(100000,chain[5]);put(chain[4],0)
            subprocess.run([binary,image],check=True,capture_output=True)

    def test_hardware_evidence_rejects_model_stale_crc_wrong_order_and_writes(self):
        import importlib.util,re,struct,zlib
        spec=importlib.util.spec_from_file_location('node_usb',ROOT/'scripts/check-polaris-usb.py')
        usb=importlib.util.module_from_spec(spec);spec.loader.exec_module(usb)
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);image,entry,chain,put,offset,size,efi=test_ufs_fat.UfsFatTests.fixture(self,d)
            with image.open('rb') as f:b=f.read(512)
            data=struct.unpack_from('<H',b,14)[0]+2*struct.unpack_from('<I',b,36)[0]
            requests=[(0,4096),(4096,4096),(8192,1),(1023,2050),(4095,2050),(8192,1),(0,4096),(0,4096),(37,16)]
            # Independent byte-address expansion: coalesce consecutive physical pages per request.
            blocks=[]
            for start,length in requests:
                pages=[(data*512+(chain[off//size]-2)*size+off%size)//4096 for off in range(start,start+length)]
                blocks.extend(page for i,page in enumerate(pages) if i==0 or page!=pages[i-1])
            n=len(blocks);build='a'*40;pa=0x102000000
            rows=[f'POLARIS_UFS_NODE build={build} state=ok mode=hardware ready=1 failed=0 error=0 queue=0 submitted=9 completed=9 cancelled=0 busy=0 errors=0 count={n} writes={3*n} doorbells={n} smmu_unchanged=1 media_writes=0 smmu_writes=0 pa={pa:016x}',
                  'POLARIS_UFS_NODE_VFS opens=2 reads=12 bytes=20502 denied=2 path=/dev/polaris-esp-file size=8193']
            upiu=(b'\x21'+bytes(33)).hex();prdt=struct.pack('<QII',pa+8192,0,4095).hex()
            with image.open('rb') as f:
                for i,block in enumerate(blocks):
                    f.seek(block*4096);crc=zlib.crc32(f.read(4096))
                    rows += [f'POLARIS_UFS_NODE_CMD n={i} lba={393216+block} opcode=28 lun=00 blocks=1 state=ok ocs=0 response=00 status=00 flags=00 bytes=4096 residual=0 segment=0 sense_len=0 guards=1 stopped=1 polls=2 run=00000000 db=00000000 is=00000401 upiu={upiu}',
                             f'POLARIS_UFS_NODE_DMA n={i} data_pa={pa+8192:016x} prdt={prdt} crc32={crc:08x}']
            rows+=['POLARIS_UFS_NODE_FAULT global_before=00000000 bank_before=00000400 global_after=00000000 bank_after=00000400','POLARIS_UFS_NODE_END']
            raw=('\n'.join(rows)+'\n').encode();log=b'POLARIS_UFS_NODE_OK bytes=8193 cross=3 repeat=2 eof=OK readonly=OK reopen=OK'
            prior=dict(source_commit=build,ufs_fat32_file_verified=True,ufs_nop_dma={'pa':pa},ufs_esp_chain=chain,ufs_esp_geometry={'spc':b[13],'data_sector':data})
            result=usb.verify_ufs_node(build,raw,prior,image,log)
            self.assertTrue(result['ufs_node_verified']);self.assertFalse(result['ufs_filesystem_mount_verified'])
            for broken in (raw.replace(b'mode=hardware',b'mode=model'),raw.replace(b'submitted=9',b'submitted=8'),
                           raw.replace(b'media_writes=0',b'media_writes=1'),raw.replace(b'denied=2',b'denied=0'),
                           raw.replace(b'guards=1',b'guards=0'),raw.replace(b'status=00',b'status=02'),
                           raw.replace(b'bank_after=00000400',b'bank_after=00000402'),
                           re.sub(rb'crc32=[0-9a-f]{8}',b'crc32=00000000',raw),raw[:-10]):
                with self.assertRaises(ValueError):usb.verify_ufs_node(build,broken,prior,image,log)
            with self.assertRaises(ValueError):usb.verify_ufs_node(build,raw,prior,image,b'')
            with self.assertRaises(ValueError):usb.verify_ufs_node(build,raw,{**prior,'ufs_fat32_file_verified':False},image,log)
