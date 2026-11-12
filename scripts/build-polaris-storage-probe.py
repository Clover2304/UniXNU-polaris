#!/usr/bin/env python3.11
"""Own Linux-built process for read-only two-volume APFS component experiments."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

BASE=Path(__file__).resolve().parents[1]


def module(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-root',type=Path,required=True,help='Private extracted original OS tree; never published')
    parser.add_argument('--os-device',required=True,help='Same-entry OS volume UUID/BSD name observed in this guest')
    parser.add_argument('--cache-device',required=True,help='Same-entry SystemCryptex UUID/BSD name observed in this guest')
    parser.add_argument('--discovery',action='store_true')
    parser.add_argument('--policy-off',action='store_true')
    parser.add_argument('--out',type=Path,required=True)
    a=parser.parse_args()
    assert all(re.fullmatch(r'/dev/disk\d+s\d+',s) for s in [a.os_device,a.cache_device])
    a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=False)
    lines=['#define STORAGE_OS_DEVICE '+json.dumps(a.os_device),'#define STORAGE_CACHE_DEVICE '+json.dumps(a.cache_device)]
    for name,rel,digest in [('launchd','sbin/launchd','8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25'),
        ('dyld','usr/lib/dyld','f8df3c58fae6e409db740df22c98aa699aee110b73523541a63f1bd5e23cf705')]:
        data=(a.original_root/rel).read_bytes()
        assert hashlib.sha256(data).hexdigest()==digest
        lines.append('static const u8 expected_'+name+'[256]={'+','.join(hex(n) for n in data[:256])+'};')
    (a.out/'storage-input.h').write_text('\n'.join(lines)+'\n')
    scripts=BASE/'scripts';own=module('own',scripts/'build-polaris-init.py')
    tree=module('tree',scripts/'build-polaris-process.py');checker=module('checker',scripts/'check-platform-kernel.py')
    clang=shutil.which('clang');linker=shutil.which('ld64.lld') or str(next(Path('/usr/lib').glob('llvm-*/bin/ld64.lld')))
    subprocess.run([clang,'--target=arm64-apple-ios17.0','-O2','-ffreestanding','-fno-builtin',
        '-fno-stack-protector','-mgeneral-regs-only','-I',scripts/'fixtures','-I',a.out,
        *(['-DSTORAGE_DISCOVERY=1'] if a.discovery else []),'-c',scripts/'fixtures/polaris-storage-probe.c','-o',a.out/'init.o'],check=True)
    subprocess.run([linker,'-arch','arm64','-e','_start','-platform_version','ios','17.0','17.0',
        '-no_pie','-no_adhoc_codesign','-headerpad','0x400','-o',a.out/'unsigned.macho',a.out/'init.o'],check=True)
    image=own.static_and_sign((a.out/'unsigned.macho').read_bytes());identity=own.inspect(image)
    addresses=checker.symbols(image,{'_start','_storage_start','_storage_results','_storage_checkpoint','_storage_resident_checkpoint'})
    root=tree.encode_tree([(0,tree.DIR,'dev',b''),(0,tree.DIR,'sbin',b''),(0,tree.DIR,'tmp',b''),
        (2,tree.EXEC,'launchd',image),(0,tree.RAM,'probe.bin',bytes(4096))])
    (a.out/'launchd.macho').write_bytes(image);(a.out/'root.img').write_bytes(root)
    report=dict(init=identity,addresses=addresses,discovery_only=a.discovery,cache_policy_off=a.policy_off,os_device=a.os_device,cache_device=a.cache_device,
        root_sha256=hashlib.sha256(root).hexdigest(),root_bytes=len(root),tree_entries=tree.decode_tree(root),
        boot_args='polaris-io=1 polaris-process=1 '+('' if a.policy_off else 'polaris-cache-probe=1 ')+'polaris-init-cdhash='+identity['cdhash'],
        filesystem_mounted=False,original_program_code_executed=False,hardware_tested=False)
    (a.out/'storage.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Built own offline storage process:',a.out,'root bytes',len(root))


if __name__=='__main__':main()
