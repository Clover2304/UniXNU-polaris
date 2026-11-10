#!/usr/bin/env python3
"""Assemble the polaris payload ESP directory for a UEFI firmware (route A).

Lays out exactly what UniXNU.efi and DarwinBoot expect on the ESP:
  EFI/BOOT/BOOTAA64.EFI                UniXNU.efi from the EFI build
  EFI/UniXNU/Payloads/DarwinBoot.efi   loader driver from the EFI build
  EFI/UniXNU/Payloads/kernel.macho     the source kernel artifact
  EFI/UniXNU/Payloads/kernel.macho.adt the polaris platform ADT
  EFI/UniXNU/unixnu.toml               menu entry from board.toml

Host-side only. Verifies the kernel against its build report, runs the
fail-closed CPU prefix check over the generated ADT and command line, and
writes nothing outside --out. The directory can be mounted/copied to any FAT
ESP; packing it into a filesystem image is left to the environment (mtools,
mkfs.vfat) and is done in CI.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True,
                        help='kernel.macho with its kernel.json beside it')
    parser.add_argument('--efi', type=Path, required=True,
                        help='EFI build output (efi/Build/... and loader/build/...), as produced by check-efi.py')
    parser.add_argument('--out', type=Path, default=ROOT / 'out/polaris-esp')
    parser.add_argument('--ram', type=int, choices=(6, 8), default=6)
    parser.add_argument('--cpus', type=int, default=1,
                        help='CPUs described by the ADT and requested by the command line (1..8)')
    parser.add_argument('--owned-memory-size', type=lambda v: int(v, 0), default=0x40000000)
    roots = parser.add_mutually_exclusive_group()
    roots.add_argument('--initrd', type=Path,
                        help='raw mount-only RAM disk; requires a newly built mockfs/checkpoint kernel')
    roots.add_argument('--user-init', type=Path,
                       help='own static PID 1 launchd.macho built by build-polaris-init.py')
    parser.add_argument('--no-probe', action='store_true',
                        help='skip the boot-progress/panic probes on the ESP kernel copy')
    parser.add_argument('--probe-extra', default='',
                        help='extra patch-xnu-entry-probe arguments (default: --no-reset); e.g. --reset-after _PE_init_platform --in _arm_init --reset-delay 8')
    args = parser.parse_args()
    if not 1 <= args.cpus <= 8:
        parser.error('--cpus must be 1..8')

    kernel = args.kernel.resolve()
    data = kernel.read_bytes()
    report = json.loads(kernel.with_suffix('.json').read_text())
    if report.get('kernel_sha256') != hashlib.sha256(data).hexdigest():
        parser.error('kernel.macho does not match its build report')
    if report.get('original_components_prelinked') is not False:
        parser.error('route A expects the source kernel without original components')
    initrd = None
    user_init = None
    if args.user_init:
        if (report.get('source_mockfs') is not True or
                report.get('init_signature_policy') != 'pid1-exact-adhoc-cdhash-v2'):
            parser.error('user init requires the source-only exact-CDHash policy kernel')
        checker = load_module('platform_check', ROOT / 'scripts/check-platform-kernel.py')
        checker.symbols(data, {'_polaris_init_signature_accepted', '_polaris_init_loader_enforced', '_mockfs_mountroot', '_rootvnode'})
        if b'polaris-init-cdhash\0' not in data:
            parser.error('kernel is missing its init signature policy')
        fixture = load_module('init_fixture', ROOT / 'scripts/build-polaris-init.py')
        image = args.user_init.read_bytes()
        try:
            user_init = fixture.inspect(image)
        except (ValueError, IndexError) as error:
            parser.error(str(error))
        initrd = image + bytes((-len(image)) % 4096)
    if args.initrd:
        if report.get('source_mockfs') is not True or report.get('root_checkpoint_available') is not True:
            parser.error('ramdisk mode requires a mockfs/checkpoint kernel build, not the old platform kernel')
        checker = load_module('platform_check', ROOT / 'scripts/check-platform-kernel.py')
        try:
            checker.symbols(data, {'_mockfs_mountroot', '_rootvnode'})
        except ValueError as error:
            parser.error(str(error))
        if not checker.root_checkpoint_present(data):
            parser.error('kernel is missing the actual root checkpoint')
        if not 4096 <= args.initrd.stat().st_size <= 256 * 1024 * 1024:
            parser.error('raw RAM disk must be 4096 bytes to 256 MiB')
        initrd = args.initrd.read_bytes()

    efi = args.efi.resolve()
    binaries = [(efi / 'efi/Build/Out/AARCH64-DEBUG/UniXNU.efi', 'EFI/BOOT/BOOTAA64.EFI'),
                (efi / 'loader/build/DarwinBoot/DEBUG_GCC/AARCH64/DarwinBoot.efi',
                 'EFI/UniXNU/Payloads/DarwinBoot.efi'),
                (kernel, 'EFI/UniXNU/Payloads/kernel.macho')]
    for source, _ in binaries:
        if not source.is_file():
            parser.error(f'missing EFI input: {source}')

    board = tomllib.loads((ROOT / 'board.toml').read_text(encoding='utf-8'))
    payload = board['payload']
    kernel_args = list(payload['kernel_args'])
    if any(value.startswith('cpus=') for value in kernel_args):
        kernel_args = [f'cpus={args.cpus}' if value.startswith('cpus=') else value
                       for value in kernel_args]
    else:
        kernel_args.append(f'cpus={args.cpus}')
    if initrd is not None:
        kernel_args = [arg for arg in kernel_args if not arg.startswith(('rd=', 'polaris-root-check=', 'polaris-init-cdhash='))]
        kernel_args += ['rd=md0']
        kernel_args += [f'polaris-init-cdhash={user_init["cdhash"]}'] if user_init else ['polaris-root-check=1']

    adt_tool = load_module('polaris_adt', ROOT / 'scripts/make-polaris-adt.py')
    prefix = load_module('cpu_prefix', ROOT / 'scripts/check-cpu-prefix.py')
    adt = adt_tool.build(cpu_count=args.cpus,
                         dram_size=adt_tool.DRAM_SIZE_6 if args.ram == 6 else adt_tool.DRAM_SIZE_8,
                         owned_memory_size=args.owned_memory_size, ramdisk=initrd is not None)
    try:
        prefix.validate(adt, ' '.join(kernel_args))
    except ValueError as error:
        parser.error(f'CPU prefix check failed: {error}')

    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    for source, relative in binaries:
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # Patch the ESP COPY of the kernel (never the artifact) with the boot-progress
    # probes: stage bars for the early MMU milestones plus the on-screen panic
    # funnel. With no serial console on this phone these are the only early-boot
    # signals; the timer heartbeat covers the IOKit stage.
    if not args.no_probe:
        import shlex
        import subprocess as sp
        probe = ROOT / 'scripts/patch-xnu-entry-probe.py'
        sp.run([sys.executable, str(probe), out / 'EFI/UniXNU/Payloads/kernel.macho',
                '--panic-screen', *shlex.split(args.probe_extra or '--no-reset')], check=True)
        # The probes rewrite __text, which invalidates the sealed FIPS POST
        # integrity HMAC the base build wrote into __fips_hmacs. Re-run the
        # seal over the patched copy so corecrypto's integrity POST sees the
        # image it is actually measuring (no selftest is skipped or weakened).
        sp.run([sys.executable, str(ROOT / 'scripts/seal-source-crypto-kernel.py'),
                out / 'EFI/UniXNU/Payloads/kernel.macho'], check=True)
    (out / 'EFI/UniXNU/Payloads/kernel.macho.adt').write_bytes(adt)
    if initrd is not None:
        (out / 'EFI/UniXNU/Payloads/root.img').write_bytes(initrd)
    initrd_line = 'initrd = "Payloads/root.img"\n' if initrd is not None else ''
    (out / 'EFI/UniXNU/unixnu.toml').write_text(
        'version = 1\n'
        '[boot]\n'
        f'timeout = {int(payload.get("timeout", 3))}\n'
        '[kits]\n'
        'autoload = false\n'
        '[[driver]]\n'
        'path = "Payloads/DarwinBoot.efi"\n'
        '[[entry]]\n'
        'id = "platform"\n'
        f'title = "{payload["title"]}"\n'
        'type = "xnu"\n'
        'path = "Payloads/kernel.macho"\n'
        f'{initrd_line}'
        f'args = "{" ".join(kernel_args)}"\n', encoding='utf-8')
    (out / 'esp.json').write_text(json.dumps({
        'kernel_sha256': report['kernel_sha256'],
        'kernel_bytes': len(data), 'cpus': args.cpus, 'ram_gib': args.ram,
        'owned_memory_size': args.owned_memory_size,
        'kernel_args': kernel_args, 'cpu_prefix_checked': True,
        'payload_kernel_sha256': hashlib.sha256((out / 'EFI/UniXNU/Payloads/kernel.macho').read_bytes()).hexdigest(),
        'ramdisk': {'bytes': len(initrd), 'sha256': hashlib.sha256(initrd).hexdigest(),
                    'expected_checkpoint': user_init['expected_checkpoint'] if user_init else 'POLARIS_ROOT_MOUNTED mockfs', 'userland_booted': False}
                   if initrd is not None else None,
        'user_init': user_init,
        'sources': report.get('sources', {}),
    }, indent=2) + '\n', encoding='utf-8')
    print(f'{out}: payload ESP assembled; cpus={args.cpus}, ram={args.ram} GiB; host-verified only')


if __name__ == '__main__':
    main()
