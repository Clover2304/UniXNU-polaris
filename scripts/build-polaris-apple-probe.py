#!/usr/bin/env python3
"""Package two unchanged, pinned original files for an opt-in execution probe.

Inputs remain local; this builds a RAM-root fixture, not a complete iPadOS image.
Exact hashes refer to the audited iPad7,1 17.7.11 / 21H461 inputs. Symbol and
entry observations follow pinned UniXNU build-target-dyld-fixture.py.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    'sbin/launchd': '8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25',
    'usr/lib/dyld': 'f8df3c58fae6e409db740df22c98aa699aee110b73523541a63f1bd5e23cf705',
}
CDHASHES = ['1d52c69747dffc4b1937d241bdbc9cee3b22df18', 'b479f90e05545314cb843410c3d98ef84d926710']
TRUST_SHA = 'da88460e6e513438d5307e5c526f76497a74754476a24da29f239f0c90120fc2'
SYMBOLS = {'_mach_init', '_vm_page_size', '_vm_page_shift', '_vm_kernel_page_shift', '_vm_page_mask',
           '__ZN5dyld4L7prepareERNS_4APIsEPKN5dyld313MachOAnalyzerE'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--volume', type=Path, required=True)
    p.add_argument('--static-trustcache', type=Path, required=True, help='Unwrapped original version-2 module')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    files = {}
    for path, expected in PINS.items():
        data = (a.volume / path).read_bytes()
        if sha(data) != expected:
            p.error('requires the exact audited original ' + path)
        files[path] = data
    tc = a.static_trustcache.read_bytes()
    if (sha(tc) != TRUST_SHA or struct.unpack_from('<I', tc)[0] != 2 or
            len(tc) != 24 + struct.unpack_from('<I', tc, 20)[0] * 24):
        p.error('requires the unchanged original static trust-cache module')
    entries = [tc[i:i+20] for i in range(24, len(tc), 24)]
    if any(entries.count(bytes.fromhex(h)) != 1 for h in CDHASHES):
        p.error('original CDHashes must occur uniquely in the original module')
    dyld = files['usr/lib/dyld']
    at, entry = 32, None
    for _ in range(struct.unpack_from('<I', dyld, 16)[0]):
        command, size = struct.unpack_from('<II', dyld, at)
        if command == 5:
            if struct.unpack_from('<II', dyld, at + 8) != (6, 68):
                p.error('unexpected original dyld thread state')
            entry = struct.unpack_from('<Q', dyld, at + 16 + 32*8)[0]
        at += size
    if entry is None:
        p.error('original dyld has no entry')
    branch = struct.unpack_from('<I', dyld, entry + 16)[0]
    if branch & 0xfc000000 != 0x14000000:
        p.error('original dyld startup branch differs')
    immediate = branch & 0x3ffffff
    if immediate & 0x2000000:
        immediate -= 0x4000000
    startup = entry + 16 + immediate*4
    nm = shutil.which('llvm-nm')
    command = [nm, '--defined-only', '--no-demangle'] if nm else ['xcrun', 'nm', '-U']
    output = subprocess.check_output([*command, a.volume / 'usr/lib/dyld'], text=True)
    symbols = {name: int(va, 16) for va, name in re.findall(r'^([0-9a-fA-F]+)\s+\S\s+(\S+)$', output, re.M)
               if name in SYMBOLS}
    if symbols.keys() != SYMBOLS:
        p.error('missing original dyld symbols')
    spec = importlib.util.spec_from_file_location('tree', ROOT / 'scripts/build-polaris-process.py')
    tree = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tree)
    root = tree.encode_tree([
        (0, tree.DIR, 'dev', b''), (0, tree.DIR, 'sbin', b''),
        (0, tree.DIR, 'usr', b''), (3, tree.DIR, 'lib', b''),
        (0, tree.DIR, 'tmp', b''),
        (2, tree.EXEC, 'launchd', files['sbin/launchd']),
        (4, tree.EXEC, 'dyld', dyld),
        (0, tree.RAM, 'probe.bin', bytes(4096)),
    ])
    # Refuse reuse so a later control cannot silently overwrite positive inputs.
    a.out.mkdir(parents=True, exist_ok=False)
    (a.out / 'root.img').write_bytes(root)
    (a.out / 'launchd').write_bytes(files['sbin/launchd'])
    (a.out / 'dyld').write_bytes(dyld)
    report = {
        'target': 'iPad7,1 / 17.7.11 / 21H461',
        'upstream_fixture_revision': '37987aea410691df3c1e0e146a1bfd6127932149',
        'files': [{'path': path, 'bytes': len(data), 'sha256': sha(data)} for path, data in files.items()],
        'original_cdhashes': CDHASHES, 'root_sha256': sha(root), 'root_bytes': len(root),
        'tree_entries': tree.decode_tree(root),
        'original_static_module_sha256': sha(tc),
        'dyld_preferred_entry': entry, 'dyld_startup_checkpoint': startup+4,
        'dyld_symbols': symbols,
        'boot_args': 'rd=md0 polaris-apple-probe=1 polaris-process=1 polaris-io=1',
        'authorization': 'explicit-owner two-original-CDHash diagnostic; no AMFI, trust-runtime or entitlement grant',
        'trust_cache_membership_checked_offline_only': True,
        'original_files_modified': False, 'apple_trust_chain_verified': False,
        'apple_userland_executed': False, 'phone_accessed': False,
    }
    (a.out / 'inputs.json').write_text(json.dumps(report, indent=2)+'\n')
    print('Packaged pinned original launchd/dyld:', len(root), 'bytes; no phone access')


if __name__ == '__main__':
    main()
