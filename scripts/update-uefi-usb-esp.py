#!/usr/bin/env python3
"""Update EFI files through the firmware's USB storage export, with readback.

The default is a read-only preview. A CI-qualified ready directory supplies
the files; the current full ESP must match --expected-image. Writes are bounded
to userdata's first 128 MiB, and failures attempt restoration of changed blocks.
"""
import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tomllib

spec = importlib.util.spec_from_file_location('usb_storage_guard', Path(__file__).with_name('check-uefi-usb-storage.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def file_path(root, name):
    path = PurePosixPath(name)
    g.require(not path.is_absolute() and path.parts and path.parts[0] == 'EFI' and '..' not in path.parts, 'unsafe payload path')
    result = root.joinpath(*path.parts)
    g.require(result.resolve().is_relative_to(root.resolve()) and result.is_file() and not result.is_symlink(), 'payload escapes its source tree')
    return result


def source_files(root):
    directory = root / 'EFI'
    entries = list(directory.rglob('*'))
    g.require(directory.is_dir() and not directory.is_symlink() and not any(p.is_symlink() for p in entries), 'symlink in payload tree')
    return {p.relative_to(root).as_posix() for p in entries if p.is_file()}


def title_override(raw, title):
    before = tomllib.loads(raw.decode())
    g.require(len(before.get('entry', [])) == 1 and title and len(title) <= 100, 'expected one entry and a bounded title')
    old = before['entry'][0]['title']
    text = raw.decode()
    pattern = r'(?m)^title\s*=\s*' + re.escape(json.dumps(old)) + r'\s*$'
    changed, count = re.subn(pattern, lambda _: 'title = ' + json.dumps(title), text)
    wanted = copy.deepcopy(before); wanted['entry'][0]['title'] = title
    g.require(count == 1 and tomllib.loads(changed) == wanted, 'title edit changed other boot settings')
    return changed.encode()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', type=Path, default=Path('/dev/disk/by-partlabel/userdata'))
    p.add_argument('--ready', type=Path, required=True)
    p.add_argument('--expected-image', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--title', help='Optional menu-only validation label; boot arguments remain identical')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    manifest = json.loads((a.ready / 'manifest.json').read_text())
    for field in ('qemu_io_verified', 'qemu_process_verified', 'qemu_tmp_verified', 'qemu_stress_verified',
                  'rejection_controls_passed', 'packaged_hmac_verified', 'esp_readback_verified'):
        g.require(manifest.get(field) is True, 'unqualified ready directory: ' + field)
    g.require(re.fullmatch('[0-9a-f]{40}', manifest['source_commit']), 'invalid source identity')
    g.require(g.sha((a.ready / 'esp.img').read_bytes()) == manifest['esp_sha256'], 'ready ESP hash mismatch')
    stress = json.loads((a.ready / 'qemu-stress-check.json').read_text())
    g.require(stress['passed'] and stress['kernel_sha256'] == manifest['source_kernel_sha256'] and
              stress['source_kernel_build']['usb_diagnostic_build_id'] == manifest['source_commit'], 'ready source/CI identity mismatch')
    source = a.ready / 'esp-tree'
    files = manifest['files']
    actual = source_files(source)
    g.require(actual == set(files), 'source tree contains missing or unrecorded files')
    expected_files = {}
    for name, item in files.items():
        data = file_path(source, name).read_bytes()
        g.require(len(data) == item['bytes'] and g.sha(data) == item['sha256'], 'payload file digest mismatch: ' + name)
        expected_files[name] = data
    required = {'EFI/BOOT/BOOTAA64.EFI', 'EFI/UniXNU/unixnu.toml', 'EFI/UniXNU/Payloads/kernel.macho',
                'EFI/UniXNU/Payloads/kernel.macho.adt', 'EFI/UniXNU/Payloads/root.img', 'EFI/UniXNU/Payloads/DarwinBoot.efi'}
    g.require(required <= set(files), 'incomplete boot file set')
    identity = g.inspect_device(a.device)
    before = g.direct_image(a.device, g.ESP_BYTES)
    g.require(before == a.expected_image.read_bytes(), 'physical ESP differs from expected starting image')
    a.out.mkdir(parents=True, exist_ok=False)
    (a.out / 'before.img').write_bytes(before)
    image = a.out / 'updated-esp.img'; image.write_bytes(before)
    def command(*args):
        return subprocess.run(args, check=True, capture_output=True)
    command('mdeltree', '-i', str(image), '::/EFI')
    command('mcopy', '-i', str(image), '-s', str(source / 'EFI'), '::/')
    if a.title:
        name = 'EFI/UniXNU/unixnu.toml'
        expected_files[name] = title_override(expected_files[name], a.title)
        override = a.out / 'unixnu.toml'; override.write_bytes(expected_files[name])
        command('mcopy', '-o', '-i', str(image), str(override), '::/' + name)
    command('fsck.fat', '-n', str(image))
    extracted = a.out / 'file-check'; extracted.mkdir()
    for n, (name, data) in enumerate(expected_files.items()):
        path = extracted / str(n)
        command('mcopy', '-i', str(image), '::/' + name, str(path))
        g.require(path.read_bytes() == data, 'staged FAT file readback mismatch: ' + name)
    after = image.read_bytes()
    g.require(before[:512] == after[:512], 'volume boot geometry changed')
    changes = g.changed_ranges(before, after, identity['sector_bytes'], 96 * 1024 * 1024)
    report = dict(passed=False, applied=False, source_commit=manifest['source_commit'], identity=identity,
                  old_esp_sha256=g.sha(before), updated_esp_sha256=g.sha(after), menu_title=a.title,
                  changed_bytes=len(changes) * identity['sector_bytes'], changed_blocks=len(changes),
                  staged_file_hashes={name:g.sha(data) for name, data in expected_files.items()},
                  boot_partition_written=False, other_partitions_written=False, kernel_boot_observed=False)
    def save():
        (a.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    save()
    if not a.apply:
        report.update(passed=True, dry_run=True); save()
        print('READ ONLY: staged EFI file replacement and all file hashes verified')
        return
    g.require(g.inspect_device(a.device) == identity, 'device changed before update')
    fd = os.open(a.device, os.O_RDWR | os.O_EXCL | os.O_CLOEXEC)
    touched = []
    try:
        g.require(os.fstat(fd).st_rdev == identity['rdev'] and g.geometry(fd) == (identity['sector_bytes'], identity['partition']['bytes']), 'opened userdata device changed')
        g.require(g.read_exact(fd, 0, g.ESP_BYTES) == before, 'ESP changed since preflight')
        try:
            for at, data in changes:
                touched.append(at)
                g.require(os.pwrite(fd, data, at) == len(data), 'short EFI update write')
            os.fsync(fd); fcntl.ioctl(fd, g.BLKFLSBUF)
            g.require(g.direct_image(a.device, g.ESP_BYTES) == after, 'direct updated ESP readback mismatch')
        except BaseException:
            if touched:
                g.require(g.inspect_device(a.device) == identity, 'device changed before rollback')
                for at in touched:
                    data = before[at:at + identity['sector_bytes']]
                    g.require(os.pwrite(fd, data, at) == len(data), 'short update rollback write')
                os.fsync(fd); fcntl.ioctl(fd, g.BLKFLSBUF)
                report['failure_rollback_verified'] = g.direct_image(a.device, g.ESP_BYTES) == before
                save()
            raise
    finally:
        os.close(fd)
    report.update(passed=True, applied=True, direct_readback_verified=True); save()
    print('EFI files updated over USB; full ESP direct readback and all staged file hashes verified')


if __name__ == '__main__':
    main()
