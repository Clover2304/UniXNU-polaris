"""Exercise the real source patch's PID/hash/entitlements admission predicate."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InitPolicyTests(unittest.TestCase):
    def test_compiled_source_policy_refuses_other_hashes_privileges_and_processes(self):
        patch = (ROOT / 'kernel-overrides/xnu/polaris-init-entitlements.patch').read_text()
        added = '\n'.join(line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
        begin = added.index('volatile uint32_t polaris_init_signature_accepted;')
        end = added.index('\n\tif (amfi == NULL)', begin)
        policy = added[begin:end]
        harness = r'''
#define _GNU_SOURCE
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
typedef int errno_t;
#define CS_CDHASH_LEN 20
#define CS_ADHOC 2
#define CS_HARD 0x100
#define CS_KILL 0x200
struct cs_blob {
 unsigned int csb_flags;
 int csb_platform_binary, csb_platform_path;
 void *csb_entitlements, *csb_entitlements_blob, *csb_der_entitlements_blob;
 unsigned char csb_cdhash[20];
};
static int pid = 1, present = 1;
static const char *configured;
static int proc_selfpid(void) { return pid; }
static int PE_parse_boot_arg_str(const char *name, char *out, int size) {
 assert(!strcmp(name, "polaris-init-cdhash"));
 if (!present) return 0;
 snprintf(out, (size_t)size, "%s", configured);
 return 1;
}
'''+policy+r'''
int main(void) {
 const char *correct="00112233445566778899aabbccddeeff00112233";
 struct cs_blob blob = {.csb_flags=CS_ADHOC|CS_HARD|CS_KILL};
 for (unsigned i=0;i<20;i++) { unsigned value; assert(sscanf(correct+i*2,"%2x",&value)==1);blob.csb_cdhash[i]=(unsigned char)value; }
 configured=correct;
 assert(polaris_init_signature_policy(&blob)==0);
 assert(polaris_init_signature_accepted==1);
 const char *bad[]={"", "0", "00112233445566778899AABBCCDDEEFF00112233", "00112233445566778899aabbccddeeff001122330", "00112233445566778899aabbccddeeff00112232", "00112233445566778899aabbccddeeff0011223g"};
 for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);i++){configured=bad[i];assert(polaris_init_signature_policy(&blob)==EPERM);}
 configured=correct;present=0;assert(polaris_init_signature_policy(&blob)==EPERM);present=1;
 pid=2;assert(polaris_init_signature_policy(&blob)==EPERM);pid=1;
 unsigned flags[]={CS_ADHOC,CS_ADHOC|CS_HARD,CS_ADHOC|CS_KILL,CS_HARD|CS_KILL};
 for(unsigned i=0;i<sizeof(flags)/sizeof(flags[0]);i++){blob.csb_flags=flags[i];assert(polaris_init_signature_policy(&blob)==EPERM);}
 blob.csb_flags=CS_ADHOC|CS_HARD|CS_KILL;
 blob.csb_platform_binary=1;assert(polaris_init_signature_policy(&blob)==EPERM);blob.csb_platform_binary=0;
 blob.csb_platform_path=1;assert(polaris_init_signature_policy(&blob)==EPERM);blob.csb_platform_path=0;
 blob.csb_entitlements=(void*)1;assert(polaris_init_signature_policy(&blob)==EPERM);blob.csb_entitlements=NULL;
 blob.csb_entitlements_blob=(void*)1;assert(polaris_init_signature_policy(&blob)==EPERM);blob.csb_entitlements_blob=NULL;
 blob.csb_der_entitlements_blob=(void*)1;assert(polaris_init_signature_policy(&blob)==EPERM);
 assert(polaris_init_signature_accepted==1);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / 'policy.c').write_text(harness)
            subprocess.run([shutil.which('clang'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', work / 'policy.c', '-o', work / 'policy'], check=True, capture_output=True)
            subprocess.run([work / 'policy'], check=True, capture_output=True)
