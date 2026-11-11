"""Golden SCSI/PRDT wire layout and a separate noncoherent DMA peer."""
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class UfsScsiTests(unittest.TestCase):
    def test_exact_build_prdt_geometry_and_raw_capacity_evidence(self):
        spec=importlib.util.spec_from_file_location('usb',ROOT/'scripts/check-polaris-usb.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);build='a'*40
        prior=json.loads((ROOT/'research/ufs-query-result.json').read_text());prior['source_commit']=build
        # The public summary intentionally lacks private raw descriptor bytes.
        prior['ufs_read_descriptor_results']=prior['descriptors']
        pa=prior['ufs_nop_dma']['pa'];unit=prior['descriptors'][1]
        inquiry=bytes((0,0,6,2,31,0,0,0))+b'VENDOR  '+b'MODEL           '+b'0001'
        last=unit['logical_blocks']-1;block=1<<unit['block_shift'];capacity=struct.pack('>II',last,block)
        rows=[f'POLARIS_UFS_SCSI build={build} state=ok count=2 writes=6 doorbells=2 smmu_unchanged=1 prdt_entries=1 sector_commands=0 media_writes=0 smmu_writes=0 retained=1 pa={pa:016x}']
        for n,(kind,op,data) in enumerate((('inquiry',0x12,inquiry),('capacity10',0x25,capacity))):
            upiu=bytearray(34);upiu[0]=0x21
            prdt=struct.pack('<QII',pa+0x2000,0,len(data)-1)
            rows += [f'POLARIS_UFS_SCSI_CMD n={n} kind={kind} opcode={op:02x} lun=00 state=ok ocs=0 response=00 status=00 flags=00 bytes={len(data)} residual=0 segment=0 sense_len=0 guards=1 stopped=1 polls=2 run=00000000 db=00000000 is=00000401 upiu={upiu.hex()}',
                     f'POLARIS_UFS_SCSI_DMA n={n} data_pa={pa+0x2000:016x} prdt={prdt.hex()}']
            for at in range(0,len(data),32):rows.append(f'POLARIS_UFS_SCSI_DATA n={n} offset={at} hex={data[at:at+32].hex()}')
        rows += [f'POLARIS_UFS_SCSI_CAPACITY last_lba={last:016x} block_bytes={block} logical_blocks={last+1} bytes={(last+1)*block} query_match=1',
                 'POLARIS_UFS_SCSI_FAULT global_before=00000000 bank_before=00000400 global_after=00000000 bank_after=00000400','POLARIS_UFS_SCSI_END']
        raw=('\n'.join(rows)+'\n').encode();r=m.verify_ufs_scsi(build,raw,prior)
        self.assertTrue(r['ufs_prdt_payload_verified']);self.assertTrue(r['ufs_scsi_capacity_verified']);self.assertFalse(r['ufs_native_media_read_verified'])
        self.assertEqual(r['ufs_scsi_capacity']['bytes'],unit['capacity_hint_bytes'])
        self.assertEqual(r['ufs_scsi_results'][0]['vendor'],'VENDOR')
        bad_prdt=struct.pack('<QII',pa+0x1200,0,35).hex().encode()
        for broken in (raw.replace(build.encode(),b'b'*40),raw.replace(b'writes=6',b'writes=7'),raw.replace(b'count=2',b'count=1'),
                       raw.replace(b'guards=1',b'guards=0'),raw.replace(b'residual=0',b'residual=4'),raw.replace(b'flags=00',b'flags=20'),
                       raw.replace(b'status=00',b'status=02'),raw.replace(b'offset=32',b'offset=31'),raw.replace(b'query_match=1',b'query_match=0'),
                       raw.replace(struct.pack('<QII',pa+0x2000,0,35).hex().encode(),bad_prdt),
                       raw.replace(b'bank_after=00000400',b'bank_after=00000402'),raw[:-20],
                       raw.replace(capacity.hex().encode(),struct.pack('>II',last-1,block).hex().encode())):
            with self.assertRaises(ValueError):m.verify_ufs_scsi(build,broken,prior)
        real=bytes.fromhex('000006021f000002534b68796e697800483238553734333031414d520000000044303033')
        nul_raw=raw.replace(inquiry[:32].hex().encode(),real[:32].hex().encode()).replace(inquiry[32:].hex().encode(),real[32:].hex().encode())
        decoded=m.verify_ufs_scsi(build,nul_raw,prior)['ufs_scsi_results'][0]
        self.assertEqual((decoded['vendor'],decoded['product'],decoded['revision']),('SKhynix','H28U74301AMR','D003'))
        broken=bytearray(real);broken[15]=0;broken[14]=0;broken[15]=ord('X')
        malformed=nul_raw.replace(real[:32].hex().encode(),broken[:32].hex().encode())
        with self.assertRaises(ValueError):m.verify_ufs_scsi(build,malformed,prior)
        prior['ufs_descriptor_queries_verified']=False
        with self.assertRaises(ValueError):m.verify_ufs_scsi(build,raw,prior)

    def test_actual_scsi_transport_cache_visibility_and_failures(self):
        source=r'''
#include "UfsScsi.h"
#include <cassert>
#include <cstring>
#include <map>
#include <vector>
struct Fake {
 enum Mode {Good,EmptySenseHeader,NullPadded,Timeout,WrongType,WrongTag,WrongLun,ResponseFailure,CheckCondition,
  BadSenseLength,Underflow,Overflow,Residual,BadInquiry,BadAscii,BadCapacity,BadBlock,Capacity16Sentinel,
  PartialData,DataMissing,RspOnlyInvalidate,NoInvalidate,PrdtCorrupt,RequestCorrupt,DataOverrun,GuardBefore,GuardTail,
  BadOcs,NoStart,NoStop,BaseChanged,ReadFailure};
 Mode mode=Good;uint64_t pa=0x102000000ULL;PolarisUfsNopResult owned;PolarisUfsDescriptor unit;
 alignas(4096) uint8_t cpu[UFS_NOP_BYTES]{},device[UFS_NOP_BYTES]{};
 std::map<unsigned,uint32_t> regs{{0x50,0x02000000},{0x54,1},{0x60,0},{0x58,0},{0x78,0},{0x80,0},{0x24,0},{0x34,1},{0x30,0x10f},{0x20,0x401}};
 unsigned polls=0,doorbells=0,cacheOps=0,delays=0;bool complete=false,prepared=false;
 std::vector<std::pair<unsigned,uint32_t>> writes;
 Fake(){owned.state=PolarisUfsNopResult::Success;owned.guards=owned.responseRead=owned.smmuUnchanged=owned.stopped=true;owned.dma=owned.finalBase=pa;
  unit.state=PolarisUfsDescriptor::Success;unit.idn=2;unit.length=35;unit.data[0]=35;unit.data[1]=2;unit.data[3]=1;unit.data[10]=12;unit.data[15]=1;}
 void response(){
  auto *p=device+UFS_NOP_RSP;std::memset(p,0,32);p[0]=mode==WrongType?0x36:0x21;
  if(mode==WrongTag)p[3]=1;if(mode==WrongLun)p[2]=1;if(mode==ResponseFailure)p[6]=1;
  if(mode==Underflow)p[1]=0x20;if(mode==Overflow)p[1]=0x40;if(mode==Residual)p[15]=4;
  if(mode==EmptySenseHeader){p[11]=2;p[32]=p[33]=0;}
  if(mode==CheckCondition){p[7]=2;p[11]=20;p[32]=0;p[33]=18;std::memset(p+34,0,18);p[34]=0x70;p[36]=6;p[46]=0x29;}
  if(mode==BadSenseLength){p[11]=20;p[32]=0;p[33]=19;}
  auto *d=device+UFS_SCSI_DATA;
  if(mode!=DataMissing){
   if(device[UFS_NOP_UCD+16]==0x12){const uint8_t inq[]={0,0,6,2,31,0,0,0};std::memcpy(d,inq,8);std::memcpy(d+8,"VENDOR  MODEL           0001",mode==PartialData?27:28);
    if(mode==NullPadded){const uint8_t real[]={0,0,6,2,31,0,0,2,0x53,0x4b,0x68,0x79,0x6e,0x69,0x78,0,0x48,0x32,0x38,0x55,0x37,0x34,0x33,0x30,0x31,0x41,0x4d,0x52,0,0,0,0,0x44,0x30,0x30,0x33};std::memcpy(d,real,36);}
    if(mode==BadInquiry)d[0]=0x1f;if(mode==BadAscii)d[8]=0;}
   else {const uint8_t cap[]={0,0xff,0xff,0xff,0,0,0x10,0};std::memcpy(d,cap,8);
    if(mode==BadCapacity)d[3]--;if(mode==BadBlock)d[6]=2;if(mode==Capacity16Sentinel)std::memset(d,0xff,4);}
  }
  if(mode==PrdtCorrupt)device[UFS_SCSI_PRDT+12]^=1;
  if(mode==RequestCorrupt)device[UFS_NOP_UCD+16]^=1;
  if(mode==DataOverrun)device[UFS_SCSI_DATA+device[UFS_NOP_UCD+15]]^=1;
  if(mode==GuardBefore)device[UFS_SCSI_DATA-1]^=1;
  if(mode==GuardTail)device[UFS_NOP_BYTES-1]^=1;
  device[8]=mode==BadOcs?2:0;regs[0x58]=0;complete=true;
  if(mode==BaseChanged)regs[0x50]+=4096;
 }
 static bool read(void *p,unsigned off,uint32_t *v){auto &f=*static_cast<Fake *>(p);assert(f.regs.count(off));
  if(f.mode==ReadFailure&&off==0x34)return false;
  if(off==0x58&&f.doorbells&&!f.complete){f.polls++;if(f.mode!=Timeout&&f.polls>=2)f.response();}
  *v=f.regs.at(off);return true;}
 static void write(void *p,unsigned off,uint32_t v){auto &f=*static_cast<Fake *>(p);assert(off==0x60||off==0x58);f.writes.push_back({off,v});
  if(off==0x60&&v==1&&f.mode==NoStart)return;
  if(off==0x60&&v==0&&f.mode==NoStop)return;
  if(off==0x58){assert(v==1&&f.prepared&&f.regs[0x60]==1);f.doorbells++;
   const uint8_t utrd[]={0,0,0,0x15,0,0,0,0,15,0,0,0,0,0,0,0,0,0x10,0,2,1,0,0,0,0x80,0,0x80,0,1,0,0,1};
   assert(!std::memcmp(utrd,f.device,32));unsigned length=f.device[UFS_NOP_UCD+15];assert(length==8||length==36);
   const uint8_t request[]={1,0x40,0,0,0,0,0,0,0,0,0,0,0,0,0,static_cast<uint8_t>(length),static_cast<uint8_t>(length==36?0x12:0x25),0,0,0,static_cast<uint8_t>(length==36?36:0),0,0,0,0,0,0,0,0,0,0,0};
   assert(!std::memcmp(request,f.device+UFS_NOP_UCD,32));
   const uint8_t prdt[]={0,0x20,0,2,1,0,0,0,0,0,0,0,static_cast<uint8_t>(length-1),0,0,0};
   assert(!std::memcmp(prdt,f.device+UFS_SCSI_PRDT,16));
  }
  f.regs[off]=v;}
 static void delay(void *p,unsigned us){auto &f=*static_cast<Fake *>(p);assert(us==100);f.delays+=us;assert(f.delays<=700000);}
 static void cache(void *p,unsigned op){auto &f=*static_cast<Fake *>(p);f.cacheOps++;
  if(op==1){assert(!f.doorbells);std::memcpy(f.device,f.cpu,sizeof(f.cpu));f.prepared=true;}
  else {assert(op==2&&f.complete);if(f.mode==NoInvalidate)return;
   if(f.mode==RspOnlyInvalidate){std::memcpy(f.cpu,f.device,UFS_SCSI_DATA);std::memcpy(f.cpu+UFS_SCSI_DATA+4096,f.device+UFS_SCSI_DATA+4096,UFS_NOP_BYTES-UFS_SCSI_DATA-4096);}
   else std::memcpy(f.cpu,f.device,sizeof(f.cpu));}
 }
 PolarisUfsScsiResult run(PolarisUfsScsiKind kind=PolarisUfsScsiKind::Inquiry,bool enabled=true){return polaris_ufs_scsi_read({this,read,write,delay,cache},owned,unit,cpu,pa,kind,enabled);}
};
int main(){
 for(auto kind:{PolarisUfsScsiKind::Inquiry,PolarisUfsScsiKind::Capacity10})for(auto mode:{Fake::Good,Fake::EmptySenseHeader,Fake::NullPadded}){
  Fake f;f.mode=mode;auto r=f.run(kind);assert(r.state==PolarisUfsScsiResult::Success&&r.guards&&r.stopped&&r.ocs==0&&r.bytes==(kind==PolarisUfsScsiKind::Inquiry?36u:8u));
  assert(r.writes==3&&r.doorbells==1&&r.polls==2&&r.dataPa==f.pa+0x2000&&f.cacheOps==2);
  assert((f.writes==std::vector<std::pair<unsigned,uint32_t>>{{0x60,1},{0x58,1},{0x60,0}}));
  if(kind==PolarisUfsScsiKind::Capacity10)assert(r.lastLba==0xffffff&&r.blockBytes==4096&&r.capacityBytes==0x1000000000ULL);
 }
 Fake disabled;assert(disabled.run(PolarisUfsScsiKind::Inquiry,false).state==PolarisUfsScsiResult::Disabled&&disabled.writes.empty());
 Fake invalid;assert(invalid.run(static_cast<PolarisUfsScsiKind>(2)).state==PolarisUfsScsiResult::InvalidRequest&&invalid.writes.empty());
 for(unsigned mode=0;mode<9;mode++){Fake f;
  if(mode==0)f.owned.guards=false;if(mode==1)f.owned.smmuUnchanged=false;if(mode==2)f.owned.finalDb=1;
  if(mode==3)f.unit.state=PolarisUfsDescriptor::ResponseError;if(mode==4)f.unit.data[3]=0;if(mode==5)f.unit.data[10]=32;
  if(mode==6)f.regs[0x60]=1;if(mode==7)f.regs[0x24]=1;if(mode==8)f.regs[0x54]=0;
  auto r=f.run();assert(r.state!=PolarisUfsScsiResult::Success&&f.writes.empty()&&!f.cacheOps);
 }
 for(unsigned mode=Fake::Timeout;mode<=Fake::ReadFailure;mode++){Fake f;f.mode=static_cast<Fake::Mode>(mode);
  auto kind=mode==Fake::BadCapacity||mode==Fake::BadBlock||mode==Fake::Capacity16Sentinel?PolarisUfsScsiKind::Capacity10:PolarisUfsScsiKind::Inquiry;
  auto r=f.run(kind);assert(r.state!=PolarisUfsScsiResult::Success&&f.doorbells<=1);
  if(mode==Fake::Timeout)assert(r.state==PolarisUfsScsiResult::Timeout&&r.polls==5000&&r.finalDb==1&&r.stopped);
  if(mode==Fake::NoStart||mode==Fake::ReadFailure)assert(!f.doorbells);
  if(mode==Fake::NoStop||mode==Fake::BaseChanged)assert(r.state==PolarisUfsScsiResult::CleanupFailed);
  if(mode==Fake::CheckCondition)assert(r.state==PolarisUfsScsiResult::ScsiStatus&&r.status==2&&r.senseLength==18&&r.sense[2]==6&&r.sense[12]==0x29);
  if(mode==Fake::PartialData||mode==Fake::RspOnlyInvalidate||mode==Fake::DataMissing)assert(r.state==PolarisUfsScsiResult::PayloadError);
 }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);(d/'scsi.cpp').write_text(source)
            r=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                              '-I',ROOT/'drivers/PolarisUsb',d/'scsi.cpp','-o',d/'scsi'],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([d/'scsi'],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)


if __name__=='__main__':unittest.main()
