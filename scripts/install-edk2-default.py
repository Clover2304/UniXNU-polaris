#!/usr/bin/env python3
"""Review or install the already-tested edk2 image as the default boot payload.

Without --apply this is read-only. Installation and restoration are restricted
to the verified phone's 64 MiB boot partition. Supply the saved full backup and
prepared manifest; recovery, GPT and all other partitions are excluded.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location('usb_storage_guard', Path(__file__).with_name('check-uefi-usb-storage.py'))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
BOOT_BYTES = 64 * 1024 * 1024
FIRMWARE_SHA = '534ebe6887b86d6c1f4cc0b6bc92651649b8aebeecd6bc6739e3d19332a5c7fb'
BACKUP_SHA = '9044bba9778f5291d4cc58842e401fa6eeb362b9b2e0739b41653b8272d60681'
CANDIDATE_SHA = '1578f00d31f66e894eaca3593d8ad1fbd2a2ad0a0c8de2a33700cbf45dee3b92'


def validate_images(candidate, backup, manifest):
    guard.require(len(candidate) == len(backup) == BOOT_BYTES, 'expected complete 64 MiB boot images')
    guard.require(guard.sha(backup) == manifest['original_boot_sha256'] == BACKUP_SHA, 'original boot backup mismatch')
    guard.require(guard.sha(candidate) == manifest['candidate_partition_sha256'] == CANDIDATE_SHA, 'edk2 candidate mismatch')
    size = manifest['firmware_payload_bytes']
    guard.require(0 < size < BOOT_BYTES and candidate[:8] == b'ANDROID!', 'invalid Android firmware envelope')
    guard.require(guard.sha(candidate[:size]) == manifest['firmware_sha256'] == FIRMWARE_SHA, 'firmware differs from temporary-boot baseline')
    guard.require(not any(candidate[size:]), 'unexpected partition trailer')
    guard.require(manifest['backup_verified_against_prior_backup'] is True, 'backup not independently verified')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ready', type=Path, required=True)
    p.add_argument('--backup', type=Path, required=True)
    p.add_argument('--device', type=Path, default=Path('/dev/disk/by-partlabel/boot'))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--restore', action='store_true')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    manifest = json.loads((a.ready / 'manifest.json').read_text())
    candidate = (a.ready / 'boot-edk2-partition.img').read_bytes()
    backup = a.backup.read_bytes()
    validate_images(candidate, backup, manifest)
    def identity():
        value = guard.inspect_device(a.device, 'boot', manifest['boot_parent_unit_bytes'], BOOT_BYTES)
        guard.require(value['partition']['bytes'] == BOOT_BYTES, 'boot partition extent mismatch')
        return value
    device = identity()
    before, after = (candidate, backup) if a.restore else (backup, candidate)
    guard.require(guard.direct_image(a.device, BOOT_BYTES) == before, 'current boot image differs from the expected starting state')
    a.out.mkdir(parents=True, exist_ok=False)
    report = dict(passed=False, applied=False, restore=a.restore, identity=device,
                  starting_sha256=guard.sha(before), target_sha256=guard.sha(after),
                  recovery_partition_written=False, other_partitions_written=False)
    def save():
        (a.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    save()
    if not a.apply:
        report.update(passed=True, dry_run=True); save()
        print('READ ONLY: device, both GPT copies, current boot, candidate and rollback backup verified')
        return
    guard.require(identity() == device, 'device changed before installation')
    fd = os.open(a.device, os.O_RDWR | os.O_EXCL | os.O_CLOEXEC)
    changed = False
    try:
        guard.require(os.fstat(fd).st_rdev == device['rdev'] and guard.geometry(fd) == (device['sector_bytes'], BOOT_BYTES), 'opened boot device mismatch')
        guard.require(guard.read_exact(fd, 0, BOOT_BYTES) == before, 'boot changed since preflight')
        try:
            for at in range(0, BOOT_BYTES, 1024 * 1024):
                changed = True
                data = after[at:at + 1024 * 1024]
                guard.require(os.pwrite(fd, data, at) == len(data), 'short boot write')
            os.fsync(fd); fcntl.ioctl(fd, guard.BLKFLSBUF)
            guard.require(guard.direct_image(a.device, BOOT_BYTES) == after, 'direct boot readback mismatch')
        except BaseException:
            if changed:
                guard.require(identity() == device, 'device changed before rollback')
                for at in range(0, BOOT_BYTES, 1024 * 1024):
                    data = before[at:at + 1024 * 1024]
                    guard.require(os.pwrite(fd, data, at) == len(data), 'short emergency rollback write')
                os.fsync(fd); fcntl.ioctl(fd, guard.BLKFLSBUF)
                report['failure_rollback_verified'] = guard.direct_image(a.device, BOOT_BYTES) == before
                save()
            raise
    finally:
        os.close(fd)
    report.update(passed=True, applied=True, direct_readback_verified=True); save()
    print('Boot partition update and direct readback verified; reset is required to test default startup')


if __name__ == '__main__':
    main()
