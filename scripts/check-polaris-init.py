#!/usr/bin/env python3
"""Observe our PID 1 at EL0, its real syscall results and its normal RB_PANIC.

Uses a read-only QEMU ESP from check-platform-kernel.py. No phone, external
network, host disk, injected return value or kernel binary patch is involved.
Negative controls name a wrong own CDHash or damage one own executable page.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SUCCESS = 'POLARIS_INIT_OK pid=1 pagesize=4096'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class Packets:
    # Buffered protocol reader adapted from the pinned UniXNU QEMU observer.
    def __init__(self, connection):
        self.connection = connection
        self.pending = bytearray()

    def request(self, text):
        if text is None:
            self.connection.settimeout(3)
            self.connection.sendall(b'\x03')
        else:
            payload = text.encode()
            self.connection.sendall(b'$' + payload + b'#' + f'{sum(payload) % 256:02x}'.encode())
        while True:
            start = self.pending.find(b'$')
            if start < 0:
                self.pending.clear()
            else:
                del self.pending[:start]
                end = self.pending.find(b'#', 1)
                if end >= 0 and len(self.pending) >= end + 3:
                    data = self.pending[1:end]
                    checksum = self.pending[end + 1:end + 3]
                    del self.pending[:end + 3]
                    if int(checksum, 16) != sum(data) % 256:
                        raise RuntimeError('GDB checksum mismatch')
                    self.connection.sendall(b'+')
                    return data.decode()
            if len(self.pending) > 1024 * 1024:
                raise RuntimeError('GDB response exceeds bound')
            chunk = self.connection.recv(65536)
            if not chunk:
                raise RuntimeError('GDB connection closed')
            self.pending.extend(chunk)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--platform-esp', type=Path, required=True, help='QEMU root-check/esp, never a Polaris phone ESP')
    parser.add_argument('--init-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/init-check')
    parser.add_argument('--negative', choices=['wrong-cdhash', 'damaged-code'])
    parser.add_argument('--cpu', default='cortex-a76')
    args = parser.parse_args()
    fixture = module('init_fixture', ROOT / 'scripts/build-polaris-init.py')
    image = (args.init_dir / 'launchd.macho').read_bytes()
    own = fixture.inspect(image)
    data = args.kernel.read_bytes()
    build = json.loads(args.kernel.with_suffix('.json').read_text())
    if (build.get('kernel_sha256') != hashlib.sha256(data).hexdigest() or
            build.get('source_mockfs') is not True or build.get('init_signature_policy') != 'pid1-exact-adhoc-cdhash-v1' or
            build.get('original_components_prelinked') is not False):
        parser.error('requires a matching source-only init-policy kernel build')
    checker = module('platform_check', ROOT / 'scripts/check-platform-kernel.py')
    names = {'_panic', '_panic_with_options', '_panic_trap_to_debugger', '_rootvnode',
             '_polaris_init_signature_accepted', '_cs_validate_page_bad_hash', '_cs_validate_page_no_hash'}
    addresses = checker.symbols(data, names)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    esp = out / 'esp'
    if esp.exists():
        shutil.rmtree(esp)
    shutil.copytree(args.platform_esp, esp)
    prefix = module('adt', ROOT / 'scripts/check-cpu-prefix.py')
    _, props, _, _ = prefix._decode((esp / 'EFI/UniXNU/Payloads/kernel.macho.adt').read_bytes())
    if props.get('compatible') != b'linux,dummy-virt\0':
        parser.error('requires the QEMU ADT, not the Polaris ADT')
    (esp / 'EFI/UniXNU/Payloads/kernel.macho').write_bytes(data)
    cdhash = own['cdhash']
    if args.negative == 'wrong-cdhash':
        cdhash = ('0' if cdhash[0] != '0' else '1') + cdhash[1:]
    if args.negative == 'damaged-code':
        image = bytearray(image)
        image[own['code_file_offset']] ^= 1
        image = bytes(image)
    ramdisk = image + bytes((-len(image)) % 4096)
    (esp / 'EFI/UniXNU/Payloads/root.img').write_bytes(ramdisk)
    config = esp / 'EFI/UniXNU/unixnu.toml'
    text = config.read_text()
    if 'polaris-root-check=1' not in text:
        parser.error('requires the verified root-check ESP boot contract')
    config.write_text(text.replace('polaris-root-check=1', f'polaris-init-cdhash={cdhash}'))
    report = {'passed': False, 'negative_control': args.negative, 'kernel_sha256': hashlib.sha256(data).hexdigest(),
              'process_sha256': own['process_sha256'], 'ramdisk_sha256': hashlib.sha256(ramdisk).hexdigest(),
              'cdhash': own['cdhash'], 'boot_owner_cdhash': cdhash, 'el0_observed': False,
              'userland_booted': False, 'physical_polaris_booted': False, 'apple_userland_booted': False,
              'source_kernel_build': build}
    with tempfile.TemporaryDirectory(prefix='polaris-init-') as temporary:
        work = Path(temporary)
        shutil.copyfile('/usr/share/AAVMF/AAVMF_VARS.fd', work / 'vars.fd')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        cmd = ['qemu-system-aarch64', '-machine', 'virt,gic-version=3', '-cpu', args.cpu, '-m', '2048', '-smp', '1',
               '-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.fd',
               '-drive', f'if=pflash,format=raw,file={work}/vars.fd',
               '-drive', f'if=none,id=esp,format=raw,readonly=on,file=fat:{esp}',
               '-device', 'virtio-blk-pci,drive=esp', '-device', 'ramfb', '-nic', 'none', '-display', 'none',
               '-no-reboot', '-serial', f'file:{out}/serial.log', '-gdb', f'tcp:127.0.0.1:{port}', '-S']
        (out / 'command.json').write_text(json.dumps(cmd, indent=2) + '\n')
        with (out / 'qemu.log').open('w') as log:
            process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 90
                while True:
                    try:
                        connection = socket.create_connection(('127.0.0.1', port), timeout=60)
                        break
                    except ConnectionRefusedError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('QEMU did not start')
                        time.sleep(0.05)
                with connection:
                    packet = Packets(connection).request

                    def memory(va, length):
                        result = packet(f'm{va:x},{length:x}')
                        if result.startswith('E') or len(result) != length * 2:
                            raise RuntimeError('Cannot read actual guest memory')
                        return bytes.fromhex(result)

                    def string(va):
                        data = bytearray()
                        for offset in range(0, 256, 16):
                            data += memory(va + offset, 16)
                            if 0 in data:
                                return data.split(b'\0')[0].decode(errors='replace')
                        raise RuntimeError('Guest string exceeds diagnostic bound')

                    stops = [own['addresses']['_checkpoint'], addresses['_panic'], addresses['_panic_with_options'], addresses['_panic_trap_to_debugger']]
                    for address in stops:
                        if packet(f'Z1,{address:x},4') != 'OK':
                            raise RuntimeError('QEMU rejected observation breakpoint')
                    for phase in range(2):
                        try:
                            packet('c')
                        except socket.timeout:
                            packet(None)
                        regs = bytes.fromhex(packet('g'))
                        pc = struct.unpack_from('<Q', regs, 256)[0]
                        psr = struct.unpack_from('<I', regs, 264)[0]
                        report.update(pc=hex(pc), pstate=hex(psr))
                        if phase == 0 and pc == stops[0]:
                            values = struct.unpack('<4Q', memory(own['addresses']['_results'], 32))
                            pages = int.from_bytes(memory(own['addresses']['_user_page_size'], 8), 'little')
                            size = int.from_bytes(memory(own['addresses']['_old_length'], 8), 'little')
                            report['el0_observed'] = (psr & 15) == 0
                            report['syscalls'] = dict(pid=values[0], sysctl_result=values[1], getpid_flags=values[2], sysctl_flags=values[3], page_size=pages, old_length=size)
                            report['userland_booted'] = (report['el0_observed'] and values[0] == 1 and values[1] == 0 and
                                                         not ((values[2] | values[3]) & (1 << 29)) and pages == 4096 and size == 4)
                            packet(f'z1,{stops[0]:x},4')
                            continue
                        if pc == addresses['_panic_with_options']:
                            report['panic_format'] = string(struct.unpack_from('<Q', regs, 24)[0])
                            if report['panic_format'] == 'userspace panic: %s':
                                pointer = int.from_bytes(memory(struct.unpack_from('<Q', regs, 248)[0], 8), 'little')
                                report['panic_message'] = string(pointer)
                        elif pc == addresses['_panic']:
                            report['panic_format'] = string(struct.unpack_from('<Q', regs)[0])
                        for name, size in [('_rootvnode', 8), ('_polaris_init_signature_accepted', 4), ('_cs_validate_page_bad_hash', 8), ('_cs_validate_page_no_hash', 8)]:
                            report[name] = int.from_bytes(memory(addresses[name], size), 'little')
                        if args.negative == 'wrong-cdhash':
                            report['passed'] = (not report['el0_observed'] and report['_rootvnode'] != 0 and
                                                report['_polaris_init_signature_accepted'] == 0 and
                                                'exec' in report.get('panic_format', '').lower())
                        elif args.negative == 'damaged-code':
                            report['passed'] = (not report['el0_observed'] and report['_rootvnode'] != 0 and
                                                report['_polaris_init_signature_accepted'] > 0 and report['_cs_validate_page_bad_hash'] > 0)
                        else:
                            report['passed'] = (report['userland_booted'] and report.get('panic_message') == SUCCESS and
                                                report['_polaris_init_signature_accepted'] > 0 and report['_rootvnode'] != 0 and
                                                report['_cs_validate_page_bad_hash'] == 0 and report['_cs_validate_page_no_hash'] == 0)
                        break
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if not report['passed']:
        raise SystemExit(f'FAIL: inspect {out / "report.json"}')
    print('PASS:', args.negative or 'PID 1 observed at EL0, getpid=1, HW_PAGESIZE=4096 and normal userspace RB_PANIC')


if __name__ == '__main__':
    main()
