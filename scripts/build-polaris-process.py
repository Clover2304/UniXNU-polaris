#!/usr/bin/env python3
"""Build our two signed programs and a bounded named RAM-root container."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
ROOT=Path(__file__).resolve().parents[1]
MAX_ENTRIES=16
DIR,EXEC,RAM=1,2,4
MESSAGE=b'POLARIS MULTIFILE ROOT\n'
TARGET=b'/bin/probe\n'
HEADER=struct.Struct('<8sIIQ3Q16s')
ENTRY=struct.Struct('<IIQQ32sQ')

def fixture_module():
    s=importlib.util.spec_from_file_location('process_fixture',ROOT/'scripts/build-polaris-init.py')
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def decode_tree(data):
    if len(data)<4096 or len(data)>32*1024*1024 or len(data)%4096:
        raise ValueError('invalid container size')
    magic,version,count,size,a,b,c,padding=HEADER.unpack_from(data)
    if magic!=b'N4KROOT\0' or version!=3 or not 0<count<=MAX_ENTRIES or size!=len(data) or a or b or c or any(padding):
        raise ValueError('invalid tree header')
    result=[];end=4096;names=set()
    for i in range(count):
        parent,flags,offset,length,raw,reserved=ENTRY.unpack_from(data,64+i*64)
        name=raw.split(b'\0')[0]
        if (parent>i or reserved or flags&~7 or not 0<len(name)<32 or name in (b'.',b'..') or
            raw!=name+bytes(32-len(name)) or any(c not in b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-' for c in name)):
            raise ValueError('invalid name, parent or flags')
        if parent and result[parent-1]['flags']!=DIR:
            raise ValueError('parent is not a directory')
        if (parent,name) in names:raise ValueError('duplicate name')
        names.add((parent,name))
        if flags&DIR:
            if flags!=DIR or offset or length:raise ValueError('invalid directory extent')
        else:
            padded=(length+4095)&~4095
            if (flags&(EXEC|RAM)==EXEC|RAM or not length or offset%4096 or offset<end or
                offset>len(data) or length>len(data)-offset or padded>len(data)-offset or flags&RAM and length!=4096):
                raise ValueError('invalid file extent')
            end=offset+padded
        path=(result[parent-1]['path'] if parent else '')+'/'+name.decode('ascii')
        result.append(dict(parent=parent,flags=flags,offset=offset,size=length,name=name.decode('ascii'),path=path))
    return result

def tree_files(init,child,seed):
    return [(0,DIR,'dev',b''),(0,DIR,'sbin',b''),(0,DIR,'bin',b''),(0,DIR,'etc',b''),(0,DIR,'tmp',b''),
            (2,EXEC,'launchd',init),(3,EXEC,'probe',child),(4,0,'message',MESSAGE),
            (4,0,'target',TARGET),(0,RAM,'probe.bin',seed+bytes(4096-len(seed)))]

def encode_tree(files):
    if len(files)>MAX_ENTRIES:raise ValueError('too many entries')
    result=bytearray(4096)
    for i,(parent,flags,name,content) in enumerate(files):
        offset=len(result) if not flags&DIR else 0
        ENTRY.pack_into(result,64+i*64,parent,flags,offset,len(content),name.encode().ljust(32,b'\0'),0)
        if not flags&DIR:result.extend(content+bytes((-len(content))%4096))
    HEADER.pack_into(result,0,b'N4KROOT\0',3,len(files),len(result),0,0,0,bytes(16))
    decode_tree(result)
    return bytes(result)

def inspect_bundle(directory):
    directory=Path(directory);f=fixture_module()
    init=(directory/'launchd.macho').read_bytes();child=(directory/'probe.macho').read_bytes()
    a,b=f.inspect(init),f.inspect(child)
    if a['mode']!='process' or b['mode']!='probe' or not a['c_stack_entry_aligned'] or not b['c_stack_entry_aligned']:
        raise ValueError('requires both signed programs with verified C startup alignment')
    # Separate addresses let observation distinguish parent and child without
    # changing their code or relying on an unqualified kernel debug transport.
    if not 0x100010000<=b['addresses']['_start']<0x100020000:
        raise ValueError('child has an unexpected link address')
    disk=(directory/'root.img').read_bytes();entries=decode_tree(disk)
    expected=encode_tree(tree_files(init,child,f.IO_SEED))
    if disk!=expected:raise ValueError('container differs from verified programs and named data')
    return dict(init=a,child=b,tmp_enabled=bool(a['tmp_symbols']),stress_enabled=bool(a['stress_symbols']),ufs_node_enabled=bool(a['ufs_node_symbols']),ramdisk_bytes=len(disk),ramdisk_sha256=hashlib.sha256(disk).hexdigest(),
                entries=entries,apple_userland_booted=False,interactive_input_qualified=False,
                expected_checkpoint='POLARIS_PROC_OK child=N exit=42 reaped=1 cow=OK; POLARIS_PROC_LIVE tick changes')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,default=ROOT/'out/polaris-process');p.add_argument('--tmp',action='store_true');p.add_argument('--stress',action='store_true');p.add_argument('--ufs-node',action='store_true');a=p.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);f=fixture_module()
    clang=shutil.which('clang');linker=shutil.which('ld64.lld') or next((str(p) for p in sorted(Path('/usr/lib').glob('llvm-*/bin/ld64.lld'),reverse=True)),None)
    if not clang or not linker:p.error('requires clang and ld64.lld')
    for label,source,extra in [('launchd','polaris-io-init.c',['-DPOLARIS_PROCESS_INIT=1']),('probe','polaris-probe.c',[])]:
        if a.tmp or a.stress:extra=extra+['-DPOLARIS_TMP_TEST=1']
        if a.stress:extra=extra+['-DPOLARIS_STRESS_TEST=1']
        if a.ufs_node and label=='launchd':extra=extra+['-DPOLARIS_UFS_NODE_TEST=1']
        subprocess.run([clang,'--target=arm64-apple-ios17.0','-O2','-ffreestanding','-fno-builtin','-fno-stack-protector','-mgeneral-regs-only',*extra,
                        '-c',ROOT/'scripts/fixtures'/source,'-o',out/(label+'.o')],check=True)
        placement=['-sectalign','__TEXT','__text','0x10000'] if label=='probe' else []
        unsigned=out/(label+'-unsigned.macho')
        subprocess.run([linker,'-arch','arm64','-e','_start','-platform_version','ios','17.0','17.0','-no_pie','-no_adhoc_codesign',
                        '-headerpad','0x400',*placement,'-o',unsigned,out/(label+'.o')],check=True)
        (out/(label+'.macho')).write_bytes(f.static_and_sign(unsigned.read_bytes()))
    disk=encode_tree(tree_files((out/'launchd.macho').read_bytes(),(out/'probe.macho').read_bytes(),f.IO_SEED))
    (out/'root.img').write_bytes(disk);report=inspect_bundle(out)
    (out/'process.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'{out}: signed PID 1 and probe, {len(report["entries"])} named entries, {len(disk)} bytes')

if __name__=='__main__':main()
