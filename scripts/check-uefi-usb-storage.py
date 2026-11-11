#!/usr/bin/env python3
"""Verify a phone-exported ESP and optionally exercise a bounded marker roundtrip.

The write exercise restores the exact original ESP. It opens only the userdata
partition for writes, verifies its USB ancestry/GPT/geometry and original hash,
and changes at most 64 KiB inside the first 128 MiB. No mount or format occurs.
"""
import argparse
import fcntl
import hashlib
import json
import mmap
import os
from pathlib import Path
import secrets
import stat
import struct
import subprocess
import zlib

ESP_BYTES = 128 * 1024 * 1024
UNIT_BYTES = 59064188928
USB_ID = ('05f9', '1234')
BLKSSZGET = 0x1268
BLKGETSIZE64 = 0x80081272
BLKFLSBUF = 0x1261


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_exact(fd, offset, size):
    data = bytearray()
    while len(data) < size:
        chunk = os.pread(fd, min(1024 * 1024, size - len(data)), offset + len(data))
        require(chunk, 'short device read')
        data.extend(chunk)
    return bytes(data)


def geometry(fd):
    sector = struct.unpack('I', fcntl.ioctl(fd, BLKSSZGET, bytes(4)))[0]
    size = struct.unpack('Q', fcntl.ioctl(fd, BLKGETSIZE64, bytes(8)))[0]
    return sector, size


def userdata_gpt(reader, size, sector, partition_name='userdata', minimum_bytes=ESP_BYTES):
    """Validate both GPT headers/tables; return one named partition extent."""
    require(sector in (512, 4096) and size % sector == 0, 'invalid disk geometry')
    last = size // sector - 1

    def header(lba, alternate):
        raw = reader(lba * sector, sector)
        require(len(raw) == sector and raw[:8] == b'EFI PART', 'missing GPT header')
        revision, length, crc, reserved = struct.unpack_from('<4I', raw, 8)
        require(revision == 0x10000 and 92 <= length <= sector and not reserved, 'invalid GPT header fields')
        clean = bytearray(raw[:length]); clean[16:20] = bytes(4)
        require(zlib.crc32(clean) == crc, 'GPT header CRC mismatch')
        here, other, first, end = struct.unpack_from('<4Q', raw, 24)
        table, count, stride, table_crc = struct.unpack_from('<Q3I', raw, 72)
        require(here == lba and other == alternate and 2 <= first <= end < last, 'invalid GPT LBAs')
        require(1 <= count <= 4096 and 128 <= stride <= 1024 and stride % 128 == 0, 'invalid GPT entry geometry')
        extent = count * stride
        require(extent <= 4 * 1024 * 1024 and 1 < table and table * sector + extent <= size, 'GPT table out of bounds')
        if lba == 1:
            require(table * sector + extent <= first * sector, 'primary GPT overlaps usable area')
        else:
            require(table > end and table * sector + extent <= last * sector, 'backup GPT overlaps usable area')
        entries = reader(table * sector, extent)
        require(len(entries) == extent and zlib.crc32(entries) == table_crc, 'GPT table CRC mismatch')
        return raw[56:72], first, end, count, stride, entries

    primary = header(1, last)
    require(primary == header(last, 1), 'primary and backup GPT disagree')
    _, first, end, count, stride, entries = primary
    found = []; used = []
    for n in range(count):
        entry = entries[n * stride:(n + 1) * stride]
        if entry[:16] == bytes(16):
            continue
        lo, hi = struct.unpack_from('<2Q', entry, 32)
        require(first <= lo <= hi <= end and entry[16:32] != bytes(16), 'invalid partition extent')
        used.append((lo, hi))
        name = entry[56:128].decode('utf-16le').split('\0', 1)[0]
        if name == partition_name:
            found.append(dict(number=n + 1, offset=lo * sector, bytes=(hi - lo + 1) * sector))
    used.sort()
    require(all(a[1] < b[0] for a, b in zip(used, used[1:])), 'overlapping partitions')
    require(len(found) == 1 and found[0]['bytes'] >= minimum_bytes, 'named partition absent, too small or ambiguous')
    return found[0], sha(entries)


def inspect_device(path, partition_name='userdata', unit_bytes=UNIT_BYTES, minimum_bytes=ESP_BYTES):
    path = path.resolve()
    require(path.parent == Path('/dev') and stat.S_ISBLK(path.stat().st_mode), 'expected a block device in /dev')
    node = Path('/sys/class/block') / path.name
    require((node / 'partition').exists(), 'write target must be a partition')
    props = dict(line.split('=', 1) for line in (node / 'uevent').read_text().splitlines() if '=' in line)
    require(props.get('PARTNAME') == partition_name, 'wrong target partition name')
    parent = node.resolve().parent
    ancestors = [p for p in parent.parents if (p / 'idVendor').exists() and (p / 'idProduct').exists()]
    require(ancestors, 'missing USB ancestry')
    usb = ancestors[0]
    require(tuple((usb / key).read_text().strip() for key in ('idVendor', 'idProduct')) == USB_ID, 'wrong USB device')
    rdev = path.stat().st_rdev
    protected = {(os.major(rdev), os.minor(rdev))}
    for sibling in [parent, *parent.iterdir()]:
        if (sibling / 'dev').exists():
            protected.add(tuple(map(int, (sibling / 'dev').read_text().strip().split(':'))))
    mounts = [tuple(map(int, line.split()[2].split(':'))) for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    require(not any(x in protected for x in mounts), 'disk or sibling partition mounted')
    holders = [p for p in [parent, *parent.iterdir()] if (p / 'holders').is_dir() and any((p / 'holders').iterdir())]
    require(not holders, 'disk is held by another block mapping')
    with (Path('/dev') / parent.name).open('rb', buffering=0) as f:
        sector, size = geometry(f.fileno())
        require(size == unit_bytes, 'unit capacity differs from verified phone')
        part, table_sha = userdata_gpt(lambda offset, length: read_exact(f.fileno(), offset, length), size, sector, partition_name, minimum_bytes)
    require(part['number'] == int((node / 'partition').read_text()), 'GPT partition number mismatch')
    require(part['offset'] == int((node / 'start').read_text()) * 512, 'partition offset mismatch')
    require(part['bytes'] == int((node / 'size').read_text()) * 512, 'partition size mismatch')
    return dict(device=str(path), rdev=rdev, sysfs=str(node.resolve()),
                usb_bus=(usb / 'busnum').read_text().strip(), usb_device=(usb / 'devnum').read_text().strip(),
                sector_bytes=sector, unit_bytes=size, partition=part, gpt_table_sha256=table_sha)


def changed_ranges(before, after, sector, maximum_bytes=65536):
    require(len(before) == len(after) == ESP_BYTES, 'unexpected ESP size')
    require(sector in (512, 4096) and ESP_BYTES % sector == 0, 'unaligned ESP')
    require(0 < maximum_bytes <= 96 * 1024 * 1024, 'invalid write limit')
    changes = [(at, after[at:at + sector]) for at in range(0, len(before), sector)
               if before[at:at + sector] != after[at:at + sector]]
    require(changes and len(changes) * sector <= maximum_bytes, 'ESP update exceeds write limit')
    return changes


def direct_image(path, size):
    """Use aligned O_DIRECT reads so a readback cannot be satisfied by page cache."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECT)
    try:
        with mmap.mmap(-1, 1024 * 1024) as buffer:
            chunks = []
            for at in range(0, size, len(buffer)):
                length = min(len(buffer), size - at)
                view = memoryview(buffer)[:length]
                try:
                    require(os.preadv(fd, [view], at) == length, 'short direct read')
                    chunks.append(bytes(view))
                finally:
                    view.release()
            return b''.join(chunks)
    finally:
        os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', type=Path, required=True)
    p.add_argument('--expected-image', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--exercise-write', action='store_true')
    a = p.parse_args()
    expected = a.expected_image.read_bytes()
    require(len(expected) == ESP_BYTES, 'expected image must be 128 MiB')
    identity = inspect_device(a.device)
    before = direct_image(a.device, ESP_BYTES)
    require(before == expected, 'physical ESP differs from verified expected image')
    a.out.mkdir(parents=True, exist_ok=False)
    (a.out / 'before.img').write_bytes(before)
    report = dict(passed=False, identity=identity, esp_sha256=sha(before),
                  read_only_match_verified=True, original_restored=False,
                  marker_write_verified=False, partition_writes=False)
    def save():
        (a.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    save()
    if not a.exercise_write:
        report.update(passed=True); save()
        print('Read-only USB ESP matches the verified 128 MiB image')
        return
    marker = ('POLARIS_UEFI_USB_ROUNDTRIP ' + secrets.token_hex(24) + '\n').encode()
    (a.out / 'USBPROBE.TXT').write_bytes(marker)
    candidate = a.out / 'candidate.img'; candidate.write_bytes(before)
    subprocess.run(['mcopy', '-i', str(candidate), str(a.out / 'USBPROBE.TXT'), '::/EFI/UniXNU/USBPROBE.TXT'], check=True, capture_output=True)
    subprocess.run(['fsck.fat', '-n', str(candidate)], check=True, capture_output=True)
    after = candidate.read_bytes(); changes = changed_ranges(before, after, identity['sector_bytes'])
    report.update(marker_sha256=sha(marker), candidate_sha256=sha(after),
                  changed_bytes=len(changes) * identity['sector_bytes'],
                  changed_offsets=[at for at, _ in changes])
    save()
    require(inspect_device(a.device) == identity, 'device identity changed')
    fd = os.open(a.device, os.O_RDWR | os.O_CLOEXEC | os.O_EXCL)
    touched = []
    try:
        require(os.fstat(fd).st_rdev == identity['rdev'] and geometry(fd) == (identity['sector_bytes'], identity['partition']['bytes']), 'opened device changed')
        require(read_exact(fd, 0, ESP_BYTES) == before, 'ESP changed since preflight')
        try:
            for at, data in changes:
                touched.append(at)
                report['partition_writes'] = True; save()
                require(os.pwrite(fd, data, at) == len(data), 'short marker write')
            os.fsync(fd); fcntl.ioctl(fd, BLKFLSBUF)
            observed = direct_image(a.device, ESP_BYTES)
            require(observed == after, 'direct marker readback mismatch')
            (a.out / 'marker-readback.img').write_bytes(observed)
            subprocess.run(['mcopy', '-i', str(a.out / 'marker-readback.img'), '::/EFI/UniXNU/USBPROBE.TXT', str(a.out / 'marker-readback.txt')], check=True, capture_output=True)
            require((a.out / 'marker-readback.txt').read_bytes() == marker, 'marker file readback mismatch')
            report['marker_write_verified'] = True; save()
        finally:
            if touched:
                require(inspect_device(a.device) == identity, 'identity changed before restore')
                for at in touched:
                    data = before[at:at + identity['sector_bytes']]
                    require(os.pwrite(fd, data, at) == len(data), 'short restoration write')
                os.fsync(fd); fcntl.ioctl(fd, BLKFLSBUF)
                require(direct_image(a.device, ESP_BYTES) == before, 'original ESP restoration mismatch')
                report['original_restored'] = True; save()
    finally:
        os.close(fd)
    report['passed'] = report['marker_write_verified'] and report['original_restored']; save()
    print('USB marker file write/direct readback passed; original ESP restored byte-for-byte')


if __name__ == '__main__':
    main()
