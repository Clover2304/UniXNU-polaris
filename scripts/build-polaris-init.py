#!/usr/bin/env python3
"""Cross-build our static ARM64 PID 1 with a native-4K ad-hoc CodeDirectory.

The explicit boot-owner CDHash names this one entitlement-free diagnostic.
No Apple executable, dynamic linker, platform identity or trust cache is used.
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
IDENTIFIER = b'org.unixnu.polaris.first-user\0'
SIGNATURE_FLAGS = 0x302  # CS_ADHOC | CS_HARD | CS_KILL; no privilege entitlements.
WANTED = {'_start', '_checkpoint', '_results', '_user_page_size', '_old_length', '_reboot_return'}


def load_checker():
    spec = importlib.util.spec_from_file_location('platform_check', ROOT / 'scripts/check-platform-kernel.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commands(data):
    if len(data) < 32 or struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 2):
        raise ValueError('expected our plain ARM64 executable')
    at = 32
    end = at + struct.unpack_from('<I', data, 20)[0]
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        kind, size = struct.unpack_from('<II', data, at)
        if size < 8 or size % 8 or at + size > min(len(data), end):
            raise ValueError('invalid load command bounds')
        yield kind, at, bytes(data[at:at + size])
        at += size
    if at != end:
        raise ValueError('load command size mismatch')


def inspect(data):
    """Independently verify the on-disk signature and the static entry contract."""
    signature = None
    entry = None
    code_offset = None
    for kind, at, raw in commands(data):
        if kind in (0xe, 0xc, 0x18, 0x80000018, 0x8000001f, 0x80000028):
            raise ValueError('fixture must not depend on dyld or a shared library')
        if kind == 5:
            if entry is not None or len(raw) != 288 or struct.unpack_from('<II', raw, 8) != (6, 68):
                raise ValueError('requires one ARM_THREAD_STATE64 UNIXTHREAD')
            entry = struct.unpack_from('<Q', raw, 16 + 32 * 8)[0]
        if kind == 0x19 and raw[8:24].rstrip(b'\0') == b'__TEXT':
            va, _, off, size = struct.unpack_from('<4Q', raw, 24)
            code_offset = (va, off, size)
        if kind == 0x1d:
            if signature is not None:
                raise ValueError('duplicate signature')
            signature = struct.unpack_from('<II', raw, 8)
    if signature is None or entry is None or code_offset is None or struct.unpack_from('<I', data, 24)[0] != 1:
        raise ValueError('missing static entry/signature contract')
    offset, size = signature
    if offset + size > len(data) or size < 108:
        raise ValueError('signature outside file')
    if struct.unpack_from('>5I', data, offset) != (0xfade0cc0, size, 1, 0, 20):
        raise ValueError('requires exactly one CodeDirectory; no entitlements')
    cd = data[offset + 20:offset + size]
    fields = struct.unpack_from('>9I4B', cd)
    magic, length, version, flags, hashoff, identoff, special, slots, limit, hashsize, hashtype, platform, page = fields
    if (magic, length, version, flags, identoff, special, limit, hashsize, hashtype, platform, page) != (
            0xfade0c02, len(cd), 0x20400, SIGNATURE_FLAGS, 88, 0, offset, 32, 2, 0, 12):
        raise ValueError('unexpected CodeDirectory flags or native-4K hash contract')
    if cd[88:hashoff] != IDENTIFIER or slots != (limit + 4095) // 4096 or hashoff + slots * 32 != len(cd):
        raise ValueError('invalid CodeDirectory identifier or hash bounds')
    for index in range(slots):
        actual = hashlib.sha256(data[index * 4096:min((index + 1) * 4096, limit)]).digest()
        if actual != cd[hashoff + index * 32:hashoff + (index + 1) * 32]:
            raise ValueError('CodeDirectory page hash mismatch')
    addresses = load_checker().symbols(data, WANTED)
    if entry != addresses['_start'] or not code_offset[0] <= entry < code_offset[0] + code_offset[2]:
        raise ValueError('static entry does not match the source symbol')
    return {'process_sha256': hashlib.sha256(data).hexdigest(), 'cdhash': hashlib.sha256(cd).hexdigest()[:40],
            'code_signature_page_bytes': 4096, 'signature_flags': flags, 'addresses': addresses,
            'code_file_offset': code_offset[1] + entry - code_offset[0], 'userland_booted': False,
            'expected_checkpoint': 'POLARIS_INIT_OK pid=1 pagesize=4096'}


def static_and_sign(unsigned):
    data = bytearray(unsigned)
    items = list(commands(data))
    if any(kind in (5, 0x1d, 0xc, 0x80000018, 0x8000001f) for kind, _, _ in items):
        raise ValueError('requires an unsigned own link without dylib dependencies')
    symbols = load_checker().symbols(data, WANTED)
    state = bytearray(272)
    struct.pack_into('<Q', state, 256, symbols['_start'])
    output = [raw for kind, _, raw in items if kind not in (0xe, 0x80000028)]
    output.append(struct.pack('<4I', 5, 288, 6, 68) + state)
    limit = (len(data) + 15) & ~15
    data.extend(bytes(limit - len(data)))
    slots = (limit + 4095) // 4096
    hashoff = 88 + len(IDENTIFIER)
    cdsize = hashoff + slots * 32
    sigsize = 20 + cdsize
    output.append(struct.pack('<4I', 0x1d, 16, limit, sigsize))
    text_bytes = None
    first_section = len(data)
    for index, raw in enumerate(output):
        if struct.unpack_from('<I', raw)[0] != 0x19:
            continue
        name = raw[8:24].rstrip(b'\0')
        if name == b'__TEXT':
            text_bytes = struct.unpack_from('<Q', raw, 48)[0]
        for section in range(struct.unpack_from('<I', raw, 64)[0]):
            position = struct.unpack_from('<I', raw, 72 + section * 80 + 48)[0]
            if position:
                first_section = min(first_section, position)
        if name == b'__LINKEDIT':
            raw = bytearray(raw)
            off = struct.unpack_from('<Q', raw, 40)[0]
            struct.pack_into('<Q', raw, 32, (limit + sigsize - off + 4095) & ~4095)
            struct.pack_into('<Q', raw, 48, limit + sigsize - off)
            output[index] = raw
    if text_bytes is None:
        raise ValueError('missing text segment')
    header = b''.join(output)
    if 32 + len(header) > first_section:
        raise ValueError('insufficient load-command padding')
    old_size = struct.unpack_from('<I', data, 20)[0]
    erase = max(len(header), old_size)
    data[32:32 + erase] = bytes(erase)
    data[32:32 + len(header)] = header
    struct.pack_into('<III', data, 16, len(output), len(header), 1)
    cd = struct.pack('>9I4B4I4Q', 0xfade0c02, cdsize, 0x20400, SIGNATURE_FLAGS, hashoff, 88, 0,
                     slots, limit, 32, 2, 0, 12, 0, 0, 0, 0, 0, 0, text_bytes, 1) + IDENTIFIER
    cd += b''.join(hashlib.sha256(data[off:min(off + 4096, limit)]).digest() for off in range(0, limit, 4096))
    return bytes(data) + struct.pack('>5I', 0xfade0cc0, sigsize, 1, 0, 20) + cd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/polaris-init')
    args = parser.parse_args()
    clang = shutil.which('clang')
    linker = shutil.which('ld64.lld') or next((str(p) for p in sorted(Path('/usr/lib').glob('llvm-*/bin/ld64.lld'), reverse=True)), None)
    if not clang or not linker:
        parser.error('requires clang and ld64.lld (LLVM LLD)')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([clang, '--target=arm64-apple-ios17.0', '-c', ROOT / 'scripts/fixtures/polaris-init.S', '-o', out / 'init.o'], check=True)
    subprocess.run([linker, '-arch', 'arm64', '-e', '_start', '-platform_version', 'ios', '17.0', '17.0',
                    '-no_pie', '-no_adhoc_codesign', '-headerpad', '0x400', '-o', out / 'unsigned.macho', out / 'init.o'], check=True)
    image = static_and_sign((out / 'unsigned.macho').read_bytes())
    report = inspect(image)
    (out / 'launchd.macho').write_bytes(image)
    ramdisk = image + bytes((-len(image)) % 4096)
    (out / 'root.img').write_bytes(ramdisk)
    report.update(ramdisk_sha256=hashlib.sha256(ramdisk).hexdigest(), ramdisk_bytes=len(ramdisk),
                  source_sha256=hashlib.sha256((ROOT / 'scripts/fixtures/polaris-init.S').read_bytes()).hexdigest())
    (out / 'init.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{out}: signed own static PID 1, native-4K hashes, CDHash {report["cdhash"]}')


if __name__ == '__main__':
    main()
