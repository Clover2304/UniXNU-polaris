#!/usr/bin/env python3
"""Build a signed owner probe and package one unchanged original 16K-signed file.

Reuses pinned UniXNU's original-cache-file experiment on the source-only kernel.
The fixture does not execute original cache code or load the complete dyld cache.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CACHE_SHA = '3d923c404fe519184a99042b60a4cb3d37247dcb8b070242ed3e670d4d523d04'
CACHE_CDHASH = '9d037a68ad6490e103e44e47086e279a2923b04e'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache-file', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    data = a.cache_file.read_bytes()
    if hashlib.sha256(data).hexdigest() != CACHE_SHA:
        p.error('requires the exact audited 21H461 dyld_shared_cache_arm64.02')
    sigoff, sigsize = struct.unpack_from('<QQ', data, 40)
    assert (sigoff, sigsize, len(data)) == (32768, 16384, 49152)
    superblob = data[sigoff:sigoff+sigsize]
    directories = []
    for i in range(struct.unpack_from('>I', superblob, 8)[0]):
        slot, offset = struct.unpack_from('>II', superblob, 12+i*8)
        magic, size = struct.unpack_from('>II', superblob, offset)
        if magic == 0xfade0c02:
            directories.append(superblob[offset:offset+size])
    assert len(directories) == 1
    cd = directories[0]
    assert hashlib.sha256(cd).hexdigest()[:40] == CACHE_CDHASH
    assert cd[39] == 14 and struct.unpack_from('>I', cd, 32)[0] == 32768
    hashoff = struct.unpack_from('>I', cd, 16)[0]
    for i in range(2):
        assert hashlib.sha256(data[i*16384:(i+1)*16384]).digest() == cd[hashoff+i*32:hashoff+(i+1)*32]
    audit = json.loads((ROOT / 'research/apple-dyld-cache-audit.json').read_text())
    assert any(x['cdhash'] == CACHE_CDHASH for x in audit['trustcache_matches'])
    checksum = 0
    for offset in range(0, 32768, 4096):
        checksum ^= struct.unpack_from('<Q', data, offset)[0]
    clang = shutil.which('clang')
    linker = shutil.which('ld64.lld') or next((str(q) for q in sorted(Path('/usr/lib').glob('llvm-*/bin/ld64.lld'), reverse=True)), None)
    if not clang or not linker:
        p.error('requires clang and ld64.lld')
    a.out.mkdir(parents=True, exist_ok=False)
    source = ROOT / 'scripts/fixtures/polaris-cache-probe.c'
    subprocess.run([clang, '--target=arm64-apple-ios17.0', '-O2', '-ffreestanding', '-fno-builtin',
                    '-fno-stack-protector', '-mgeneral-regs-only',
                    '-DPOLARIS_CACHE_EXPECTED_XOR='+hex(checksum)+'UL', '-c', source, '-o', a.out / 'init.o'], check=True)
    subprocess.run([linker, '-arch', 'arm64', '-e', '_start', '-platform_version', 'ios', '17.0', '17.0',
                    '-no_pie', '-no_adhoc_codesign', '-headerpad', '0x400',
                    '-o', a.out / 'unsigned.macho', a.out / 'init.o'], check=True)
    own = module('own', ROOT / 'scripts/build-polaris-init.py')
    tree = module('tree', ROOT / 'scripts/build-polaris-process.py')
    checker = module('checker', ROOT / 'scripts/check-platform-kernel.py')
    image = own.static_and_sign((a.out / 'unsigned.macho').read_bytes())
    identity = own.inspect(image)
    names = {'_start', '_polaris_cache_start', '_cache_checkpoint', '_cache_resident_checkpoint', '_cache_results'}
    addresses = checker.symbols(image, names)
    entry = addresses['_start']
    for cmd, offset, raw in own.commands(image):
        if cmd == 0x19 and raw[8:24].rstrip(b'\0') == b'__TEXT':
            va, vmsize, fileoff, filesize = struct.unpack_from('<4Q', raw, 24)
            words = struct.unpack_from('<4I', image, fileoff+entry-va)
            assert words[:3] == (0x910003e0, 0x927cec09, 0x9100013f) and words[3]&0xfc000000 == 0x14000000
            displacement = words[3]&0x3ffffff
            if displacement&0x2000000:
                displacement -= 0x4000000
            assert entry+12+displacement*4 == addresses['_polaris_cache_start']
    root = tree.encode_tree([(0, tree.DIR, 'dev', b''), (0, tree.DIR, 'sbin', b''),
                             (0, tree.DIR, 'tmp', b''), (2, tree.EXEC, 'launchd', image),
                             (0, tree.EXEC, 'cache.bin', data), (0, tree.RAM, 'probe.bin', bytes(4096))])
    (a.out / 'launchd.macho').write_bytes(image)
    (a.out / 'root.img').write_bytes(root)
    identity.update(mode='cache-probe', c_stack_entry_aligned=True)
    report = {'init': identity, 'addresses': addresses, 'original_file_sha256': CACHE_SHA,
              'original_cdhash': CACHE_CDHASH, 'original_file_bytes': len(data),
              'signing_block_bytes': 16384, 'code_limit': 32768,
              'signature_offset': sigoff, 'signature_size': sigsize,
              'expected_page_word_xor': checksum, 'expected_native_pages': 8,
              'root_sha256': hashlib.sha256(root).hexdigest(), 'root_bytes': len(root),
              'tree_entries': tree.decode_tree(root),
              'boot_args': 'polaris-io=1 polaris-process=1 polaris-cache-probe=1 polaris-init-cdhash='+identity['cdhash'],
              'cache_code_executed': False, 'complete_dyld_cache_loaded': False,
              'hardware_tested': False, 'original_file_modified': False}
    (a.out / 'cache.json').write_text(json.dumps(report, indent=2)+'\n')
    print('Built original cache-file probe:', len(root), 'bytes; no phone access')


if __name__ == '__main__':
    main()
