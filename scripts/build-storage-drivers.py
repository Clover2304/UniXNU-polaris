#!/usr/bin/env python3
"""Build existing storage/AES archives using measured original base bindings.

This is a compiler/binding step only. It requires a previously observed original
storage class baseline; the new composed image, crypto operations and real disk
I/O remain unqualified until separate QEMU checks pass.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

ROOT=Path(__file__).resolve().parents[1]
ORIGINAL_SHA='f18b4df865405f2a287b84977d7aca745e056d656d9fc9df1662970ce2969de7'
STORAGE_REVISION='e45fbe30a7879e0b16e26dddb78538f1b00e0bff'


def symbol_table(data):
    if struct.unpack_from('<4I',data)!=(0xfeedfacf,0x100000c,0,1):
        raise ValueError('Expected ordinary ARM64 object')
    at=32;sections=[];symbol_command=None;deployment=None
    for _ in range(struct.unpack_from('<I',data,16)[0]):
        cmd,size=struct.unpack_from('<II',data,at)
        if cmd==0x32:deployment=struct.unpack_from('<II',data,at+8)
        if cmd==2:symbol_command=struct.unpack_from('<4I',data,at+8)
        if cmd==0x19:
            count=struct.unpack_from('<I',data,at+64)[0]
            sections += [at+72+i*80 for i in range(count)]
        at+=size
    if deployment!=(2,0x110000) or symbol_command is None:
        raise ValueError('Missing iOS 17 object provenance')
    sy,count,st,_=symbol_command;symbols=[]
    for i in range(count):
        ix,kind,_,_,value=struct.unpack_from('<IBBHQ',data,sy+i*16)
        symbols.append((data[st+ix:data.index(b'\0',st+ix)].decode(),value,kind))
    return symbols,sections


def vtable_methods(data,component):
    symbols,sections=symbol_table(data)
    table=next(value for name,value,kind in symbols if name=='__ZTV'+str(len(component))+component)
    relocations={}
    for at in sections:
        va,size=struct.unpack_from('<QQ',data,at+32)
        if va<=table<va+size:
            ro,rc=struct.unpack_from('<II',data,at+56)
            for i in range(rc):
                address,word=struct.unpack_from('<II',data,ro+i*8)
                if word>>27&1 and word>>28==0 and word>>25&3==3:
                    relocations[va+address]=symbols[word&0xffffff][0]
    methods=[];at=table+16
    while at in relocations:
        methods.append(relocations[at]);at+=8
    return methods,symbols


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--bindings',type=Path,required=True)
    a=parser.parse_args();a.source=a.source.resolve();a.bindings=a.bindings.resolve()
    if subprocess.check_output(['git','-C',a.source,'rev-parse','HEAD'],text=True).strip()!=STORAGE_REVISION:
        raise SystemExit('Unexpected storage header source')
    qualification=json.loads(a.bindings.read_text())
    if not qualification.get('source_storage_abi_qualified') or qualification['original_sha256']!=ORIGINAL_SHA or qualification['slide']!=-0x1000000:
        raise SystemExit('First qualify original storage dispatch and live class metadata')
    record=json.loads((ROOT/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
    include=ROOT/'build/storage-driver-inputs/include/IOKit/storage';include.mkdir(parents=True,exist_ok=True)
    for header in a.source.glob('*.h'):shutil.copyfile(header,include/header.name)
    sdk=Path(subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip())
    shutil.copyfile(sdk/'usr/include/TargetConditionals.h',include.parents[1]/'TargetConditionals.h')
    for component,label,count in [('NativeAES','native-aes',181),('NativeVirtioBlock','native-virtio-block',190)]:
        out=ROOT/'build'/label;out.mkdir(parents=True,exist_ok=True)
        source=ROOT/'drivers'/component/(component+'.cpp');obj=out/(component+'.o')
        command=[];args=record['arguments'];i=0
        while i<len(args):
            value=args[i]
            if value==record['file']:command.append(str(source))
            elif value in ('-o','-MF','-MT','-MJ'):
                if value=='-o':command+=['-o',str(obj)]
                i+=1
            elif value=='-Xclang' and args[i+1].startswith('-W'):i+=1
            elif not value.startswith('-W') and value not in ('-MD','-MP'):command.append(value)
            i+=1
        command+=['-I',str(include.parents[1]),'-I',str(ROOT/'.cache/xnu-work/osfmk'),
            '-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-deprecated-declarations',
            '-Wno-c99-designator','-Wno-reorder-init-list','-Wno-deprecated-enum-enum-conversion']
        (out/'compile-command.json').write_text(json.dumps(command,indent=2)+'\n')
        result=subprocess.run(command,cwd=record['directory'],capture_output=True,text=True)
        (out/'compile.log').write_text(result.stdout+result.stderr)
        if result.returncode:raise SystemExit((result.stdout+result.stderr)[-5000:])
        data=obj.read_bytes();methods,symbols=vtable_methods(data,component)
        if len(methods)!=count or '___cxa_pure_virtual' in methods:
            raise SystemExit('Unexpected concrete '+component+' dispatch')
        if component=='NativeAES':
            expected=qualification['aes_reference_methods']
            if len(expected)!=181:
                raise SystemExit('Incomplete original AES dispatch reference')
            for slot,(actual,wanted) in enumerate(zip(methods,expected)):
                if actual.replace('9NativeAES','16IOAESAccelerator')!=wanted:
                    raise SystemExit('AES dispatch mismatch at '+str(slot))
        if component=='NativeVirtioBlock':
            expected=qualification['storage_reference_methods']
            if len(expected)!=190:raise SystemExit('Incomplete qualified reference table')
            all_names=list(dict.fromkeys(methods+expected));demangled=subprocess.check_output(
                ['xcrun','c++filt'],input='\n'.join(n[1:] for n in all_names)+'\n',text=True).splitlines()
            readable=dict(zip(all_names,demangled))
            for slot,(actual,wanted) in enumerate(zip(methods,expected)):
                own=actual.startswith(('__ZN17NativeVirtioBlock','__ZNK17NativeVirtioBlock'))
                if wanted=='___cxa_pure_virtual':
                    if not own:raise SystemExit('Required storage method unimplemented')
                elif actual!=wanted and (not own or (slot not in (0,1) and readable[actual].split('::',1)[1]!=readable[wanted].split('::',1)[1])):
                    raise SystemExit('Storage dispatch mismatch at '+str(slot))
        token='IOAESAccelerator' if component=='NativeAES' else 'IOBlockStorageDevice'
        allowed=qualification['aes_bindings' if component=='NativeAES' else 'storage_bindings']
        undefined=[name for name,value,kind in symbols if kind==1 and token in name]
        if any(name not in allowed for name in undefined):
            raise SystemExit('Unqualified original import: '+str([n for n in undefined if n not in allowed]))
        if component=='NativeVirtioBlock' and any(name.endswith(('IOBlockStorageDeviceD0Ev','IOBlockStorageDeviceD1Ev')) for name in undefined):
            raise SystemExit('Abstract complete/deleting base destructor cannot be called')
        assembly=out/'original-bindings.S'
        assembly.write_text(''.join('.globl '+name+'\n.set '+name+', '+hex(int(allowed[name]))+'\n' for name in sorted(undefined)))
        subprocess.run(['xcrun','clang','-target','arm64-apple-ios17.0','-Werror','-Wno-incompatible-sysroot','-c',assembly,'-o',out/'original-bindings.o'],check=True)
        archive=out/('lib'+component+'.a');archive.unlink(missing_ok=True)
        subprocess.run(['xcrun','ar','rcs',archive,obj,out/'original-bindings.o'],check=True)
        report=dict(component=component,object_sha256=hashlib.sha256(data).hexdigest(),archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            qualification_sha256=hashlib.sha256(a.bindings.read_bytes()).hexdigest(),virtual_methods_checked=count,
            methods=methods,bindings={name:int(allowed[name]) for name in undefined},
            source_storage_abi_baseline=qualification['candidate_sha256'],original_sha256=ORIGINAL_SHA,
            new_composed_image_qualified=False,crypto_operations_tested=False,disk_read_tested=False,hardware_tested=False)
        (out/'bindings.json').write_text(json.dumps(report,indent=2)+'\n')
        print(component+': original-import archive compiled; new runtime remains unqualified')


if __name__=='__main__':main()
