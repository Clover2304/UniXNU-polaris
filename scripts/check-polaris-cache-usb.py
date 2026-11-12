#!/usr/bin/env python3
"""Collect the bounded original-cache probe over native CDC; no device reboot.

This profile checks its own program's syscall/data result and changing heartbeat.
It does not observe kernel hash-cache counters or qualify full cache loading.
The existing process/stress USB acceptance checker is unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import secrets
import time


def verify(build, nonce, responses):
    if responses['ping'].strip() != b'POLARIS_PONG':
        raise ValueError('wrong ping response')
    if responses['echo'].strip() != b'POLARIS_ECHO '+nonce.encode():
        raise ValueError('nonce did not round-trip')
    status, log = responses['status'], responses['log']
    if not re.search(rb'^POLARIS_USB_STATUS build='+build.encode()+rb' .*configured=1 ',status,re.M):
        raise ValueError('wrong build or unconfigured device')
    for key in (b'cmd_errors',b'reply_overflow'):
        field = re.search(rb'(?:^| )'+key+rb'=(\d+)(?: |\n|$)',status)
        if not field or int(field[1]):
            raise ValueError('USB health counter failed: '+key.decode())
    begin = re.search(rb'^POLARIS_LOG_BEGIN build='+build.encode()+
        rb' capacity=65536 scope=post-console-open end=(\d+) dropped=(\d+)\n',log,re.M)
    end = re.search(rb'\nPOLARIS_LOG_END end=(\d+) dropped=(\d+)\n',log)
    if not begin or not end or begin.groups()!=end.groups() or int(begin[2]):
        raise ValueError('incomplete, wrong-build or dropped runtime log')
    if b'ERR ' in log or b'POLARIS_CACHE_FAILED' in log or b'panic(cpu' in log:
        raise ValueError('runtime failure in console log')
    for marker in (b'POLARIS_CACHE_START pid=1 pagesize=4096',
        b'POLARIS_CACHE_OK original=1 blocks16k=2 pages4k=8 map=RX cleanup=OK'):
        if marker not in log:
            raise ValueError('cache-probe acceptance marker absent')
    ticks = [int(n) for n in re.findall(rb'POLARIS_CACHE_LIVE tick=(\d) ',log)]
    if not ticks:
        raise ValueError('resident heartbeat absent')
    return dict(log_end=int(begin[1]),dropped=0,last_tick_mod10=ticks[-1],
                log_sha256=hashlib.sha256(log).hexdigest(),log_bytes=len(log))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-build',required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--wait',type=float,default=20)
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}',args.expected_build) or not 0<=args.wait<=120:
        parser.error('requires exact source SHA and bounded wait')
    import usb.core
    import usb.util
    args.out.mkdir(parents=True,exist_ok=False)
    report = dict(passed=False,source_commit=args.expected_build,physical_polaris=False,
        transport='native XNU polling USB CDC',hardware_kernel_hash_counters_observed=False,
        complete_dyld_cache_loaded=False,original_cache_code_executed=False,
        apple_launchd_main_executed=False,ipados_boot_demonstrated=False,
        log_scope='64 KiB post-console-open ring; own syscall/data acceptance marker',samples=[])
    device = None
    detached, claimed = [], []

    def exchange(directory,name,command,finish,seconds=10):
        data = bytearray()
        path = directory/(name+'.raw')
        try:
            device.write(0x01,command.encode()+b'\n',timeout=2000)
            deadline = time.monotonic()+seconds
            while time.monotonic()<deadline:
                try:
                    data.extend(device.read(0x81,16384,timeout=500))
                except usb.core.USBTimeoutError:
                    continue
                if len(data)>160*1024:
                    raise ValueError('response exceeds bound')
                if finish(data):
                    return bytes(data)
            raise TimeoutError('USB command timed out: '+name)
        finally:
            path.write_bytes(data)

    try:
        deadline = time.monotonic()+args.wait
        while True:
            devices = list(usb.core.find(find_all=True,idVendor=0x1209,idProduct=0x0001))
            if len(devices)>1:
                raise ValueError('multiple diagnostic devices')
            if devices:
                device = devices[0]
                break
            if time.monotonic()>=deadline:
                raise TimeoutError('native CDC did not enumerate')
            time.sleep(.25)
        if usb.util.get_string(device,device.iSerialNumber)!='POLARISDBG01':
            raise ValueError('unexpected serial string')
        for interface in [0,1]:
            try:
                if device.is_kernel_driver_active(interface):
                    device.detach_kernel_driver(interface)
                    detached.append(interface)
            except NotImplementedError:
                pass
            usb.util.claim_interface(device,interface)
            claimed.append(interface)
        device.ctrl_transfer(0x21,0x22,3,0,None,timeout=2000)
        for index in range(6):
            directory = args.out/f'sample-{index:02}'
            directory.mkdir()
            nonce = secrets.token_hex(16)
            responses = {}
            responses['ping'] = exchange(directory,'ping','ping',lambda d:b'POLARIS_PONG\n' in d)
            responses['echo'] = exchange(directory,'echo','echo '+nonce,
                lambda d:('POLARIS_ECHO '+nonce+'\n').encode() in d)
            responses['status'] = exchange(directory,'status','status',lambda d:b'\n' in d)
            responses['log'] = exchange(directory,'log','log',
                lambda d:re.search(rb'\nPOLARIS_LOG_END end=\d+ dropped=\d+\n',d),20)
            result = verify(args.expected_build,nonce,responses)
            result.update(path=directory.name,nonce=nonce)
            prior = report['samples'][-1] if report['samples'] else None
            report['samples'].append(result)
            if prior and result['log_end']>prior['log_end'] and result['last_tick_mod10']!=prior['last_tick_mod10']:
                report.update(passed=True,physical_polaris=True,native_enumeration_verified=True,
                    bidirectional_roundtrip_verified=True,build_identity_verified=True,
                    runtime_console_log_retrieved=True,own_cache_probe_syscall_result_verified=True,
                    resident_heartbeat_verified=True,usb_errors=0,log_dropped=0)
                break
            time.sleep(1.2)
        if not report['passed']:
            raise ValueError('heartbeat did not advance between independent snapshots')
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        if device is not None:
            for interface in reversed(claimed):
                try:
                    usb.util.release_interface(device,interface)
                except usb.core.USBError:
                    pass
            for interface in detached:
                try:
                    device.attach_kernel_driver(interface)
                except usb.core.USBError:
                    pass
            usb.util.dispose_resources(device)
        (args.out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('PASS: exact-build native cache-probe result and advancing heartbeat; evidence:',args.out)


if __name__ == '__main__':
    main()
