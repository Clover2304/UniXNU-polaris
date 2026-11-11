"""Actual signed programs, root-table parser bounds and scoped exec admission."""
import importlib.util
import json
import re
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('process_builder',ROOT/'scripts/build-polaris-process.py')
PROCESS=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(PROCESS)

def patch_header(name):
    text=(ROOT/'kernel-overrides/xnu/polaris-process.patch').read_text()
    chunk=text.split('+++ b/'+name+'\n',1)[1].split('\n--- ',1)[0]
    return '\n'.join(line[1:] for line in chunk.splitlines() if line.startswith('+'))+'\n'

class ProcessFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.out=Path(cls.temp.name)
        subprocess.run([sys.executable,ROOT/'scripts/build-polaris-process.py','--out',cls.out],check=True,capture_output=True)

    def test_named_directories_and_independently_signed_programs(self):
        report=PROCESS.inspect_bundle(self.out)
        files={x['path']:x for x in report['entries']}
        self.assertEqual(len(files),10)
        self.assertEqual(files['/bin']['flags'],PROCESS.DIR)
        self.assertEqual(files['/bin/probe']['parent'],3)
        self.assertEqual(files['/sbin/launchd']['parent'],2)
        self.assertEqual(files['/probe.bin']['flags'],PROCESS.RAM)
        self.assertNotEqual(report['init']['cdhash'],report['child']['cdhash'])
        self.assertTrue(report['init']['c_stack_entry_aligned'])
        self.assertTrue(report['child']['c_stack_entry_aligned'])
        parent=set(report['init']['addresses'].values())|set(report['init']['io_symbols'].values())|set(report['init']['process_symbols'].values())
        child=set(report['child']['probe_symbols'].values())
        self.assertFalse(parent&child)

    def test_actual_arm64_assembly_reserves_zero_executable_text(self):
        patch=(ROOT/'kernel-overrides/xnu/polaris-process.patch').read_text()
        added='\n'.join(line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
        source=re.search(r'__asm__\(.*?\);',added,re.S).group(0)+'\nvoid fixture(void){}\n'
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary);(out/'reserve.c').write_text(source)
            subprocess.run([shutil.which('clang'),'--target=arm64-apple-ios17.0','-c',out/'reserve.c','-o',out/'reserve.o'],check=True,capture_output=True)
            data=(out/'reserve.o').read_bytes();at=32;found=False
            for _ in range(struct.unpack_from('<I',data,16)[0]):
                command,size=struct.unpack_from('<II',data,at)
                if command==0x19:
                    for i in range(struct.unpack_from('<I',data,at+64)[0]):
                        section=at+72+i*80
                        if data[section:section+16].rstrip(b'\0')==b'__text':
                            length,offset=struct.unpack_from('<QI',data,section+40)
                            self.assertGreaterEqual(length,8192);self.assertEqual(data[offset:offset+8192],bytes(8192));found=True
                at+=size
            self.assertTrue(found)

    def test_child_code_corruption_cannot_be_packaged_as_a_valid_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary)
            for name in ('launchd.macho','probe.macho','root.img'):shutil.copyfile(self.out/name,out/name)
            report=PROCESS.inspect_bundle(out);data=bytearray((out/'probe.macho').read_bytes())
            data[report['child']['code_file_offset']]^=1;(out/'probe.macho').write_bytes(data)
            with self.assertRaisesRegex(ValueError,'page hash mismatch'):PROCESS.inspect_bundle(out)

    def test_real_kernel_table_validator_rejects_malformed_extents_and_names(self):
        disk=(self.out/'root.img').read_bytes();cases=[(disk,0)]
        def mutate(offset,fmt,value):
            data=bytearray(disk);struct.pack_into(fmt,data,offset,value);cases.append((data,-1))
        mutate(12,'<I',0);mutate(12,'<I',17);mutate(16,'<Q',len(disk)+4096);mutate(24,'<Q',1)
        mutate(64,'<I',1) # forward/self parent
        mutate(64+4,'<I',3) # directory plus executable
        mutate(64+8,'<Q',4096) # directory with file data
        mutate(64+5*64+8,'<Q',4097) # unaligned executable extent
        mutate(64+6*64+8,'<Q',4096) # overlapping executable extents
        mutate(64+6*64+16,'<Q',(1<<64)-1)
        mutate(64+9*64+4,'<I',6) # mutable executable
        mutate(64+9*64+16,'<Q',4095)
        mutate(64+7*64,'<I',6) # regular file as directory parent
        mutate(64+4,'<I',8)
        for name in (b'../bad',b'.',b'..',b'A'*32,b'dev\0junk'):
            data=bytearray(disk);data[64+24:64+56]=name.ljust(32,b'\0');cases.append((data,-1))
        data=bytearray(disk);data[64+64+24:64+64+56]=data[64+24:64+56];cases.append((data,-1))
        cases.append((disk[:4095],-1))
        for data,expected in cases:
            if expected:
                with self.assertRaises(ValueError):PROCESS.decode_tree(data)
            else:self.assertEqual(len(PROCESS.decode_tree(data)),10)
        harness='''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
'''+patch_header('bsd/miscfs/mockfs/mockfs_polaris_tree.h')+r'''
int main(int argc,char **argv){
 for(int i=1;i<argc;i++){
  FILE*f=fopen(argv[i],"rb");if(!f)return 2;fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);
  unsigned char *p=calloc(1,(size_t)n+4096);if(!p)return 3;
  if(fread(p,1,(size_t)n,f)!=(size_t)n)return 4;fclose(f);
  printf("%d\n",n<4096?-1:polaris_tree_validate((const void*)p,(uint64_t)n));free(p);
 }
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary);(out/'table.c').write_text(harness);paths=[]
            for i,(data,_) in enumerate(cases):
                path=out/f'{i}.bin';path.write_bytes(data);paths.append(path)
            subprocess.run([shutil.which('clang'),'-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',out/'table.c','-o',out/'table'],check=True,capture_output=True)
            result=subprocess.run([out/'table',*paths],check=True,capture_output=True,text=True)
            self.assertEqual([int(x) for x in result.stdout.split()],[x[1] for x in cases])

    def test_real_child_policy_does_not_grant_general_unsigned_execution(self):
        harness=r'''
#define _GNU_SOURCE
#include <stdint.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
typedef int errno_t;
#define CS_CDHASH_LEN 20
#define CS_ADHOC 2
#define CS_HARD 0x100
#define CS_KILL 0x200
struct cs_blob { unsigned csb_flags;int csb_platform_binary,csb_platform_path;void *csb_entitlements,*csb_entitlements_blob,*csb_der_entitlements_blob;unsigned char csb_cdhash[20]; };
'''+patch_header('bsd/kern/polaris_process_policy.h')+r'''
int main(void){
 struct cs_blob b={.csb_flags=0x302};const char *zero="0000000000000000000000000000000000000000";
 assert(polaris_process_blob_policy(&b,1,0,0,zero)==0);
 assert(polaris_process_blob_policy(&b,2,1,1,zero)==0);
 assert(polaris_process_blob_policy(&b,2,1,0,zero)==EPERM);
 assert(polaris_process_blob_policy(&b,3,2,1,zero)==EPERM);
 assert(polaris_process_blob_policy(&b,0,1,1,zero)==EPERM);
 assert(polaris_process_blob_policy(&b,2,1,1,NULL)==EPERM);
 assert(polaris_process_blob_policy(&b,2,1,1,"0")==EPERM);
 assert(polaris_process_blob_policy(&b,2,1,1,"0000000000000000000000000000000000000001")==EPERM);
 b.csb_platform_binary=1;assert(polaris_process_blob_policy(&b,2,1,1,zero)==EPERM);b.csb_platform_binary=0;
 b.csb_entitlements=(void*)1;assert(polaris_process_blob_policy(&b,2,1,1,zero)==EPERM);b.csb_entitlements=NULL;
 b.csb_flags=2;assert(polaris_process_blob_policy(&b,2,1,1,zero)==EPERM);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            out=Path(temporary);(out/'policy.c').write_text(harness)
            subprocess.run([shutil.which('clang'),'-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',out/'policy.c','-o',out/'policy'],check=True,capture_output=True)
            subprocess.run([out/'policy'],check=True,capture_output=True)
