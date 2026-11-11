#!/usr/bin/env python3
"""Compile the actual Darwin 23 CodeDirectory page-size gate on Linux or macOS.

This validates only the extracted gate, not the complete signature validator,
integrity-cache implementation, entitlements, or an Apple executable.
"""
import argparse,hashlib,json,re,shutil,subprocess
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True,help='actual patched bsd/kern/ubc_subr.c')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();source=a.source.read_text()
    signature='cs_validate_codedirectory(const CS_CodeDirectory *cd, size_t length)'
    if signature not in source:p.error('actual CodeDirectory validator not found')
    body=source[source.index(signature):]
    m=re.search(r'\tif \(\(cd->pageSize != PAGE_SHIFT_4K\).*?\n\t\}',body,re.S)
    if not m or 'XNU_NATIVE_CS_HASH_CACHE' not in m[0] or len(m[0])>2048:p.error('expected bounded native 4K gate not found')
    compiler=shutil.which('clang')
    if not compiler:p.error('clang is required')
    a.out.mkdir(parents=True,exist_ok=False)
    text='''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#define EBADEXEC 85
#define PAGE_SHIFT_4K 12
#define PAGE_SHIFT NATIVE_KERNEL_SHIFT
struct gate {uint8_t pageSize;};
int main(int argc,char **argv){
 if(argc!=2)return 2;
 struct gate value={(uint8_t)strtoul(argv[1],0,10)};const struct gate *cd=&value;
'''+m[0]+'\nreturn 0;\n}\n'
    c=a.out/'actual-gate.c';c.write_text(text);cases=[]
    for cache in (0,1):
        for kernel in (12,14):
            binary=a.out/f'gate-{kernel}-{cache}'
            subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror',f'-DNATIVE_KERNEL_SHIFT={kernel}',
                f'-DXNU_NATIVE_CS_HASH_CACHE={cache}',str(c),'-o',str(binary)],check=True,capture_output=True,timeout=30)
            for signing in (12,14):
                r=subprocess.run([str(binary),str(signing)],capture_output=True,text=True,timeout=5)
                accepted=r.returncode==0;expected=signing==12 or kernel==14 or cache==1
                if r.returncode not in (0,85) or accepted!=expected:raise RuntimeError('actual signature gate diverges from the expected matrix')
                cases.append(dict(kernel_page_shift=kernel,signature_page_shift=signing,integrity_cache_enabled=bool(cache),accepted=accepted))
    report=dict(passed=True,scope='extracted actual CodeDirectory page-size gate only',source_sha256=hashlib.sha256(a.source.read_bytes()).hexdigest(),
        gate_sha256=hashlib.sha256(m[0].encode()).hexdigest(),cases=cases,complete_signature_validator_verified=False,
        apple_binary_executed=False,hardware_verified=False,kernel_configuration_changed=False)
    (a.out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('PASS: eight actual gate cases; no Apple execution or complete signing qualification')

if __name__=='__main__':main()
