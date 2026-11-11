#!/usr/bin/env python3
"""Read the real polaris CDC diagnostic channel and save bounded host evidence.

Requires PyUSB and libusb. This talks to the XNU 1209:0001 diagnostic device;
it does not use fastboot/ADB and does not submit a reboot or memory-write command.
"""
import argparse,hashlib,json,re,secrets,time
from pathlib import Path

def verify_ufs(build,raw):
    pattern=(rb'POLARIS_UFS_SNAPSHOT build='+build.encode()+rb' state=([a-z-]+) gcc_reads=(\d+) controller_reads=(\d+) writes=0 media_reads=0\n'
             rb'POLARIS_UFS_GATE bcr=([0-9a-f]{8}) gdsc=([0-9a-f]{8}) cfg=([0-9a-f]{8}) axi=([0-9a-f]{8}) ahb=([0-9a-f]{8}) aggre=([0-9a-f]{8})\n'
             rb'POLARIS_UFS_REG cap=([0-9a-f]{8}) ver=([0-9a-f]{8}) hcs=([0-9a-f]{8}) hce=([0-9a-f]{8}) utrl=([0-9a-f]{8}) utmrl=([0-9a-f]{8})\nPOLARIS_UFS_END\n')
    m=re.fullmatch(pattern,raw)
    if not m:raise ValueError('incomplete UFS snapshot or wrong build identity')
    state=m[1].decode();gcc_reads=int(m[2]);controller_reads=int(m[3])
    gates=dict(zip(('bcr','gdsc','cfg','axi','ahb','aggre'),[int(v,16) for v in m.groups()[3:9]]))
    regs=dict(zip(('cap','ver','hcs','hce','utrl','utmrl'),[int(v,16) for v in m.groups()[9:15]]))
    if state=='snapshot':
        if gcc_reads!=12 or controller_reads!=6:raise ValueError('inconsistent UFS snapshot counts')
        if gates['gdsc']==0xffffffff or gates['cfg']==0xffffffff or gates['gdsc']&1 or not gates['cfg']&0x10000 or gates['bcr']&1 or any(not gates[k]&1 or gates[k]&0x80000000 for k in ('axi','ahb','aggre')):
            raise ValueError('UFS snapshot without ready power/reset/clock gate')
        if regs['cap'] in (0,0xffffffff) or regs['ver'] not in (0x100,0x101,0x110,0x200,0x210,0x300,0x310,0x400) or regs['hcs']==0xffffffff or regs['hce']&~3:
            raise ValueError('invalid UFS controller register values')
    elif state in ('power-not-ready','reset-asserted','clocks-not-ready'):
        if gcc_reads!=6 or controller_reads or any(regs.values()):raise ValueError('unsafe or inconsistent skipped UFS probe')
    else:raise ValueError('UFS probe did not yield a valid snapshot or safe readiness skip: '+state)
    return dict(ufs_probe_received=True,ufs_probe_state=state,ufs_controller_registers_verified=state=='snapshot',
                ufs_native_media_read_verified=False,ufs_gate=gates,ufs_registers=regs,
                ufs_snapshot_sha256=hashlib.sha256(raw).hexdigest())

def verify_response(build,nonce,pong,echo,status,log):
    if pong.strip()!=b'POLARIS_PONG':raise ValueError('wrong ping response')
    if echo.strip()!=b'POLARIS_ECHO '+nonce.encode():raise ValueError('nonce did not round-trip')
    if not re.search(rb'^POLARIS_USB_STATUS build='+build.encode()+rb' .*configured=1 ',status,re.M):
        raise ValueError('wrong firmware identity or unconfigured interface')
    match=re.search(rb'^POLARIS_LOG_BEGIN build='+build.encode()+rb' capacity=65536 scope=post-console-open end=(\d+) dropped=(\d+)\n',log,re.M)
    if not match:raise ValueError('missing matching log header')
    end=re.search(rb'\nPOLARIS_LOG_END end=(\d+) dropped=(\d+)\n',log)
    if not end or end.groups()!=match.groups() or b'ERR ' in log:raise ValueError('incomplete log snapshot')
    for marker in [b'POLARIS_STRESS_OK total=1000 reaped=1000 maxlive=8',b'POLARIS_STRESS_CLEAN nochild=OK fd=OK tmp=OK cow=OK']:
        if marker not in log:raise ValueError('runtime checkpoint missing from log')
    return dict(log_end=int(match[1]),dropped=int(match[2]),log_bytes=len(log),log_sha256=hashlib.sha256(log).hexdigest())

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--expected-build',required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--ufs-probe',action='store_true',help='collect and validate the opt-in cached UFS snapshot')
    p.add_argument('--wait',type=float,default=120);a=p.parse_args()
    if not re.fullmatch('[0-9a-f]{40}',a.expected_build):p.error('requires the exact source commit')
    import usb.core,usb.util
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    report=dict(passed=False,physical_polaris=False,source_commit=a.expected_build,transport='native XNU polling USB CDC',
                log_scope='64 KiB runtime console ring; no pre-console or persistent crash capture',apple_usb_pairing_qualified=False)
    device=None;detached=[];claimed=[]
    def exchange(command,finish,seconds=10):
        device.write(0x01,command.encode()+b'\n',timeout=2000)
        data=bytearray();deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            try:data.extend(device.read(0x81,16384,timeout=500))
            except usb.core.USBTimeoutError:continue
            if len(data)>160*1024:raise ValueError('USB response exceeds bound')
            if finish(data):return bytes(data)
        raise TimeoutError('USB command timed out: '+command.split()[0])
    try:
        deadline=time.monotonic()+a.wait
        while True:
            devices=list(usb.core.find(find_all=True,idVendor=0x1209,idProduct=0x0001))
            if len(devices)>1:raise ValueError('multiple diagnostic devices; isolate the test phone')
            if devices:device=devices[0];break
            if time.monotonic()>deadline:raise TimeoutError('native CDC device did not enumerate')
            time.sleep(.25)
        if usb.util.get_string(device,device.iSerialNumber)!='POLARISDBG01':raise ValueError('unexpected diagnostic serial string')
        for interface in [0,1]:
            try:
                if device.is_kernel_driver_active(interface):device.detach_kernel_driver(interface);detached.append(interface)
            except NotImplementedError:pass
            usb.util.claim_interface(device,interface);claimed.append(interface)
        device.ctrl_transfer(0x21,0x22,3,0,None,timeout=2000)
        nonce=secrets.token_hex(16)
        pong=exchange('ping',lambda d:b'POLARIS_PONG\n' in d)
        echo=exchange('echo '+nonce,lambda d:('POLARIS_ECHO '+nonce+'\n').encode() in d)
        status=exchange('status',lambda d:b'\n' in d)
        log=exchange('log',lambda d:re.search(rb'\nPOLARIS_LOG_END end=\d+ dropped=\d+\n',d),20)
        for name,data in [('ping',pong),('echo',echo),('status',status),('log',log)]:
            (out/(name+'.raw')).write_bytes(data)
        if a.ufs_probe:
            raw=exchange('ufs',lambda d:b'POLARIS_UFS_END\n' in d)
            (out/'ufs.raw').write_bytes(raw)
            report.update(verify_ufs(a.expected_build,raw))
        values=verify_response(a.expected_build,nonce,pong,echo,status,log)
        report.update(values,passed=True,physical_polaris=True,nonce=nonce,
                      native_enumeration_verified=True,bidirectional_roundtrip_verified=True,build_identity_verified=True,
                      runtime_console_log_retrieved=True)
    except Exception as error:
        report['error']=str(error);raise
    finally:
        if device is not None:
            for interface in reversed(claimed):usb.util.release_interface(device,interface)
            for interface in detached:
                try:device.attach_kernel_driver(interface)
                except usb.core.USBError:pass
            usb.util.dispose_resources(device)
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('PASS: native USB nonce round-trip, exact build identity and runtime console log; evidence:',out)

if __name__=='__main__':main()
