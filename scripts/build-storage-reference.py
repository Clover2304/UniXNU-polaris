#!/usr/bin/env python3
"""Compile public IOStorageFamily annotations; never link them into the kernel.

Uses the actual configured XNU compile record, public source pin and generated
headers. The output describes reference layout only; original/live class ABI,
component initialization and disk I/O still require separate qualification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

STORAGE_REVISION = 'e45fbe30a7879e0b16e26dddb78538f1b00e0bff'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tree',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a = p.parse_args()
    a.tree=a.tree.resolve();a.source=a.source.resolve();a.out=a.out.resolve()
    revision=subprocess.check_output(['git','-C',a.source,'rev-parse','HEAD'],text=True).strip()
    if revision!=STORAGE_REVISION:
        raise SystemExit('Unexpected public IOStorageFamily revision')
    sys.path.insert(0,str(a.tree/'kits/NativePrelink'))
    import native_prelink as np
    a.out.mkdir(parents=True,exist_ok=False)
    include=a.out/'include/IOKit/storage';include.mkdir(parents=True)
    for header in a.source.glob('*.h'):
        shutil.copyfile(header,include/header.name)
    sdk=Path(subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip())
    conditions=sdk/'usr/include/TargetConditionals.h'
    shutil.copyfile(conditions,a.out/'include/TargetConditionals.h')
    objroot=a.tree/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    recipe=objroot/'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
    record=json.loads(recipe.read_text().strip().rstrip(','))
    obj=a.out/'IOBlockStorageDevice-reference.o'
    args=record['arguments'];command=[];index=0
    while index<len(args):
        value=args[index]
        if value==record['file']:
            command.append(str(a.source/'IOBlockStorageDevice.cpp'))
        elif value in ('-o','-MF','-MT','-MJ'):
            if value=='-o':command+=['-o',str(obj)]
            index+=1
        elif value=='-Xclang' and args[index+1].startswith('-W'):
            index+=1
        elif not value.startswith('-W') and value not in ('-MD','-MP'):
            command.append(value)
        index+=1
    command+=['-I',str(a.out/'include'),'-I',str(a.tree/'.cache/xnu-work/osfmk'),
        '-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-deprecated-declarations',
        '-Wno-c99-designator','-Wno-reorder-init-list',
        '-Wno-deprecated-enum-enum-conversion']
    (a.out/'compile-command.json').write_text(json.dumps(command,indent=2)+'\n')
    result=subprocess.run(command,cwd=record['directory'],capture_output=True,text=True)
    (a.out/'compile.log').write_text(result.stdout+result.stderr)
    if result.returncode:
        raise SystemExit((result.stdout+result.stderr)[-5000:])
    data=obj.read_bytes();image=np.macho(data)
    if image['kind']!=1:
        raise SystemExit('Expected ordinary ARM64 annotation object')
    at=next(at for command,at,size in image['commands'] if command==0x32)
    if struct.unpack_from('<II',data,at+8)!=(2,0x110000):
        raise SystemExit('Annotation must target iOS 17')
    dwarf=subprocess.check_output(['xcrun','dwarfdump','--name=IOBlockStorageDevice','--show-children',obj],text=True)
    (a.out/'storage-class.dwarf.txt').write_text(dwarf)
    size=re.search(r'DW_AT_name\s+\("IOBlockStorageDevice"\)[\s\S]{0,200}?DW_AT_byte_size\s+\((0x[0-9a-f]+)\)',dwarf)
    if not size or int(size[1],16)!=0x90:
        raise SystemExit('Public compiled storage class size differs')
    at=next(at for command,at,size in image['commands'] if command==2)
    sy,count,st,_=struct.unpack_from('<4I',data,at+8)
    symbols=[]
    for i in range(count):
        ix,kind,_,_,value=struct.unpack_from('<IBBHQ',data,sy+i*16)
        symbols.append(dict(name=data[st+ix:data.index(b'\0',st+ix)].decode(),kind=kind,value=value))
    table=next(s['value'] for s in symbols if s['name']=='__ZTV20IOBlockStorageDevice')
    end=min(s['value'] for s in symbols if not s['kind']&0xe0 and s['kind']&0xe==0xe and s['value']>table)
    relocations={}
    for segment in image['segments']:
        for section in segment['sections']:
            if section['va']<=table<section['va']+section['size']:
                ro,rc=struct.unpack_from('<II',data,section['command_offset']+56)
                for i in range(rc):
                    address,word=struct.unpack_from('<II',data,ro+i*8)
                    if word>>27&1 and word>>28==0 and word>>25&3==3:
                        relocations[section['va']+address]=symbols[word&0xffffff]['name']
    methods=[]
    for at in range(table+16,end,8):
        if at not in relocations:
            break
        methods.append(relocations[at])
    if len(methods)!=190 or methods.count('___cxa_pure_virtual')!=14:
        raise SystemExit('Public compiled storage dispatch shape differs')
    meta_obj=objroot/'libkern/DEVELOPMENT/OSMetaClass.cpo'
    meta_dwarf=subprocess.check_output(['xcrun','dwarfdump','--name=OSMetaClass','--show-children',meta_obj],text=True)
    (a.out/'metaclass.dwarf.txt').write_text(meta_dwarf)
    layout={}
    for member in meta_dwarf.split('DW_TAG_member')[1:]:
        name=re.search(r'DW_AT_name\s+\("([^\"]+)"\)',member)
        position=re.search(r'DW_AT_data_member_location\s+\((0x[0-9a-f]+)\)',member)
        if name and position and name[1] in ('superClassLink','className','classSize'):
            layout[name[1]]=int(position[1],16)
    if layout!=dict(superClassLink=16,className=24,classSize=32):
        raise SystemExit('Actual source metaclass layout differs')
    report=dict(source_commit=os.environ.get('GITHUB_SHA','local-unqualified'),public_source_revision=revision,
        public_source_sha256=sha(a.source/'IOBlockStorageDevice.cpp'),compile_record_sha256=sha(recipe),
        reference_object_sha256=sha(obj),reference_object_executed_or_linked=False,
        public_compiled_class_size=int(size[1],16),method_slots=len(methods),pure_virtual_slots=14,
        methods=[dict(slot=i,reference_name=name) for i,name in enumerate(methods)],
        compiled_metaclass_offsets=layout,metaclass_object_sha256=sha(meta_obj),
        platform_header_sha256=sha(conditions),platform_header_sdk_version=subprocess.check_output(
            ['xcrun','--sdk','macosx','--show-sdk-version'],text=True).strip(),
        original_target_abi_qualified=False,live_class_metadata_observed=False,
        components_loaded=False,filesystem_mounted=False,disk_read_tested=False,hardware_tested=False)
    (a.out/'reference-layout.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Public reference compiled: class 0x90, 190 slots / 14 pure virtual; live/original ABI remains unqualified')


if __name__=='__main__':
    main()
