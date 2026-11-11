"""Owned-queue NOP with an independent noncoherent DMA peer and fault injection."""
import importlib.util
from pathlib import Path
import json, shutil, subprocess, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]

class UfsNopTests(unittest.TestCase):
    def test_exact_build_nop_evidence_and_qualification_scope(self):
        spec=importlib.util.spec_from_file_location('usb',ROOT/'scripts/check-polaris-usb.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);build='a'*40
        prior=json.loads((ROOT/'research/ufs-queue-smmu-snapshot.json').read_text())
        prior.update(ufs_controller_registers_verified=True,ufs_readonly_inventory_verified=True)
        status=b'POLARIS_USB_STATUS dma=103000000 smmu=80000740/00000004\n'
        raw=(f'POLARIS_UFS_NOP build={build} state=ok bytes=16384 writes=6 doorbells=1 polls=3 cache_prepared=1 response_read=1 guards=1 stopped=1 smmu_unchanged=1 media_commands=0 smmu_writes=0 retained=1\n'
             'POLARIS_UFS_NOP_DMA pa=0000000102000000 old_base=00000000c989a000 final_base=0000000102000000 usb_pa=0000000103000000 ocs=0 rsp0=20000000 rsp1=00000000 rsp2=00000000\n'
             'POLARIS_UFS_NOP_REG is_before=00000400 is_after=00000401 run=00000000 db=00000000 ie=00000000 hcs=0000010f hce=00000001\n'
             'POLARIS_UFS_NOP_FAULT global_before=00000000 bank_before=00000400 global_after=00000000 bank_after=00000400 far=0000000000000000 syn=00000000\n'
             'POLARIS_UFS_NOP_END\n').encode()
        result=m.verify_ufs_nop(build,raw,prior,status)
        self.assertTrue(result['ufs_owned_nop_verified']);self.assertTrue(result['ufs_dma_verified'])
        self.assertFalse(result['ufs_native_media_read_verified']);self.assertFalse(result['ufs_exclusive_firmware_quiescence_verified'])
        for broken in (raw.replace(build.encode(),b'b'*40),raw.replace(b'writes=6',b'writes=5'),
                       raw.replace(b'doorbells=1',b'doorbells=2'),raw.replace(b'guards=1',b'guards=0'),
                       raw.replace(b'cache_prepared=1',b'cache_prepared=0'),raw.replace(b'retained=1',b'retained=0'),
                       raw.replace(b'polls=3',b'polls=5001'),raw.replace(b'smmu_writes=0',b'smmu_writes=1'),
                       raw.replace(b'media_commands=0',b'media_commands=1'),raw.replace(b'rsp0=20000000',b'rsp0=20000001'),
                       raw.replace(b'ocs=0',b'ocs=1'),raw.replace(b'run=00000000',b'run=00000001'),
                       raw.replace(b'bank_after=00000400',b'bank_after=00000402'),
                       raw.replace(b'final_base=0000000102000000',b'final_base=00000000c989a000'),
                       raw.replace(b'pa=0000000102000000',b'pa=0000000103000000'),raw[:-12]):
            with self.assertRaises(ValueError):m.verify_ufs_nop(build,broken,prior,status)

    def test_actual_nop_with_separate_cpu_device_memory_and_faults(self):
        source=r'''
#include "UfsNop.h"
#include <cassert>
#include <cstring>
#include <map>
#include <vector>
struct Fake {
 enum Mode {Good,IgnoreStop,IgnoreBaseHi,IgnoreStart,Timeout,BadOcs,BadTag,BadType,Overrun,
            CorruptDescriptor,IgnoreInvalidate,IgnoreCleanup,BadLength,ChangedBefore,ChangedAfterStop};
 Mode mode=Good;PolarisUfsSnapshot base;PolarisUfsInventory inv;
 alignas(4096) uint8_t cpu[UFS_NOP_BYTES]{},device[UFS_NOP_BYTES]{};
 std::map<unsigned,uint32_t> regs{{0x20,0x400},{0x24,0},{0x50,0xc989a000},{0x54,0},{0x58,0},{0x60,1},
  {0x70,0xc989b000},{0x74,0},{0x78,0},{0x80,0},{0x30,0x10f},{0x34,1}};
 std::vector<std::pair<unsigned,uint32_t>> writes;
 unsigned cacheOps=0,dbWrites=0,polls=0,delayed=0;bool prepared=false,completed=false;
 uint64_t pa=0x102000000ULL,usb=0x103000000ULL;
 Fake(){
  base.state=PolarisUfsSnapshot::Ready;base.regs[0]=0x1587031f;base.regs[1]=0x210;
  inv.state=PolarisUfsInventory::Snapshot;inv.matches=1;inv.coverage=0xffff;inv.idr[2]=0x5555;
  inv.groups=inv.scanned=76;inv.validGroups=8;inv.pages=64;inv.banks=45;inv.cr0=0x2d0406;
  auto &m=inv.match[0];m.smr=0x800f0100;m.s2cr=m.bank=2;m.sctlr=0xe0;m.cbar=0x10000;m.cba2r=1;m.shared=1;
  unsigned off[]={0x20,0x24,0x50,0x54,0x58,0x60,0x70,0x74,0x78,0x80,0x30,0x34};
  for(unsigned i=0;i<12;i++)inv.queues[0][i]=inv.queues[1][i]=regs[off[i]];
 }
 static bool read(void *p,unsigned off,uint32_t *value){
  auto &f=*static_cast<Fake *>(p);assert(f.regs.count(off));
  if(f.mode==ChangedBefore&&off==0x20){*value=f.regs[off]|1;return true;}
  if(off==0x58&&f.dbWrites&&!f.completed){
   f.polls++;
   if(f.mode!=Timeout&&f.polls>=3){
    f.device[8]=f.mode==BadOcs?6:0;
    std::memset(f.device+UFS_NOP_RSP,0,32);
    f.device[UFS_NOP_RSP]=f.mode==BadType?0x21:0x20;
    f.device[UFS_NOP_RSP+3]=f.mode==BadTag?1:0;
    if(f.mode==BadLength)f.device[UFS_NOP_RSP+11]=1;
    if(f.mode==Overrun)f.device[UFS_NOP_RSP+512]^=1;
    if(f.mode==CorruptDescriptor)f.device[0]^=1;
    f.regs[0x58]=0;f.regs[0x20]|=1;f.completed=true;
   }
  }
  *value=f.regs.at(off);return true;
 }
 static void write(void *p,unsigned off,uint32_t v){
  auto &f=*static_cast<Fake *>(p);assert(off==0x50||off==0x54||off==0x58||off==0x60);
  f.writes.push_back({off,v});
  if(off==0x60&&v==0){
   if(f.mode==IgnoreStop || (f.mode==IgnoreCleanup&&f.dbWrites))return;
   f.regs[off]=0;if(f.mode==ChangedAfterStop)f.regs[0x58]=2;return;
  }
  if(off==0x54&&f.mode==IgnoreBaseHi)return;
  if(off==0x60&&v==1&&f.mode==IgnoreStart)return;
  if(off==0x58){
   assert(v==1&&f.prepared&&f.regs[0x60]==1);f.dbWrites++;
   assert(((uint64_t(f.regs[0x54])<<32)|f.regs[0x50])==f.pa);
   // Independent golden UFSHCI 2.1 UTRD: CT=1, I=1, DD=0, OCS=15;
   // UCD at 0x102001000, 512-byte response at +512, zero PRDT length.
   const uint8_t golden[]={0,0,0,0x11,0,0,0,0,15,0,0,0,0,0,0,0,
    0,0x10,0,2,1,0,0,0,0x80,0,0x80,0,0,0,0,1};
   assert(!std::memcmp(f.device,golden,32));
   for(unsigned i=UFS_NOP_UCD;i<UFS_NOP_UCD+512;i++)assert(f.device[i]==0);
   for(unsigned i=UFS_NOP_RSP;i<UFS_NOP_RSP+512;i++)assert(f.device[i]==0x5a);
  }
  f.regs[off]=v;
 }
 static void delay(void *p,unsigned us){auto &f=*static_cast<Fake *>(p);assert(us==100);f.delayed+=us;assert(f.delayed<=700000);}
 static void cache(void *p,unsigned op){
  auto &f=*static_cast<Fake *>(p);f.cacheOps++;
  if(op==1){assert(!f.dbWrites);std::memcpy(f.device,f.cpu,sizeof(f.cpu));f.prepared=true;}
  else {assert(op==2&&f.completed&&!f.regs[0x58]);if(f.mode!=IgnoreInvalidate)std::memcpy(f.cpu,f.device,sizeof(f.cpu));}
 }
 PolarisUfsNopResult run(bool enabled=true){return polaris_ufs_nop({this,read,write,delay,cache},base,inv,cpu,pa,usb,enabled);}
};
int main(){
 for(uint32_t format=0;format<4;format++)assert(!polaris_ufs_nop_fault(0,format<<9));
 for(unsigned bit:{1u,2u,3u,4u,5u,6u,7u,8u,30u,31u})assert(polaris_ufs_nop_fault(0,(1u<<bit)|0x400u));
 assert(polaris_ufs_nop_fault(2,0x400));
 Fake disabled;auto d=disabled.run(false);assert(d.state==PolarisUfsNopResult::Disabled&&disabled.writes.empty()&&!disabled.cacheOps);
 Fake good;auto r=good.run();assert(r.state==PolarisUfsNopResult::Success);
 assert(r.ocs==0&&r.rsp[0]==0x20000000&&r.rsp[1]==0&&r.rsp[2]==0&&r.guards);
 assert(r.writes==6&&r.doorbells==1&&r.polls==3&&r.responseRead&&r.cachePrepared&&r.stopped);
 assert(r.finalBase==good.pa&&r.finalBase!=r.oldBase&&r.finalRun==0&&r.finalDb==0&&r.finalIe==0);
 assert(good.regs[0x70]==0xc989b000&&good.regs[0x80]==0&&good.regs[0x34]==1);
 assert((good.writes==std::vector<std::pair<unsigned,uint32_t>>{{0x60,0},{0x50,0x02000000},{0x54,1},{0x60,1},{0x58,1},{0x60,0}}));
 for(unsigned mode=0;mode<12;mode++){
  Fake f;
  if(mode==0)f.inv.stable=false;if(mode==1)f.inv.queuesStable=false;if(mode==2)f.inv.truncated=true;
  if(mode==3)f.inv.overlap=true;if(mode==4)f.inv.match[0].bank=4;if(mode==5)f.inv.match[0].sctlr|=1;
  if(mode==6)f.inv.queues[1][1]=1;if(mode==7)f.inv.queues[1][4]=1;if(mode==8)f.inv.queues[1][9]=1;
  if(mode==9)f.base.regs[0]&=~(1u<<24);if(mode==10)f.inv.queues[1][0]|=4;if(mode==11)f.inv.idr[2]=0x4444;
  assert(f.run().state==PolarisUfsNopResult::PreflightFailed&&f.writes.empty()&&!f.cacheOps);
 }
 for(uint64_t address:{0ULL,0xffffffffULL,0x102000001ULL,0x140000000ULL,static_cast<unsigned long long>(UINT64_MAX),0x103000000ULL}){
  Fake f;f.pa=address;assert(f.run().state==PolarisUfsNopResult::DmaInvalid&&f.writes.empty()&&!f.cacheOps);
 }
 const Fake::Mode modes[]={Fake::IgnoreStop,Fake::IgnoreBaseHi,Fake::IgnoreStart,Fake::Timeout,Fake::BadOcs,Fake::BadTag,
  Fake::BadType,Fake::Overrun,Fake::CorruptDescriptor,Fake::IgnoreInvalidate,Fake::IgnoreCleanup,Fake::BadLength,
  Fake::ChangedBefore,Fake::ChangedAfterStop};
 const PolarisUfsNopResult::State expected[]={PolarisUfsNopResult::StopTimeout,PolarisUfsNopResult::BaseReadback,
  PolarisUfsNopResult::StartTimeout,PolarisUfsNopResult::CommandTimeout,PolarisUfsNopResult::CommandError,
  PolarisUfsNopResult::ResponseError,PolarisUfsNopResult::ResponseError,PolarisUfsNopResult::GuardError,
  PolarisUfsNopResult::GuardError,PolarisUfsNopResult::CommandError,PolarisUfsNopResult::CleanupFailed,
  PolarisUfsNopResult::ResponseError,PolarisUfsNopResult::StateChanged,PolarisUfsNopResult::StateChanged};
 for(unsigned i=0;i<sizeof(modes)/sizeof(modes[0]);i++){
  Fake f;f.mode=modes[i];auto result=f.run();assert(result.state==expected[i]);assert(f.dbWrites<=1);
  if(f.mode==Fake::IgnoreStop){assert(f.writes.size()==1&&!f.dbWrites);}
  if(f.mode==Fake::Timeout){assert(result.polls==5000&&result.finalDb==1&&!result.responseRead&&result.stopped);}
  for(auto w:f.writes)if(w.first==0x50)assert(w.second!=0xc989a000); // no restoration of firmware pointers
 }
 auto after=good.inv;assert(polaris_ufs_nop_same_smmu(good.inv,after));
 after.match[0].sctlr|=1;assert(!polaris_ufs_nop_same_smmu(good.inv,after));
}
'''
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);(d/'nop.cpp').write_text(source)
            result=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror',
                '-fsanitize=address,undefined','-I',ROOT/'drivers/PolarisUsb',d/'nop.cpp','-o',d/'nop'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            run=subprocess.run([d/'nop'],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
