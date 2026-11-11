#!/usr/bin/env python3
"""Read the real polaris CDC diagnostic channel and save bounded host evidence.

Requires PyUSB and libusb. This talks to the XNU 1209:0001 diagnostic device;
it does not use fastboot/ADB and does not submit a reboot or memory-write command.
"""
import argparse,hashlib,json,re,secrets,time
from pathlib import Path

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
