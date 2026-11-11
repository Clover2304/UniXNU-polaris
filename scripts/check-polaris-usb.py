#!/usr/bin/env python3
"""Read the real polaris CDC diagnostic channel and save bounded host evidence.

Requires PyUSB and libusb. This talks to the XNU 1209:0001 diagnostic device;
it does not use fastboot/ADB and does not submit a reboot or memory-write command.
"""
import argparse,hashlib,json,re,secrets,time,struct,zlib
from pathlib import Path

def verify_primary_gpt(data,capacity):
    if len(data)!=6*4096 or capacity['block_bytes']!=4096:raise ValueError('requires six 4-KiB sectors')
    last=capacity['last_lba']
    if data[510:512]!=b'\x55\xaa' or not any(data[446+16*n+4]==0xee for n in range(4)):
        raise ValueError('protective MBR missing')
    h=data[4096:8192]
    if h[:8]!=b'EFI PART':raise ValueError('GPT signature missing')
    revision,length,crc,reserved=struct.unpack_from('<4I',h,8)
    if revision!=0x10000 or not 92<=length<=4096 or reserved:raise ValueError('GPT header fields invalid')
    clean=bytearray(h[:length]);clean[16:20]=bytes(4)
    if zlib.crc32(clean)!=crc:raise ValueError('GPT header CRC mismatch')
    here,alternate,first,end=struct.unpack_from('<4Q',h,24)
    table,count,stride,table_crc=struct.unpack_from('<Q3I',h,72)
    if here!=1 or alternate!=last or not 2<=first<=end<last:raise ValueError('GPT header LBAs disagree with capacity')
    if not 1<=count<=128 or not 128<=stride<=1024 or stride%128:raise ValueError('GPT entry geometry invalid')
    extent=count*stride;table_blocks=(extent+4095)//4096
    if table<2 or table+table_blocks>6 or table+table_blocks>first:raise ValueError('GPT table outside fixed read range')
    entries=data[table*4096:table*4096+extent]
    if len(entries)!=extent or zlib.crc32(entries)!=table_crc:raise ValueError('GPT table CRC mismatch')
    partitions=[];guids=set()
    for n in range(count):
        entry=entries[n*stride:(n+1)*stride]
        if not any(entry[:16]):continue
        guid=entry[16:32];start,stop=struct.unpack_from('<2Q',entry,32)
        name=entry[56:128].decode('utf-16-le').rstrip('\0')
        if not any(guid) or guid in guids or not first<=start<=stop<=end:raise ValueError('GPT partition GUID/bounds invalid')
        if any(start<=p['last_lba'] and stop>=p['first_lba'] for p in partitions):raise ValueError('GPT partitions overlap')
        guids.add(guid);partitions.append(dict(number=n+1,name=name,first_lba=start,last_lba=stop,bytes=(stop-start+1)*4096))
    if len([p for p in partitions if p['name']=='userdata'])!=1:raise ValueError('GPT userdata partition missing or duplicated')
    return dict(primary_gpt_verified=True,backup_gpt_verified=False,gpt_header_crc32=crc,gpt_table_crc32=table_crc,
                gpt_table_sha256=hashlib.sha256(entries).hexdigest(),gpt_partitions=partitions)

def verify_ufs_read(build,raw,prior,reference):
    if prior.get('source_commit')!=build or not prior.get('ufs_scsi_capacity_verified') or not prior.get('ufs_prdt_payload_verified'):
        raise ValueError('sector reads require matching verified SCSI capacity/PRDT')
    if len(reference)!=6*4096:raise ValueError('reference must be exactly six 4-KiB sectors')
    lines=raw.splitlines();pa=prior['ufs_nop_dma']['pa'];cursor=1;sectors=[];all_data=bytearray()
    header=(f'POLARIS_UFS_READ build={build} state=ok count=6 writes=18 doorbells=6 smmu_unchanged=1 prdt_entries=1 sector_commands=6 media_writes=0 smmu_writes=0 retained=1 pa={pa:016x}').encode()
    if len(raw)>131072 or not lines or lines[0]!=header or not raw.endswith(b'\n') or lines[-1]!=b'POLARIS_UFS_READ_END':
        raise ValueError('invalid or incomplete bounded READ stage')
    for n in range(6):
        pattern=(rb'POLARIS_UFS_READ_CMD n='+str(n).encode()+rb' lba='+str(n).encode()+
            rb' opcode=28 lun=00 blocks=1 state=ok ocs=0 response=00 status=00 flags=([0-9a-f]{2}) bytes=4096 residual=0 segment=(0|2) sense_len=0 guards=1 stopped=1 polls=(\d+) run=00000000 db=00000000 is=([0-9a-f]{8}) upiu=([0-9a-f]{68})')
        if cursor>=len(lines):raise ValueError('missing sector command')
        m=re.fullmatch(pattern,lines[cursor]);cursor+=1
        if not m:raise ValueError('READ command failed status, bounds, guards or cleanup')
        flags,segment,polls,is_value=int(m[1],16),int(m[2]),int(m[3]),int(m[4],16);upiu=bytes.fromhex(m[5].decode())
        if flags&0x60 or not 1<=polls<=5000 or is_value&0x71884 or upiu[0]!=0x21 or upiu[1]!=flags or any(upiu[i] for i in (2,3,6,7,8)) or int.from_bytes(upiu[10:12],'big')!=segment or any(upiu[12:16]) or (segment and any(upiu[32:34])):
            raise ValueError('READ UPIU flags/residual/sense invalid')
        if cursor>=len(lines):raise ValueError('missing READ PRDT')
        m=re.fullmatch(rb'POLARIS_UFS_READ_DMA n='+str(n).encode()+rb' data_pa=([0-9a-f]{16}) prdt=([0-9a-f]{32})',lines[cursor]);cursor+=1
        if not m:raise ValueError('READ PRDT missing')
        data_pa=int(m[1],16);prdt=bytes.fromhex(m[2].decode())
        if data_pa!=pa+0x2000 or struct.unpack('<QII',prdt)!=(data_pa,0,4095):raise ValueError('READ PRDT target/length invalid')
        data=bytearray()
        while len(data)<4096:
            if cursor>=len(lines):raise ValueError('sector data truncated')
            m=re.fullmatch(rb'POLARIS_UFS_READ_DATA n='+str(n).encode()+rb' offset='+str(len(data)).encode()+rb' hex=([0-9a-f]{64})',lines[cursor]);cursor+=1
            if not m:raise ValueError('sector data is missing, duplicated or out of order')
            data.extend(bytes.fromhex(m[1].decode()))
        if data!=reference[n*4096:(n+1)*4096]:raise ValueError('native sector differs from independent read-only reference')
        all_data.extend(data);sectors.append(dict(lba=n,bytes=4096,sha256=hashlib.sha256(data).hexdigest(),polls=polls,data_pa=data_pa,prdt_hex=prdt.hex(),upiu_hex=upiu.hex()))
    if cursor+2!=len(lines):raise ValueError('unexpected READ records')
    m=re.fullmatch(rb'POLARIS_UFS_READ_FAULT global_before=([0-9a-f]{8}) bank_before=([0-9a-f]{8}) global_after=([0-9a-f]{8}) bank_after=([0-9a-f]{8})',lines[cursor])
    if not m:raise ValueError('READ fault snapshot missing')
    faults=dict(zip(('global_before','bank_before','global_after','bank_after'),[int(x,16) for x in m.groups()]))
    if faults['global_before'] or faults['global_after'] or (faults['bank_before']|faults['bank_after'])&0xc00001fe:raise ValueError('SMMU fault around sector reads')
    result=verify_primary_gpt(bytes(all_data),prior['ufs_scsi_capacity'])
    result.update(ufs_native_media_read_verified=True,ufs_bounded_read_verified=True,ufs_read_sectors=sectors,
                  ufs_read_faults=faults,ufs_read_sha256=hashlib.sha256(raw).hexdigest(),
                  ufs_read_reference_sha256=hashlib.sha256(reference).hexdigest(),media_write_verified=False)
    return result

def verify_ufs_scsi(build,raw,prior):
    if prior.get('source_commit')!=build or not prior.get('ufs_descriptor_queries_verified') or not prior.get('ufs_owned_nop_verified'):
        raise ValueError('SCSI PRDT requires matching verified descriptor queries and owned NOP')
    lines=raw.splitlines();pa=prior['ufs_nop_dma']['pa'];unit=prior['ufs_read_descriptor_results'][1]
    if not unit['enabled'] or unit['unit_index']!=0:raise ValueError('unit 0 is not enabled')
    h=(rb'POLARIS_UFS_SCSI build='+build.encode()+rb' state=ok count=2 writes=6 doorbells=2 smmu_unchanged=1 prdt_entries=1 sector_commands=0 media_writes=0 smmu_writes=0 retained=1 pa='+f'{pa:016x}'.encode())
    if not lines or not re.fullmatch(h,lines[0]) or not raw.endswith(b'\n') or lines[-1]!=b'POLARIS_UFS_SCSI_END':raise ValueError('invalid or incomplete SCSI stage')
    cursor=1;results=[]
    for n,(kind,opcode,length) in enumerate((('inquiry',0x12,36),('capacity10',0x25,8))):
        pattern=(rb'POLARIS_UFS_SCSI_CMD n='+str(n).encode()+rb' kind='+kind.encode()+rb' opcode='+f'{opcode:02x}'.encode()+
            rb' lun=00 state=ok ocs=0 response=00 status=00 flags=([0-9a-f]{2}) bytes='+str(length).encode()+
            rb' residual=0 segment=(0|2) sense_len=0 guards=1 stopped=1 polls=(\d+) run=00000000 db=00000000 is=([0-9a-f]{8}) upiu=([0-9a-f]{68})')
        if cursor>=len(lines):raise ValueError('missing SCSI command')
        m=re.fullmatch(pattern,lines[cursor]);cursor+=1
        if not m:raise ValueError('SCSI command did not complete GOOD with guards and cleanup')
        flags,segment,polls,is_value=int(m[1],16),int(m[2]),int(m[3]),int(m[4],16);upiu=bytes.fromhex(m[5].decode())
        if flags&0x60 or not 1<=polls<=5000 or is_value&0x71884 or upiu[0]!=0x21 or upiu[1]!=flags or any(upiu[i] for i in (2,3,6,7,8)) or int.from_bytes(upiu[10:12],'big')!=segment or any(upiu[12:16]) or (segment and any(upiu[32:34])):
            raise ValueError('invalid SCSI UPIU response, residual, sense or flags')
        if cursor>=len(lines):raise ValueError('missing PRDT evidence')
        m=re.fullmatch(rb'POLARIS_UFS_SCSI_DMA n='+str(n).encode()+rb' data_pa=([0-9a-f]{16}) prdt=([0-9a-f]{32})',lines[cursor]);cursor+=1
        if not m:raise ValueError('invalid PRDT evidence')
        data_pa=int(m[1],16);prdt=bytes.fromhex(m[2].decode());address,reserved,dbc=struct.unpack('<QII',prdt)
        if data_pa!=pa+0x2000 or address!=data_pa or reserved or dbc!=length-1:raise ValueError('PRDT target or byte count differs from the separate owned data page')
        data=bytearray()
        while len(data)<length:
            if cursor>=len(lines):raise ValueError('missing SCSI payload')
            m=re.fullmatch(rb'POLARIS_UFS_SCSI_DATA n='+str(n).encode()+rb' offset='+str(len(data)).encode()+rb' hex=([0-9a-f]+)',lines[cursor]);cursor+=1
            if not m or len(m[1])!=2*min(32,length-len(data)):raise ValueError('SCSI payload is truncated or noncontiguous')
            data.extend(bytes.fromhex(m[1].decode()))
        record=dict(kind=kind,opcode=opcode,bytes=length,polls=polls,flags=flags,segment=segment,data_pa=data_pa,
                    prdt_hex=prdt.hex(),upiu_hex=upiu.hex(),data_hex=data.hex(),sha256=hashlib.sha256(data).hexdigest())
        if n==0:
            if data[0] or data[3]&15!=2 or data[4]<31:raise ValueError('invalid standard direct-access INQUIRY payload')
            def field(raw):
                padded=False
                for c in raw:
                    if c==0:padded=True;continue
                    if c<0x20 or c>0x7e or (padded and c!=0x20):raise ValueError('invalid INQUIRY text padding')
                return bytes(raw).rstrip(b'\0 ').decode()
            record.update(vendor=field(data[8:16]),product=field(data[16:32]),revision=field(data[32:36]))
        else:
            last,block=struct.unpack('>II',data)
            if last==0xffffffff or block!=1<<unit['block_shift'] or last+1!=unit['logical_blocks']:raise ValueError('SCSI capacity differs from unit descriptor')
            capacity=dict(last_lba=last,logical_blocks=last+1,block_bytes=block,bytes=(last+1)*block)
        results.append(record)
    if cursor+3!=len(lines):raise ValueError('unexpected SCSI data or duplicate record')
    wanted=(f"POLARIS_UFS_SCSI_CAPACITY last_lba={capacity['last_lba']:016x} block_bytes={capacity['block_bytes']} logical_blocks={capacity['logical_blocks']} bytes={capacity['bytes']} query_match=1").encode()
    if lines[cursor]!=wanted or capacity['bytes']!=unit['capacity_hint_bytes']:raise ValueError('capacity summary disagrees with raw PRDT data')
    m=re.fullmatch(rb'POLARIS_UFS_SCSI_FAULT global_before=([0-9a-f]{8}) bank_before=([0-9a-f]{8}) global_after=([0-9a-f]{8}) bank_after=([0-9a-f]{8})',lines[cursor+1])
    if not m:raise ValueError('missing SCSI fault snapshot')
    faults=dict(zip(('global_before','bank_before','global_after','bank_after'),[int(x,16) for x in m.groups()]))
    if faults['global_before'] or faults['global_after'] or (faults['bank_before']|faults['bank_after'])&0xc00001fe:raise ValueError('SMMU fault around PRDT transfers')
    return dict(ufs_scsi_identity_verified=True,ufs_prdt_payload_verified=True,ufs_scsi_capacity_verified=True,
                ufs_scsi_results=results,ufs_scsi_capacity=capacity,ufs_scsi_faults=faults,ufs_scsi_sha256=hashlib.sha256(raw).hexdigest(),ufs_native_media_read_verified=False)

def verify_ufs_query(build,raw,prior):
    if prior.get('source_commit')!=build or not prior.get('ufs_owned_nop_verified') or not prior.get('ufs_dma_verified'):raise ValueError('QUERY requires a matching verified owned NOP')
    lines=raw.splitlines()
    if len(lines)<6 or not raw.endswith(b'\n') or lines[-1]!=b'POLARIS_UFS_QUERY_END':raise ValueError('incomplete UFS QUERY record')
    h=re.fullmatch(rb'POLARIS_UFS_QUERY build='+build.encode()+rb' state=ok count=2 writes=6 doorbells=2 smmu_unchanged=1 sector_commands=0 smmu_writes=0 retained=1 pa=([0-9a-f]{16})',lines[0])
    if not h or int(h[1],16)!=prior['ufs_nop_dma']['pa']:raise ValueError('wrong QUERY identity, count or owned buffer')
    cursor=1;descriptors=[]
    for n,idn in enumerate((0,2)):
        if cursor>=len(lines):raise ValueError('missing descriptor record')
        pattern=(rb'POLARIS_UFS_DESC n='+str(n).encode()+rb' idn='+f'{idn:02x}'.encode()+
            rb' index=00 selector=00 state=ok ocs=0 response=00 requested=255 segment=(\d+) osf=(\d+) guards=1 stopped=1 polls=(\d+) run=00000000 db=00000000 is=([0-9a-f]{8}) upiu=([0-9a-f]{64})')
        m=re.fullmatch(pattern,lines[cursor]);cursor+=1
        if not m:raise ValueError('descriptor command did not complete with checked guards and stop')
        length,osf,polls=int(m[1]),int(m[2]),int(m[3]);is_value=int(m[4],16);upiu=bytes.fromhex(m[5].decode())
        if not (26 if idn==0 else 19)<=length<=osf<=255 or not 1<=polls<=5000 or is_value&0x71884:
            raise ValueError('invalid descriptor response length or status')
        if upiu[0]!=0x36 or upiu[1]&0x60 or any(upiu[i] for i in (2,3,6,7,8,14,15)) or upiu[12:14]!=bytes((1,idn)) or int.from_bytes(upiu[10:12],'big')!=length or int.from_bytes(upiu[18:20],'big')!=osf:
            raise ValueError('descriptor UPIU does not match its request or lengths')
        data=bytearray()
        while len(data)<length:
            if cursor>=len(lines):raise ValueError('descriptor data truncated')
            prefix=rb'POLARIS_UFS_DESC_DATA n='+str(n).encode()+rb' offset='+str(len(data)).encode()+rb' hex=([0-9a-f]+)'
            part=re.fullmatch(prefix,lines[cursor]);cursor+=1
            if not part or len(part[1])!=2*min(32,length-len(data)):raise ValueError('descriptor data has a gap or exceeds bounds')
            data.extend(bytes.fromhex(part[1].decode()))
        if data[0]!=length or data[1]!=idn or (idn==2 and data[2]):raise ValueError('wrong descriptor type, index or bLength')
        record=dict(idn=idn,length=length,osf_length=osf,polls=polls,upiu_hex=upiu.hex(),data_hex=data.hex(),sha256=hashlib.sha256(data).hexdigest())
        if idn==0:
            if not 1<=data[6]<=32:raise ValueError('invalid LU count')
            record.update(spec_version=int.from_bytes(data[16:18],'big'),manufacturer_id=int.from_bytes(data[24:26],'big'),number_lus=data[6],number_wlus=data[7])
        else:
            enabled=data[3];shift=data[10];blocks=int.from_bytes(data[11:19],'big')
            if enabled>1 or (enabled and (not 9<=shift<=16 or not blocks or blocks>((1<<64)-1)>>shift)):raise ValueError('invalid unit geometry')
            record.update(unit_index=0,enabled=bool(enabled),block_shift=shift,logical_blocks=blocks,capacity_hint_bytes=(blocks<<shift) if enabled else None)
        descriptors.append(record)
    if cursor!=len(lines)-2:raise ValueError('unexpected QUERY data or duplicate descriptor')
    f=re.fullmatch(rb'POLARIS_UFS_QUERY_FAULT global_before=([0-9a-f]{8}) bank_before=([0-9a-f]{8}) global_after=([0-9a-f]{8}) bank_after=([0-9a-f]{8})',lines[cursor])
    if not f:raise ValueError('missing QUERY fault snapshot')
    faults=dict(zip(('global_before','bank_before','global_after','bank_after'),[int(v,16) for v in f.groups()]))
    if faults['global_before'] or faults['global_after'] or (faults['bank_before']|faults['bank_after'])&0xc00001fe:raise ValueError('SMMU fault around descriptor queries')
    return dict(ufs_descriptor_queries_verified=True,ufs_read_descriptor_results=descriptors,ufs_query_faults=faults,
                ufs_query_sha256=hashlib.sha256(raw).hexdigest(),ufs_prdt_payload_verified=False,
                ufs_native_media_read_verified=False,ufs_scsi_capacity_verified=False)

def verify_ufs_nop(build,raw,prior,status):
    lines=raw.splitlines()
    if len(lines)!=5 or lines[-1]!=b'POLARIS_UFS_NOP_END' or not raw.endswith(b'\n'):raise ValueError('incomplete UFS NOP record')
    h=re.fullmatch(rb'POLARIS_UFS_NOP build='+build.encode()+rb' state=ok bytes=16384 writes=6 doorbells=1 polls=(\d+) cache_prepared=1 response_read=1 guards=1 stopped=1 smmu_unchanged=1 media_commands=0 smmu_writes=0 retained=1',lines[0])
    if not h or not 1<=int(h[1])<=5000:raise ValueError('UFS NOP did not satisfy single-command completion/cleanup')
    def fields(line,prefix,keys,widths):
        pattern=prefix.encode()+b''.join(b' '+key.encode()+rb'=([0-9a-f]{'+str(width).encode()+rb'})' for key,width in zip(keys,widths))
        m=re.fullmatch(pattern,line)
        if not m:raise ValueError('invalid UFS NOP record: '+prefix)
        return dict(zip(keys,[int(v,16) for v in m.groups()]))
    d=fields(lines[1],'POLARIS_UFS_NOP_DMA',('pa','old_base','final_base','usb_pa','ocs','rsp0','rsp1','rsp2'),(16,16,16,16,1,8,8,8))
    r=fields(lines[2],'POLARIS_UFS_NOP_REG',('is_before','is_after','run','db','ie','hcs','hce'),(8,)*7)
    f=fields(lines[3],'POLARIS_UFS_NOP_FAULT',('global_before','bank_before','global_after','bank_after','far','syn'),(8,8,8,8,16,8))
    if not prior.get('ufs_controller_registers_verified') or not prior.get('ufs_readonly_inventory_verified') or not prior.get('ufs_candidate_bypass_register_shape'):
        raise ValueError('missing matching UFS readiness inventory')
    matches=prior['ufs_candidate_matches'];q=prior['ufs_queue_samples'][1]
    if len(matches)!=1 or matches[0]['smr']!=0x800f0100 or matches[0]['s2cr']!=2 or matches[0]['bank']!=2 or not prior['ufs_idle_queue_samples']:
        raise ValueError('NOP candidate does not match the qualified bank-2 path')
    if prior['ufs_registers']['ver']!=0x210 or not prior['ufs_registers']['cap']&(1<<24) or q['tr_run']!=1 or q['tm_run']!=0:
        raise ValueError('unsupported NOP handoff configuration')
    if d['pa']&4095 or not 0x100000000<=d['pa']<=0x140000000-16384 or d['final_base']!=d['pa'] or d['old_base']!=q['trl'] or d['pa']==d['old_base']:
        raise ValueError('invalid owned UFS DMA range or queue-base handoff')
    m=re.search(rb'(?:^| )dma=([0-9a-f]+)(?: |\n)',status)
    if not m or int(m[1],16)!=d['usb_pa'] or (d['pa']<d['usb_pa']+65536 and d['usb_pa']<d['pa']+16384):
        raise ValueError('UFS allocation overlaps or misidentifies the USB allocation')
    p=b''.join(d[k].to_bytes(4,'big') for k in ('rsp0','rsp1','rsp2'))
    if d['ocs'] or p[0]!=0x20 or p[1]&0x60 or any(p[i] for i in (2,3,6,7,8,10,11)):
        raise ValueError('wrong NOP IN response, task tag or OCS')
    if r['run'] or r['db'] or r['ie'] or r['hce']!=1 or r['hcs']&0xb!=0xb or r['is_after']&0x71884 or r['is_before']!=q['is']:
        raise ValueError('UFS queue did not reach the recorded stopped/healthy state')
    if f['global_before'] or f['global_after'] or (f['bank_before']|f['bank_after'])&0xc00001fe:
        raise ValueError('SMMU fault present around the NOP')
    return dict(ufs_owned_nop_verified=True,ufs_owned_queue_handoff_verified=True,ufs_dma_verified=True,
                ufs_exclusive_firmware_quiescence_verified=False,ufs_native_media_read_verified=False,
                ufs_nop_polls=int(h[1]),ufs_nop_dma=d,ufs_nop_regs=r,ufs_nop_faults=f,
                ufs_nop_sha256=hashlib.sha256(raw).hexdigest(),
                ufs_dma_scope='One NOP descriptor/request/response with owned pages, explicit cache maintenance, guard checks and stopped transfer queue; no data payload or media read')

def verify_ufs_inventory(build,raw):
    """Validate a cached register inventory; never infer DMA or SID ownership."""
    def parse(line,prefix,spec):
        parts=spec.split();patterns={'d':rb'(0|[1-9][0-9]*)','x8':rb'([0-9a-f]{8})','x16':rb'([0-9a-f]{16})','x4':rb'([0-9a-f]{4})'}
        pattern=prefix.encode()+b''.join(b' '+p.split(':')[0].encode()+b'='+patterns[p.split(':')[1]] for p in parts)
        m=re.fullmatch(pattern,line)
        if not m:raise ValueError('invalid UFS inventory line: '+prefix)
        return {p.split(':')[0]:int(v,10 if p.endswith(':d') else 16) for p,v in zip(parts,m.groups())}
    lines=raw.splitlines()
    header=rb'POLARIS_UFS_INVENTORY build='+build.encode()+rb' state=snapshot gcc_reads=12 controller_reads=24 smmu_reads=(0|[1-9][0-9]*) writes=0 media_commands=0'
    if len(lines)<5 or not raw.endswith(b'\n') or lines[-1]!=b'POLARIS_UFS_INVENTORY_END':raise ValueError('incomplete UFS inventory')
    h=re.fullmatch(header,lines[0])
    if not h:raise ValueError('wrong UFS inventory build, state or read/write counts')
    queues=[parse(lines[i+1],'POLARIS_UFS_QUEUE','sample:d is:x8 ie:x8 trl:x16 tr_db:x8 tr_run:x8 tmrl:x16 tm_db:x8 tm_run:x8 hcs:x8 hce:x8') for i in range(2)]
    for i,q in enumerate(queues):
        if q['sample']!=i or q['hce']&~3 or q['hcs']==0xffffffff or q['tr_run']&~1 or q['tm_run']&~1:
            raise ValueError('invalid inherited UFS queue register sample')
    info=parse(lines[3],'POLARIS_UFS_IOMMU','idr0:x8 idr1:x8 idr2:x8 cr0:x8 groups:d scanned:d valid:d pages:d banks:d truncated:d stable:d queues_stable:d matches:d overlap:d coverage:x4 sid_base:x4 sid_count:d')
    groups=info['idr0']&255;banks=info['idr1']&255;pages=1<<(((info['idr1']>>28)&7)+1)
    if not info['idr0']&(1<<27) or info['cr0']&8 or info['idr1']>>31 or not groups or not 0<banks<=128 or pages>128 or (pages+banks)*4096>0x100000:
        raise ValueError('unsupported UFS SMMU geometry')
    if (info['groups'],info['scanned'],info['banks'],info['pages'],info['truncated'])!=(groups,min(groups,128),banks,pages,int(groups>128)):
        raise ValueError('inconsistent UFS SMMU scan bounds')
    if info['sid_base']!=0x100 or info['sid_count']!=16 or not 0<=info['matches']<=min(16,info['valid']) or not info['valid']<=info['scanned']:
        raise ValueError('inconsistent candidate stream inventory')
    if any(info[k] not in (0,1) for k in ('stable','queues_stable','overlap')) or len(lines)!=5+info['matches']:
        raise ValueError('inconsistent UFS inventory size or boolean')
    if info['queues_stable']!=int({k:v for k,v in queues[0].items() if k!='sample'}=={k:v for k,v in queues[1].items() if k!='sample'}):
        raise ValueError('misreported UFS queue stability')
    matches=[];coverage=0;overlap=False;slots=set();translated=0
    for i,line in enumerate(lines[4:-1]):
        m=parse(line,'POLARIS_UFS_MATCH','n:d slot:d smr:x8 s2cr:x8 coverage:x4 bank:d shared:d sctlr:x8 cbar:x8 cba2r:x8')
        mask=(m['smr']>>16)&0x7fff
        expected=sum(1<<n for n in range(16) if not (m['smr']^(0x100+n))&0xffff&~mask)
        if m['n']!=i or m['slot']>=info['scanned'] or m['slot'] in slots or not m['smr']&0x80000000 or not expected or m['coverage']!=expected:
            raise ValueError('invalid candidate SID match')
        slots.add(m['slot']);overlap|=bool(coverage&expected);coverage|=expected
        kind=(m['s2cr']>>16)&3
        if kind==0:
            translated+=1
            if m['bank']!=m['s2cr']&255 or m['bank']>=banks or m['shared']>=info['valid']:
                raise ValueError('invalid SMMU context index or reference count')
        elif m['bank']!=255 or any(m[k] for k in ('shared','sctlr','cbar','cba2r')):
            raise ValueError('context registers reported for non-translate stream')
        m['bypass_register_shape']=kind==1 or (kind==0 and not m['sctlr']&1 and (m['cbar']>>16)&3==1 and bool(m['cba2r']&1))
        matches.append(m)
    expected_reads=8+2*info['scanned']+2*info['valid']+6*translated
    if int(h[1])!=expected_reads or coverage!=info['coverage'] or overlap!=bool(info['overlap']):
        raise ValueError('inconsistent SMMU read count or SID coverage')
    mapping_stable=bool(info['stable'] and not info['truncated'] and not info['overlap'])
    return dict(ufs_readonly_inventory_verified=True,ufs_queue_samples=queues,ufs_smmu_inventory=info,
                ufs_candidate_matches=matches,ufs_candidate_mapping_stable=mapping_stable,
                ufs_candidate_bypass_register_shape=bool(mapping_stable and matches and not info['cr0']&1 and all(m['bypass_register_shape'] for m in matches)),
                ufs_idle_queue_samples=bool(info['queues_stable'] and all(not q['tr_db'] and not q['tm_db'] for q in queues)),
                ufs_candidate_sid_hardware_confirmed=False,ufs_queue_ownership_verified=False,ufs_dma_verified=False,
                ufs_native_media_read_verified=False,ufs_inventory_sha256=hashlib.sha256(raw).hexdigest())

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
    p.add_argument('--ufs-inventory',action='store_true',help='also collect the read-only candidate stream/queue inventory')
    p.add_argument('--ufs-nop',action='store_true',help='verify the cached single owned-queue NOP result')
    p.add_argument('--ufs-query',action='store_true',help='verify cached read-only device and unit descriptor results')
    p.add_argument('--ufs-read',action='store_true',help='verify six cached fixed READ(10) sectors against independent reference')
    p.add_argument('--ufs-read-reference',type=Path,help='six 4-KiB sectors captured independently before boot')
    p.add_argument('--ufs-scsi',action='store_true',help='verify cached INQUIRY and READ CAPACITY PRDT results')
    p.add_argument('--wait',type=float,default=120);a=p.parse_args()
    if not re.fullmatch('[0-9a-f]{40}',a.expected_build):p.error('requires the exact source commit')
    if a.ufs_inventory and not a.ufs_probe:p.error('--ufs-inventory requires --ufs-probe')
    if a.ufs_nop and not a.ufs_inventory:p.error('--ufs-nop requires --ufs-inventory')
    if a.ufs_query and not a.ufs_nop:p.error('--ufs-query requires --ufs-nop')
    if a.ufs_scsi and not a.ufs_query:p.error('--ufs-scsi requires --ufs-query')
    if a.ufs_read and (not a.ufs_scsi or not a.ufs_read_reference):p.error('--ufs-read requires --ufs-scsi and --ufs-read-reference')
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
        if a.ufs_inventory:
            raw=exchange('ufs-map',lambda d:b'POLARIS_UFS_INVENTORY_END\n' in d)
            (out/'ufs-map.raw').write_bytes(raw)
            report.update(verify_ufs_inventory(a.expected_build,raw))
        if a.ufs_nop:
            raw=exchange('ufs-nop',lambda d:b'POLARIS_UFS_NOP_END\n' in d)
            (out/'ufs-nop.raw').write_bytes(raw)
            report.update(verify_ufs_nop(a.expected_build,raw,report,status))
        if a.ufs_query:
            raw=exchange('ufs-query',lambda d:b'POLARIS_UFS_QUERY_END\n' in d)
            (out/'ufs-query.raw').write_bytes(raw)
            report.update(verify_ufs_query(a.expected_build,raw,report))
        if a.ufs_scsi:
            raw=exchange('ufs-scsi',lambda d:b'POLARIS_UFS_SCSI_END\n' in d)
            (out/'ufs-scsi.raw').write_bytes(raw)
            report.update(verify_ufs_scsi(a.expected_build,raw,report))
        if a.ufs_read:
            raw=exchange('ufs-read',lambda d:b'POLARIS_UFS_READ_END\n' in d,30)
            (out/'ufs-read.raw').write_bytes(raw)
            report.update(verify_ufs_read(a.expected_build,raw,report,a.ufs_read_reference.read_bytes()))
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
