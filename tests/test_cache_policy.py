"""Actual cache-registration predicate against the audited original membership."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CachePolicyTests(unittest.TestCase):
    def test_original_members_need_pid1_explicit_mode_and_original_granule(self):
        patch = (ROOT / 'kernel-overrides/xnu/polaris-target-dyld.patch').read_text()
        section = patch.split('+++ b/bsd/kern/polaris_cache_policy.h\n', 1)[1].split('\n--- ', 1)[0]
        header = '\n'.join(line[1:] for line in section.splitlines() if line.startswith('+'))
        audit = json.loads((ROOT / 'research/apple-dyld-cache-audit.json').read_text())
        hashes = sorted({item['cdhash'] for item in audit['trustcache_matches']})
        self.assertEqual(len(hashes), 53)
        arrays = ',\n'.join('{' + ','.join('0x'+h[i:i+2] for i in range(0, 40, 2)) + '}' for h in hashes)
        source = '''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#define CS_CDHASH_LEN 20
#define CS_VALID 1
#define CS_ADHOC 2
struct cs_blob { unsigned csb_flags,csb_hash_pageshift; unsigned char csb_cdhash[20]; };
''' + header + '\nstatic const unsigned char audited[53][20] = {\n' + arrays + '\n};\n' + '''
int main(void) {
 for(unsigned row=0;row<53;row++) {
  struct cs_blob blob={.csb_flags=3,.csb_hash_pageshift=14};
  memcpy(blob.csb_cdhash,audited[row],20);
  const struct cs_blob before=blob;
  assert(polaris_cache_blob_member(&blob,1,1)==1);
  assert(!memcmp(&blob,&before,sizeof(blob)));
  const int pids[]={-1,0,2,3,1024},modes[]={-1,0,2,256};
  for(unsigned i=0;i<5;i++)assert(polaris_cache_blob_member(&blob,pids[i],1)==0);
  for(unsigned i=0;i<4;i++)assert(polaris_cache_blob_member(&blob,1,modes[i])==0);
  for(unsigned flag=0;flag<3;flag++) {
   blob.csb_flags=flag;assert(polaris_cache_blob_member(&blob,1,1)==0);
  }
  blob.csb_flags=3;
  for(unsigned page=0;page<=16;page++) {
   blob.csb_hash_pageshift=page;
   assert(polaris_cache_blob_member(&blob,1,1)==(page==14));
  }
  blob.csb_hash_pageshift=14;
  for(unsigned byte=0;byte<20;byte++)for(unsigned bit=0;bit<8;bit++) {
   blob.csb_cdhash[byte]^=1u<<bit;
   assert(polaris_cache_blob_member(&blob,1,1)==0);
   blob.csb_cdhash[byte]^=1u<<bit;
  }
 }
 struct cs_blob other={.csb_flags=3,.csb_hash_pageshift=14};
 assert(!polaris_cache_blob_member(&other,1,1));
 assert(!polaris_cache_blob_member(0,1,1));
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / 'policy.c').write_text(source)
            subprocess.run([shutil.which('clang'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', out / 'policy.c', '-o', out / 'policy'],
                           check=True, capture_output=True)
            subprocess.run([out / 'policy'], check=True, capture_output=True)
