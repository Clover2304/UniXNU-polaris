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
