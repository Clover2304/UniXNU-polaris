#!/usr/bin/env python3
"""Observe real RAM-file I/O, standard descriptors and resident PID 1 in QEMU.

Read-only boot media; the only mutable file is a fixed 4K kernel RAM buffer.
Observe EL0 through GDB, kernel counters through bounded physical QMP reads,
and advancing timer IRQs across three real select(2) timeout iterations.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['stage', 'failure_errno', 'console_fd', 'stdout_bytes', 'stderr_bytes',
          'pipe_read_fd', 'pipe_write_fd', 'stdin_bytes', 'file_fd', 'seed_read_bytes',
          'file_write_bytes', 'file_read_bytes', 'bounds_errno', 'launchd_write_errno',
          'file_eof_verified', 'reopen_verified', 'closed_descriptors', 'heartbeat_writes',
          'select_result', 'select_returns', 'pipe_eof_verified']


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel', type=Path, required=True)
    p.add_argument('--platform-esp', type=Path, required=True)
    p.add_argument('--init-dir', type=Path, required=True)
    p.add_argument('--out', type=Path, default=ROOT / 'out/io-check')
    p.add_argument('--cpu', default='cortex-a76')
    p.add_argument('--negative', choices=['missing-ram-file'])
    a = p.parse_args()
    fixture = module('fixture', ROOT / 'scripts/build-polaris-init.py')
    common = module('common', ROOT / 'scripts/check-polaris-init.py')
    platform = module('platform', ROOT / 'scripts/check-platform-kernel.py')
    image = (a.init_dir / 'launchd.macho').read_bytes()
    identity = fixture.inspect(image)
    if identity['mode'] != 'io':
        p.error('requires the own resident I/O init')
    data = a.kernel.read_bytes()
    build = json.loads(a.kernel.with_suffix('.json').read_text())
    if (build.get('kernel_sha256') != hashlib.sha256(data).hexdigest() or
            build.get('original_components_prelinked') is not False or
            build.get('init_signature_policy') != 'pid1-exact-adhoc-cdhash-v2' or
            build.get('ram_io_available') is not True):
        p.error('requires the matching source-only RAM I/O kernel')
    counters = {'_rootvnode': 8, '_polaris_io_root_enabled': 4, '_polaris_console_ready': 4,
                '_polaris_io_file_reads': 8, '_polaris_io_file_writes': 8,
                '_polaris_init_signature_accepted': 4, '_polaris_init_loader_enforced': 4,
                '_cs_validate_page_bad_hash': 8, '_cs_validate_page_no_hash': 8,
                '_armcpu_timer_interrupts': 8, '_kernel_debugger_entry_count': 4}
    names = set(counters) | {'_panic', '_panic_with_options', '_panic_trap_to_debugger'}
    syms = platform.symbols(data, names)
    out = a.out.resolve();out.mkdir(parents=True, exist_ok=True)
    esp = out / 'esp'
    if esp.exists():
        shutil.rmtree(esp)
    shutil.copytree(a.platform_esp, esp)
    adt = module('adt', ROOT / 'scripts/check-cpu-prefix.py')
    _, props, _, _ = adt._decode((esp / 'EFI/UniXNU/Payloads/kernel.macho.adt').read_bytes())
    if props.get('compatible') != b'linux,dummy-virt\0':
        p.error('requires the QEMU root-check ESP')
    (esp / 'EFI/UniXNU/Payloads/kernel.macho').write_bytes(data)
    ramdisk = fixture.ramdisk(image) if not a.negative else image + bytes((-len(image)) % 4096)
    (esp / 'EFI/UniXNU/Payloads/root.img').write_bytes(ramdisk)
    config = esp / 'EFI/UniXNU/unixnu.toml';text = config.read_text()
    if 'polaris-root-check=1' not in text:
        p.error('missing root-check boot contract')
    config.write_text(text.replace('polaris-root-check=1', f'polaris-init-cdhash={identity["cdhash"]} polaris-io=1'))
    report = dict(passed=False, milestone='resident PID 1 with bounded RAM-file I/O and stdio',
                  negative_control=a.negative, kernel_sha256=hashlib.sha256(data).hexdigest(), process_sha256=identity['process_sha256'],
                  cdhash=identity['cdhash'], ramdisk_sha256=hashlib.sha256(ramdisk).hexdigest(),
                  source_kernel_build=build, cpu=a.cpu, el0_observed=False, io_verified=False,
                  resident_verified=False, physical_polaris_booted=False, apple_userland_booted=False,
                  interactive_input_qualified=False, stdin_source='self-fed pipe', snapshots=[])
    user = identity['addresses'];user.update(identity['io_symbols'])
    with tempfile.TemporaryDirectory(prefix='polaris-io-') as temporary:
        work = Path(temporary);qmp_path = work / 'qmp'
        shutil.copyfile('/usr/share/AAVMF/AAVMF_VARS.fd', work / 'vars.fd')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0));port = reservation.getsockname()[1]
        cmd = ['qemu-system-aarch64', '-machine', 'virt,gic-version=3', '-cpu', a.cpu, '-m', '2048', '-smp', '1',
               '-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.fd',
               '-drive', f'if=pflash,format=raw,file={work}/vars.fd',
               '-drive', f'if=none,id=esp,format=raw,readonly=on,file=fat:{esp}',
               '-device', 'virtio-blk-pci,drive=esp', '-device', 'ramfb', '-nic', 'none', '-display', 'none', '-no-reboot',
               '-serial', f'file:{out}/serial.log', '-gdb', f'tcp:127.0.0.1:{port}', '-S',
               '-qmp', f'unix:{qmp_path},server=on,wait=off']
        (out / 'command.json').write_text(json.dumps(cmd, indent=2)+'\n')
        with (out / 'qemu.log').open('w') as log:
            process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic()+120
                while True:
                    try:
                        connection = socket.create_connection(('127.0.0.1', port), timeout=60);break
                    except ConnectionRefusedError:
                        if process.poll() is not None or time.monotonic()>deadline:
                            raise RuntimeError('QEMU did not start')
                        time.sleep(.05)
                with connection, socket.socket(socket.AF_UNIX) as qs:
                    qs.settimeout(10);qs.connect(str(qmp_path))
                    with qs.makefile('rw') as stream:
                        json.loads(stream.readline())
                        def qmp(name, **arguments):
                            stream.write(json.dumps({'execute':name,'arguments':arguments})+'\n');stream.flush()
                            while True:
                                reply=json.loads(stream.readline())
                                if 'error' in reply:raise RuntimeError(reply['error'])
                                if 'return' in reply:return reply['return']
                        qmp('qmp_capabilities')
                        packet = common.Packets(connection).request
                        def memory(va, length):
                            raw=packet(f'm{va:x},{length:x}')
                            if raw.startswith('E') or len(raw)!=length*2:raise RuntimeError('Cannot read actual user memory')
                            return bytes.fromhex(raw)
                        def kernel_counters():
                            logtext=(out/'serial.log').read_text(errors='replace')
                            match=re.search(r'DARWINBOOT: ARM64 va=0x([0-9a-fA-F]+) pa=0x([0-9a-fA-F]+)',logtext)
                            if not match:raise RuntimeError('Missing actual loader placement')
                            va,pa=(int(x,16) for x in match.groups());snapshot={}
                            for name,size in counters.items():
                                physical=pa+syms[name]-va
                                if not pa<=physical<=pa+0x40000000-size:raise RuntimeError('Counter outside owned kernel window')
                                qmp('pmemsave',val=physical,size=size,filename=str(work/'counter.bin'))
                                snapshot[name]=int.from_bytes((work/'counter.bin').read_bytes(),'little')
                            return snapshot
                        stops=[user['_checkpoint'],user['_io_checkpoint'],user['_resident_checkpoint'],user['_io_failure'],
                               syms['_panic'],syms['_panic_with_options'],syms['_panic_trap_to_debugger']]
                        for address in stops:
                            if packet(f'Z1,{address:x},4')!='OK':raise RuntimeError('Cannot set observation breakpoint')
                        while time.monotonic()<deadline:
                            connection.settimeout(min(30,max(.1,deadline-time.monotonic())))
                            try:packet('c')
                            except socket.timeout:packet(None)
                            regs=bytes.fromhex(packet('g'));pc=struct.unpack_from('<Q',regs,256)[0]
                            psr=struct.unpack_from('<I',regs,264)[0];report.update(pc=hex(pc),pstate=hex(psr))
                            if pc==user['_checkpoint']:
                                v=struct.unpack('<4Q',memory(user['_results'],32));pages=int.from_bytes(memory(user['_user_page_size'],8),'little');length=int.from_bytes(memory(user['_old_length'],8),'little')
                                report['syscalls']=dict(pid=v[0],sysctl_result=v[1],getpid_flags=v[2],sysctl_flags=v[3],page_size=pages,old_length=length)
                                report['el0_observed']=((psr&15)==0 and v[0]==1 and v[1]==0 and not ((v[2]|v[3])&(1<<29)) and pages==4096 and length==4)
                                if not report['el0_observed']:break
                                packet(f'z1,{pc:x},4');continue
                            if pc==user['_io_failure']:
                                report['failure_stage']=struct.unpack_from('<Q',regs,0)[0]
                                report['failure_errno']=struct.unpack_from('<Q',regs,8)[0]
                                if a.negative == 'missing-ram-file':
                                    report['passed'] = report['el0_observed'] and report['failure_stage']==16 and report['failure_errno']==2
                                break
                            if pc in [user['_io_checkpoint'],user['_resident_checkpoint']]:
                                values=struct.unpack('<32Q',memory(user['_io_results'],256));state=dict(zip(FIELDS,values));report['io_results']=state
                                report['io_verified']=((psr&15)==0 and state['stage']==100 and state['failure_errno']==0 and
                                    state['stdout_bytes']>0 and state['stderr_bytes']>0 and state['stdin_bytes']>0 and
                                    state['seed_read_bytes']>0 and state['file_write_bytes']==state['file_read_bytes']>0 and
                                    state['bounds_errno']==28 and state['launchd_write_errno'] in (26,30) and
                                    state['file_eof_verified']==state['reopen_verified']==state['pipe_eof_verified']==1)
                                if not report['io_verified']:break
                                if pc==user['_io_checkpoint']:
                                    packet(f'z1,{pc:x},4');continue
                                sample=kernel_counters();sample.update(ticks=int.from_bytes(memory(user['_io_ticks'],8),'little'),select_returns=state['select_returns'],elapsed=time.monotonic())
                                report['snapshots'].append(sample)
                                if len(report['snapshots'])>=3:
                                    first,last=report['snapshots'][0],sample
                                    report['resident_verified']=(last['ticks']>first['ticks']>=1 and last['select_returns']>=2 and state['select_result']==0 and
                                        last['elapsed']-first['elapsed']>=1.5 and last['_armcpu_timer_interrupts']>first['_armcpu_timer_interrupts']>0)
                                    report['passed']=(report['el0_observed'] and report['io_verified'] and report['resident_verified'] and
                                        last['_polaris_io_root_enabled']==last['_polaris_console_ready']==1 and
                                        last['_polaris_io_file_reads']>0 and last['_polaris_io_file_writes']>0 and
                                        last['_polaris_init_signature_accepted']>0 and last['_polaris_init_loader_enforced']>0 and
                                        last['_rootvnode']!=0 and last['_cs_validate_page_bad_hash']==last['_cs_validate_page_no_hash']==last['_kernel_debugger_entry_count']==0)
                                    break
                                # GDB continue at a hardware breakpoint can stop at the
                                # same PC without executing it. Step once with it disarmed.
                                if packet(f'z1,{pc:x},4') != 'OK':
                                    raise RuntimeError('Cannot disarm resident breakpoint')
                                packet('s')
                                if packet(f'Z1,{pc:x},4') != 'OK':
                                    raise RuntimeError('Cannot rearm resident breakpoint')
                                continue
                            if pc in (syms['_panic'],syms['_panic_with_options']):
                                ptr=struct.unpack_from('<Q',regs,24 if pc==syms['_panic_with_options'] else 0)[0]
                                raw=memory(ptr,256);report['panic_format']=raw.split(b'\0')[0].decode(errors='replace')
                            break
                        qmp('screendump',filename=str(out/'screen.ppm'))
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']:raise SystemExit(f'FAIL: inspect {out/"report.json"}')
    print('PASS: absent RAM data file returns ENOENT without a kernel panic' if a.negative else 'PASS: actual PID 1 verified RAM-file read/write, stdio and three timed resident iterations')


if __name__=='__main__':
    main()
