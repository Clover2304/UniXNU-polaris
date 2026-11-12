"""Compile the actual owner-launcher admission predicates and refusal cases."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def header(patch, path):
    section = (ROOT / 'kernel-overrides/xnu' / patch).read_text().split(
        '+++ b/bsd/kern/' + path + '\n', 1)[1].split('\n--- ', 1)[0]
    return '\n'.join(line[1:] for line in section.splitlines() if line.startswith('+'))


class AppleLauncherTests(unittest.TestCase):
    def test_actual_predicate_requires_both_modes_exact_unprivileged_native4k_owner(self):
        source = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
typedef int errno_t;
#define CS_CDHASH_LEN 20
#define CS_VALID 1
#define CS_ADHOC 2
#define CS_HARD 0x100
#define CS_KILL 0x200
struct cs_blob {
 unsigned csb_flags, csb_hash_pageshift;
 int csb_platform_binary, csb_platform_path;
 void *csb_entitlements, *csb_entitlements_blob, *csb_der_entitlements_blob;
 unsigned char csb_cdhash[20];
};
''' + header('polaris-process.patch', 'polaris_process_policy.h') + '\n' + header(
            'polaris-target-dyld.patch', 'polaris_apple_launcher.h') + r'''
int main(void) {
 const char *owner="00112233445566778899aabbccddeeff00112233";
 struct cs_blob b={.csb_flags=CS_VALID|CS_ADHOC|CS_HARD|CS_KILL,.csb_hash_pageshift=12,
  .csb_cdhash={0x00,0x11,0x22,0x33,0x44,0x55,0x66,0x77,0x88,0x99,0xaa,0xbb,0xcc,0xdd,0xee,0xff,0x00,0x11,0x22,0x33}};
 const struct cs_blob original=b;
 assert(polaris_apple_launcher_blob(&b,1,1,1,owner));
 assert(!memcmp(&b,&original,sizeof(b)));
 for(int mode=-1;mode<4;mode++) if(mode!=1) {
  assert(!polaris_apple_launcher_blob(&b,1,mode,1,owner));
  assert(!polaris_apple_launcher_blob(&b,1,1,mode,owner));
 }
 for(int pid=-1;pid<5;pid++)if(pid!=1)assert(!polaris_apple_launcher_blob(&b,pid,1,1,owner));
 for(unsigned bit=0;bit<160;bit++) {
  b.csb_cdhash[bit/8]^=1u<<(bit%8);
  assert(!polaris_apple_launcher_blob(&b,1,1,1,owner));b=original;
 }
 const unsigned flags[]={CS_VALID,CS_ADHOC,CS_HARD,CS_KILL};
 for(unsigned i=0;i<4;i++){b.csb_flags^=flags[i];assert(!polaris_apple_launcher_blob(&b,1,1,1,owner));b=original;}
 const unsigned pages[]={0,11,13,14,16};
 for(unsigned i=0;i<5;i++){b.csb_hash_pageshift=pages[i];assert(!polaris_apple_launcher_blob(&b,1,1,1,owner));b=original;}
 b.csb_platform_binary=1;assert(!polaris_apple_launcher_blob(&b,1,1,1,owner));b=original;
 b.csb_platform_path=1;assert(!polaris_apple_launcher_blob(&b,1,1,1,owner));b=original;
 b.csb_entitlements=(void*)1;assert(!polaris_apple_launcher_blob(&b,1,1,1,owner));b=original;
 b.csb_entitlements_blob=(void*)1;assert(!polaris_apple_launcher_blob(&b,1,1,1,owner));b=original;
 b.csb_der_entitlements_blob=(void*)1;assert(!polaris_apple_launcher_blob(&b,1,1,1,owner));b=original;
 const char *bad[]={0,"","0","00112233445566778899AABBCCDDEEFF00112233","00112233445566778899aabbccddeeff001122330"};
 for(unsigned i=0;i<5;i++)assert(!polaris_apple_launcher_blob(&b,1,1,1,bad[i]));
 assert(!polaris_apple_launcher_blob(0,1,1,1,owner));
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'policy.c').write_text(source)
            subprocess.run([shutil.which('clang'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', work / 'policy.c', '-o', work / 'policy'],
                           check=True, capture_output=True)
            subprocess.run([work / 'policy'], check=True, capture_output=True)
