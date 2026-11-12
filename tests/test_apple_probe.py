"""Compile the actual opt-in original-input predicate; no phone or Apple files."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AppleProbeTests(unittest.TestCase):
    def test_actual_policy_requires_explicit_mode_pid1_valid_signature_and_exact_hash(self):
        patch = (ROOT / 'kernel-overrides/xnu/polaris-target-dyld.patch').read_text()
        section = patch.split('+++ b/bsd/kern/polaris_apple_probe.h\n', 1)[1].split('\n--- ', 1)[0]
        header = '\n'.join(s[1:] for s in section.splitlines() if s.startswith('+'))
        harness = '''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#define CS_CDHASH_LEN 20
#define CS_VALID 1
#define CS_ADHOC 2
struct cs_blob { unsigned csb_flags; unsigned char csb_cdhash[20]; };
''' + header + '''
int main(void) {
 const unsigned char originals[2][20] = {
  {0x1d,0x52,0xc6,0x97,0x47,0xdf,0xfc,0x4b,0x19,0x37,0xd2,0x41,0xbd,0xbc,0x9c,0xee,0x3b,0x22,0xdf,0x18},
  {0xb4,0x79,0xf9,0x0e,0x05,0x54,0x53,0x14,0xcb,0x84,0x34,0x10,0xc3,0xd9,0x8e,0xf8,0x4d,0x92,0x67,0x10}
 };
 for(unsigned kind=0;kind<2;kind++) {
  struct cs_blob blob={.csb_flags=CS_VALID|CS_ADHOC};
  memcpy(blob.csb_cdhash,originals[kind],20);
  const struct cs_blob before=blob;
  assert(polaris_apple_blob_kind(&blob,1,1)==kind+1);
  assert(!memcmp(&blob,&before,sizeof(blob)));
  const int modes[]={-1,0,2,256};
  for(unsigned i=0;i<4;i++)assert(polaris_apple_blob_kind(&blob,1,modes[i])==0);
  const int pids[]={-1,0,2,3,1024};
  for(unsigned i=0;i<5;i++)assert(polaris_apple_blob_kind(&blob,pids[i],1)==0);
  for(unsigned flag=0;flag<3;flag++) {
   blob.csb_flags=flag;assert(polaris_apple_blob_kind(&blob,1,1)==0);
  }
  blob.csb_flags=CS_VALID|CS_ADHOC;
  for(unsigned byte=0;byte<20;byte++)for(unsigned bit=0;bit<8;bit++) {
   blob.csb_cdhash[byte]^=(1u<<bit);
   assert(polaris_apple_blob_kind(&blob,1,1)==0);
   blob.csb_cdhash[byte]^=(1u<<bit);
  }
 }
 assert(polaris_apple_blob_kind(0,1,1)==0);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary = root / 'policy.c', root / 'policy'
            source.write_text(harness)
            subprocess.run([shutil.which('clang'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', source, '-o', binary],
                           check=True, capture_output=True)
            subprocess.run([binary], check=True, capture_output=True)
