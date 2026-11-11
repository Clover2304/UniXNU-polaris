#!/usr/bin/env python3
"""Compile a platform archive using the actual Darwin 23 XNU compile record.

Used inside the assembled UniXNU tree. Runs the recorded compiler and xcrun
ar; writes only build/<component>/. Does not access a device.
"""
import argparse
import json
import os
import re
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=['ArmDTPlatform', 'ArmCpu'])
    args = parser.parse_args()
    out = ROOT / 'build' / {'ArmCpu': 'armcpu', 'ArmDTPlatform': 'armdt-platform'}[args.component]
    recipe = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
    record = json.loads(recipe.read_text().strip().rstrip(','))
    source = ROOT / 'drivers' / args.component / (args.component + '.cpp')
    destination = out / (args.component + '.o')
    out.mkdir(parents=True, exist_ok=True)
    command = []
    arguments = record['arguments']
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source))
        elif value in ('-o','-MF','-MT','-MJ'):
            if value=='-o':command.extend(['-o', str(destination)])
            index += 1
        elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W'):
            command.append(value)
        index += 1
    command.extend(['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-Wno-c99-designator', '-Wno-reorder-init-list',
                    '-Wno-deprecated-enum-enum-conversion',
                    '-I', str(ROOT / '.cache/xnu-work/osfmk')])
    (out / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True)
    (out / 'compile.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit(result.stdout + result.stderr)
    data = destination.read_bytes()
    if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 1):
        raise SystemExit('Expected a plain ARM64 Mach-O object')
    offset = 32
    deployment = None
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        cmd, length = struct.unpack_from('<II', data, offset)
        if cmd == 0x32:
            deployment = struct.unpack_from('<II', data, offset + 8)
        offset += length
    if deployment != (2, 0x110000):
        raise SystemExit(f'Expected iOS 17 deployment, got {deployment}')
    archive = out / ('lib' + args.component + '.a')
    archive.unlink(missing_ok=True)
    objects=[destination]
    if args.component=='ArmCpu':
        usb_source=ROOT/'drivers/PolarisUsb/PolarisUsb.cpp';usb_obj=out/'PolarisUsb.o'
        build_id=os.environ.get('GITHUB_SHA','local-unqualified')
        if build_id!='local-unqualified' and not re.fullmatch('[0-9a-f]{40}',build_id):
            raise SystemExit('Invalid USB diagnostic build identity')
        usb_command=[str(usb_source) if item==str(source) else str(usb_obj) if item==str(destination) else item for item in command]
        usb_command+=['-mgeneral-regs-only','-DPOLARIS_USB_BUILD_ID="'+build_id+'"']
        result=subprocess.run(usb_command,cwd=record['directory'],capture_output=True,text=True)
        (out/'usb-compile.log').write_text(result.stdout+result.stderr)
        if result.returncode:raise SystemExit(result.stdout+result.stderr)
        objects.append(usb_obj)
    subprocess.run(['xcrun', 'ar', 'rcs', archive, *objects], check=True)
    print(f'{args.component}: ARM64/iOS 17 archive built')


if __name__ == '__main__':
    main()
