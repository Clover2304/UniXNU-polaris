"""Read-only descriptor requests with a separate noncoherent DMA peer."""
import importlib.util
from pathlib import Path
import json,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class UfsQueryTests(unittest.TestCase):
    def test_exact_query_evidence_and_descriptor_bounds(self):
        spec=importlib.util.spec_from_file_location('usb',ROOT/'scripts/check-polaris-usb.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);build='a'*40
        prior=json.loads((ROOT/'research/ufs-nop-result.json').read_text());prior['source_commit']=build
        data0=bytearray(64);data0[0]=64;data0[6]=3;data0[16:18]=b'\x02\x10';data0[24:26]=b'\x01\xce'
        data1=bytearray(35);data1[0]=35;data1[1]=2;data1[3]=1;data1[10]=12;data1[11:19]=(0x1000000).to_bytes(8,'big')
        rows=[f"POLARIS_UFS_QUERY build={build} state=ok count=2 writes=6 doorbells=2 smmu_unchanged=1 sector_commands=0 smmu_writes=0 retained=1 pa={prior['ufs_nop_dma']['pa']:016x}"]
        for n,(idn,d) in enumerate(((0,data0),(2,data1))):
            u=bytearray(32);u[0]=0x36;u[11]=len(d);u[12]=1;u[13]=idn;u[19]=len(d)
            rows.append(f'POLARIS_UFS_DESC n={n} idn={idn:02x} index=00 selector=00 state=ok ocs=0 response=00 requested=255 segment={len(d)} osf={len(d)} guards=1 stopped=1 polls=2 run=00000000 db=00000000 is=00000401 upiu={u.hex()}')
            for offset in range(0,len(d),32):rows.append(f'POLARIS_UFS_DESC_DATA n={n} offset={offset} hex={d[offset:offset+32].hex()}')
        rows+=['POLARIS_UFS_QUERY_FAULT global_before=00000000 bank_before=00000400 global_after=00000000 bank_after=00000400','POLARIS_UFS_QUERY_END']
        raw=('\n'.join(rows)+'\n').encode();result=m.verify_ufs_query(build,raw,prior)
        self.assertTrue(result['ufs_descriptor_queries_verified']);self.assertFalse(result['ufs_prdt_payload_verified']);self.assertFalse(result['ufs_native_media_read_verified'])
        self.assertEqual(result['ufs_read_descriptor_results'][0]['manufacturer_id'],0x1ce)
        self.assertEqual(result['ufs_read_descriptor_results'][1]['capacity_hint_bytes'],0x1000000<<12)
        for broken in (raw.replace(build.encode(),b'b'*40),raw.replace(b'writes=6',b'writes=9'),raw.replace(b'count=2',b'count=3'),
                       raw.replace(b'segment=64',b'segment=256'),raw.replace(b'osf=64',b'osf=63'),raw.replace(b'offset=32',b'offset=33'),
                       raw.replace(b'guards=1',b'guards=0'),raw.replace(b'idn=02',b'idn=05'),
                       raw.replace(b'bank_after=00000400',b'bank_after=00000402'),raw[:-20]):
            with self.assertRaises(ValueError):m.verify_ufs_query(build,broken,prior)

    def test_actual_transport_bounds_guards_and_failure_paths(self):
        source=r'''
#include "UfsQuery.h"
#include <cassert>
#include <cstring>
#include <map>
#include <vector>
struct Fake {
 enum Mode {Good,Timeout,WrongType,WrongTag,WrongOpcode,WrongIdn,WrongIndex,Rejected,Long,Zero,
  ShortOsf,LargeOsf,WrongLength,WrongDescType,WrongUnitIndex,BadBlock,Overrun,RequestCorrupt,NoInvalidate,BadOcs,NoStart,NoStop};
 Mode mode=Good;uint64_t pa=0x102000000ULL;PolarisUfsNopResult owned;
 alignas(4096) uint8_t cpu[UFS_NOP_BYTES]{},device[UFS_NOP_BYTES]{};
 std::map<unsigned,uint32_t> regs{{0x50,0x02000000},{0x54,1},{0x60,0},{0x58,0},{0x78,0},{0x80,0},{0x24,0},{0x34,1},{0x30,0x10f},{0x20,0x401}};
 unsigned polls=0,doorbells=0,cacheOps=0,delays=0;bool complete=false,prepared=false;
 std::vector<std::pair<unsigned,uint32_t>> writes;
 Fake(){owned.state=PolarisUfsNopResult::Success;owned.guards=owned.responseRead=owned.smmuUnchanged=owned.stopped=true;owned.dma=owned.finalBase=pa;}
 void response(){
  unsigned idn=device[UFS_NOP_UCD+13],len=idn==0?64:35;
  auto *r=device+UFS_NOP_RSP;std::memset(r,0,32+len);r[0]=mode==WrongType?0x20:0x36;
  if(mode==WrongTag)r[3]=1;if(mode==Rejected)r[6]=0xf9;
  unsigned advertised=mode==Long?256:mode==Zero?0:len;r[10]=advertised>>8;r[11]=advertised;
  r[12]=mode==WrongOpcode?2:1;r[13]=mode==WrongIdn?7:idn;if(mode==WrongIndex)r[14]=1;
  unsigned osf=mode==ShortOsf?len-1:mode==LargeOsf?256:len;r[18]=osf>>8;r[19]=osf;
  auto *d=r+32;d[0]=mode==WrongLength?len-1:len;d[1]=mode==WrongDescType?7:idn;
  if(idn==0){d[6]=3;d[0x10]=2;d[0x11]=0x10;d[0x18]=1;d[0x19]=0xce;}
  else {d[2]=mode==WrongUnitIndex?1:0;d[3]=1;d[10]=mode==BadBlock?40:12;d[15]=1;}
  if(mode==Overrun)device[UFS_NOP_RSP+512]^=1;
  if(mode==RequestCorrupt)device[UFS_NOP_UCD+5]^=1;
  device[8]=mode==BadOcs?6:0;regs[0x58]=0;complete=true;
 }
 static bool read(void *p,unsigned off,uint32_t *v){auto &f=*static_cast<Fake *>(p);assert(f.regs.count(off));
  if(off==0x58&&f.doorbells&&!f.complete){f.polls++;if(f.mode!=Timeout&&f.polls>=2)f.response();}
  *v=f.regs.at(off);return true;
 }
 static void write(void *p,unsigned off,uint32_t v){auto &f=*static_cast<Fake *>(p);assert(off==0x60||off==0x58);f.writes.push_back({off,v});
  if(off==0x60&&v==1&&f.mode==NoStart)return;
  if(off==0x60&&v==0&&f.mode==NoStop)return;
  if(off==0x58){assert(v==1&&f.prepared&&f.regs[0x60]==1);f.doorbells++;
   const uint8_t golden[]={0,0,0,0x11,0,0,0,0,15,0,0,0,0,0,0,0,0,0x10,0,2,1,0,0,0,0x80,0,0x80,0,0,0,0,1};
   assert(!std::memcmp(golden,f.device,32));
   const uint8_t request[]={0x16,0,0,0,0,1,0,0,0,0,0,0,1,f.device[UFS_NOP_UCD+13],0,0,0,0,0,255,0,0,0,0,0,0,0,0,0,0,0,0};
   assert(!std::memcmp(request,f.device+UFS_NOP_UCD,32));
  }
  f.regs[off]=v;
 }
 static void delay(void *p,unsigned us){auto &f=*static_cast<Fake *>(p);assert(us==100);f.delays+=us;assert(f.delays<=700000);}
 static void cache(void *p,unsigned op){auto &f=*static_cast<Fake *>(p);f.cacheOps++;
  if(op==1){assert(!f.doorbells);std::memcpy(f.device,f.cpu,sizeof(f.cpu));f.prepared=true;}
  else {assert(op==2&&f.complete);if(f.mode!=NoInvalidate)std::memcpy(f.cpu,f.device,sizeof(f.cpu));}
 }
 PolarisUfsDescriptor run(unsigned idn=0,bool enabled=true){return polaris_ufs_read_descriptor({this,read,write,delay,cache},owned,cpu,pa,idn,enabled);}
};
int main(){
 for(unsigned idn:{0u,2u}){Fake f;auto r=f.run(idn);assert(r.state==PolarisUfsDescriptor::Success&&r.ocs==0&&r.guards&&r.stopped);
  assert(r.writes==3&&r.doorbells==1&&r.polls==2&&r.length==(idn==0?64u:35u));
  assert(r.data[1]==idn&&r.finalRun==0&&r.finalDb==0&&f.cacheOps==2);
  if(idn==0)assert(polaris_ufs_be16(r.data+0x10)==0x210);else assert(polaris_ufs_be64(r.data+11)==0x1000000);
  assert((f.writes==std::vector<std::pair<unsigned,uint32_t>>{{0x60,1},{0x58,1},{0x60,0}}));
 }
 Fake disabled;assert(disabled.run(0,false).state==PolarisUfsDescriptor::Disabled&&disabled.writes.empty());
 for(unsigned idn:{1u,5u,0xc4u}){Fake f;assert(f.run(idn).state==PolarisUfsDescriptor::InvalidRequest&&f.writes.empty()&&!f.cacheOps);}
 for(unsigned mode=0;mode<8;mode++){Fake f;
  if(mode==0)f.owned.state=PolarisUfsNopResult::CommandTimeout;if(mode==1)f.owned.guards=false;
  if(mode==2)f.owned.smmuUnchanged=false;if(mode==3)f.owned.finalDb=1;
  if(mode==4)f.regs[0x60]=1;if(mode==5)f.regs[0x58]=1;if(mode==6)f.regs[0x24]=1;if(mode==7)f.regs[0x54]=0;
  auto r=f.run();assert(r.state!=PolarisUfsDescriptor::Success&&f.writes.empty()&&!f.cacheOps);
 }
 for(unsigned mode=Fake::Timeout;mode<=Fake::NoStop;mode++){Fake f;f.mode=static_cast<Fake::Mode>(mode);
  auto r=f.run(mode==Fake::WrongUnitIndex||mode==Fake::BadBlock?2:0);
  assert(r.state!=PolarisUfsDescriptor::Success&&f.doorbells<=1);
  if(mode==Fake::Timeout)assert(r.state==PolarisUfsDescriptor::Timeout&&r.polls==5000&&r.finalDb==1&&r.stopped);
  if(mode==Fake::NoStart)assert(!f.doorbells);
  if(mode==Fake::NoStop)assert(r.state==PolarisUfsDescriptor::CleanupFailed);
 }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);(d/'query.cpp').write_text(source)
            r=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                '-I',ROOT/'drivers/PolarisUsb',d/'query.cpp','-o',d/'query'],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([d/'query'],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
