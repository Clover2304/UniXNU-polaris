#!/usr/bin/env python3
"""Reuse installed runner tools and bound downloads of missing Linux tools."""
import argparse
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile


APT_MIRRORS = (
    'https://mirrors.mit.edu/ubuntu',
    'https://mirrors.edge.kernel.org/ubuntu',
    'https://archive.ubuntu.com/ubuntu',
)
UBUNTU_KEYRING = Path('/usr/share/keyrings/ubuntu-archive-keyring.gpg')


def install_packages(packages, work):
    release = platform.freedesktop_os_release()
    codename = release.get('VERSION_CODENAME', '')
    architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    if release.get('ID') != 'ubuntu' or not re.fullmatch('[a-z][a-z0-9]*', codename) or architecture != 'amd64':
        raise SystemExit('Linux CI dependency installation requires Ubuntu amd64')
    if not UBUNTU_KEYRING.is_file():
        raise SystemExit('Ubuntu archive signing keyring is missing')
    work.chmod(0o755)
    for index, mirror in enumerate(APT_MIRRORS):
        attempt = work / f'apt-{index}'
        attempt.mkdir(mode=0o755)
        lists = attempt / 'lists'
        lists.mkdir(mode=0o755)
        source = attempt / 'ubuntu.sources'
        source.write_text(
            f'Types: deb\nURIs: {mirror}\n'
            f'Suites: {codename} {codename}-updates {codename}-security\n'
            'Components: main restricted universe multiverse\n'
            f'Architectures: {architecture}\nSigned-By: {UBUNTU_KEYRING}\n')
        source.chmod(0o644)
        # Isolate both sources and indexes: failed updates cannot reuse the
        # runner's stale Azure-mirror package URLs. Keep Ubuntu signatures.
        apt = ['sudo', 'timeout', '--kill-after=5s', '180s', 'apt-get',
               '-o', f'Dir::Etc::sourcelist={source}', '-o', 'Dir::Etc::sourceparts=-',
               '-o', f'Dir::State::lists={lists}', '-o', 'APT::Update::Error-Mode=any',
               '-o', 'Acquire::Retries=0', '-o', 'Acquire::ForceIPv4=true',
               '-o', 'Acquire::http::Timeout=15', '-o', 'Acquire::https::Timeout=15']
        print(f'Ubuntu dependency mirror {index + 1}/{len(APT_MIRRORS)}: {mirror}', flush=True)
        try:
            subprocess.run([*apt, 'update'], check=True, timeout=195)
            subprocess.run([*apt, 'install', '--no-install-recommends', '-y', *packages],
                           check=True, timeout=195)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            print(f'Mirror attempt failed ({type(error).__name__}); trying the next mirror', flush=True)
            continue
        print('Dependencies installed through ' + mirror, flush=True)
        return
    raise SystemExit('All Ubuntu dependency mirrors failed; no unsigned packages or stale indexes accepted')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', choices=('host', 'efi', 'native'), required=True)
    a = p.parse_args()
    aliases = Path(tempfile.mkdtemp(prefix='polaris-tools-', dir=os.environ.get('RUNNER_TEMP')))
    os.environ['PATH'] = str(aliases) + os.pathsep + os.environ['PATH']
    required = {'clang': 'clang', 'clang++': 'clang', 'ld.lld': 'lld'}
    if a.profile == 'host':
        required.update({name: 'mtools' for name in ('mformat','mcopy','mmd','mtype','mshowfat')})
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
        print('Installing missing tool packages:', ', '.join(packages), flush=True)
        install_packages(packages, aliases)
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
