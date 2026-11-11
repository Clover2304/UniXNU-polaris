#!/usr/bin/env python3
"""Reuse installed runner tools and bound downloads of missing Linux tools."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', choices=('host', 'efi', 'native'), required=True)
    a = p.parse_args()
    aliases = Path(tempfile.mkdtemp(prefix='polaris-tools-', dir=os.environ.get('RUNNER_TEMP')))
    os.environ['PATH'] = str(aliases) + os.pathsep + os.environ['PATH']
    required = {'clang': 'clang', 'clang++': 'clang', 'ld.lld': 'lld'}
    if a.profile != 'host':
        required.update({name: 'llvm' for name in
                         ('llvm-nm', 'llvm-objcopy', 'llvm-objdump', 'llvm-readelf', 'llvm-size')})
        required.update({'ld64.lld': 'lld', 'qemu-system-aarch64': 'qemu-system-arm'})
    if a.profile == 'efi':
        required.update({'make': 'build-essential', 'gcc': 'build-essential', 'g++': 'build-essential',
                         'aarch64-linux-gnu-gcc': 'gcc-aarch64-linux-gnu',
                         'aarch64-linux-gnu-objcopy': 'binutils-aarch64-linux-gnu'})

    def find(name):
        found = shutil.which(name)
        if found:
            return found
        for version in range(22, 13, -1):
            for candidate in (Path(f'/usr/lib/llvm-{version}/bin') / name,
                              Path('/usr/bin') / f'{name}-{version}'):
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    (aliases / name).symlink_to(candidate)
                    return str(candidate)
        return None

    def missing():
        packages = {package for name, package in required.items() if not find(name)}
        if a.profile != 'host' and not all(Path('/usr/share/AAVMF', name).is_file()
                                           for name in ('AAVMF_CODE.fd', 'AAVMF_VARS.fd')):
            packages.add('qemu-efi-aarch64')
        if a.profile == 'efi' and not Path('/usr/include/uuid/uuid.h').is_file():
            packages.add('uuid-dev')
        return sorted(packages)

    packages = missing()
    if packages:
        # Replace only the runner's Ubuntu mirror indirection. Signed-By and
        # normal apt signature verification stay intact.
        sources = [Path('/etc/apt/sources.list'), *Path('/etc/apt/sources.list.d').glob('*.list'),
                   *Path('/etc/apt/sources.list.d').glob('*.sources')]
        for path in sources:
            if not path.is_file():
                continue
            before = path.read_text()
            after = before.replace('mirror+file:/etc/apt/apt-mirrors.txt', 'https://archive.ubuntu.com/ubuntu')
            after = after.replace('mirror+file:/etc/apt/apt-mirrors-security.txt', 'https://security.ubuntu.com/ubuntu')
            after = after.replace('http://azure.archive.ubuntu.com/ubuntu', 'https://archive.ubuntu.com/ubuntu')
            if after != before:
                subprocess.run(['sudo', 'tee', str(path)], input=after, text=True,
                               stdout=subprocess.DEVNULL, check=True, timeout=15)
        apt = ['sudo', 'apt-get', '-o', 'Acquire::Retries=1', '-o', 'Acquire::ForceIPv4=true',
               '-o', 'Acquire::http::Timeout=15', '-o', 'Acquire::https::Timeout=15']
        print('Installing missing tool packages:', ', '.join(packages), flush=True)
        subprocess.run([*apt, 'update'], check=True, timeout=240)
        subprocess.run([*apt, 'install', '--no-install-recommends', '-y', *packages], check=True, timeout=240)
        if missing():
            p.error('tools still missing after package installation: ' + ', '.join(missing()))
    else:
        print('Required tools already installed; apt not needed', flush=True)
    for name in sorted(required):
        print(f'{name}: {shutil.which(name)}', flush=True)
    if os.environ.get('GITHUB_PATH'):
        with open(os.environ['GITHUB_PATH'], 'a') as f:
            f.write(str(aliases) + '\n')


if __name__ == '__main__':
    main()
