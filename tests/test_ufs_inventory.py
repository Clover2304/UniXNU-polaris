"""Actual inventory MMIO access bounds and candidate-only evidence grading."""
import importlib.util
from pathlib import Path
import shutil, subprocess, tempfile, unittest

ROOT=Path(__file__).resolve().parents[1]

class UfsInventoryTests(unittest.TestCase):
    def test_evidence_keeps_candidate_sid_dma_and_ownership_unqualified(self):
        spec=importlib.util.spec_from_file_location('usb',ROOT/'scripts/check-polaris-usb.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);build='a'*40
        raw=(f'POLARIS_UFS_INVENTORY build={build} state=snapshot gcc_reads=12 controller_reads=24 smmu_reads=170 writes=0 media_commands=0\n'
             'POLARIS_UFS_QUEUE sample=0 is=00000000 ie=00000000 trl=0000000088000000 tr_db=00000000 tr_run=00000001 tmrl=0000000088001000 tm_db=00000000 tm_run=00000001 hcs=0000010f hce=00000001\n'
             'POLARIS_UFS_QUEUE sample=1 is=00000000 ie=00000000 trl=0000000088000000 tr_db=00000000 tr_run=00000001 tmrl=0000000088001000 tm_db=00000000 tm_run=00000001 hcs=0000010f hce=00000001\n'
             'POLARIS_UFS_IOMMU idr0=4c017e4c idr1=5000002d idr2=00005555 cr0=002d0406 groups=76 scanned=76 valid=2 pages=64 banks=45 truncated=0 stable=1 queues_stable=1 matches=1 overlap=0 coverage=ffff sid_base=0100 sid_count=16\n'
             'POLARIS_UFS_MATCH n=0 slot=1 smr=800f0100 s2cr=00000004 coverage=ffff bank=4 shared=1 sctlr=000000e0 cbar=0001f000 cba2r=00000001\n'
             'POLARIS_UFS_INVENTORY_END\n').encode()
        good=m.verify_ufs_inventory(build,raw)
        for flag in ('ufs_readonly_inventory_verified','ufs_candidate_mapping_stable','ufs_candidate_bypass_register_shape','ufs_idle_queue_samples'):
            self.assertTrue(good[flag],flag)
        for flag in ('ufs_candidate_sid_hardware_confirmed','ufs_queue_ownership_verified','ufs_dma_verified','ufs_native_media_read_verified'):
            self.assertFalse(good[flag],flag)
        for broken in (raw.replace(build.encode(),b'b'*40),raw.replace(b'smmu_reads=170',b'smmu_reads=169'),
                       raw.replace(b'pages=64',b'pages=32'),raw.replace(b'bank=4 shared',b'bank=45 shared'),
                       raw.replace(b'scanned=76',b'scanned=172'),raw.replace(b'coverage=ffff bank',b'coverage=0001 bank'),
                       raw.replace(b'writes=0',b'writes=1'),raw.replace(b'media_commands=0',b'media_commands=1'),
                       raw.replace(b'matches=1',b'matches=2'),raw[:-15]):
            with self.assertRaises(ValueError):m.verify_ufs_inventory(build,broken)
        changed=m.verify_ufs_inventory(build,raw.replace(b' stable=1 ',b' stable=0 '))
        self.assertFalse(changed['ufs_candidate_bypass_register_shape'])
        translated=m.verify_ufs_inventory(build,raw.replace(b'sctlr=000000e0',b'sctlr=000000e1'))
        self.assertFalse(translated['ufs_candidate_bypass_register_shape'])
        truncated=raw.replace(b'smmu_reads=170',b'smmu_reads=274').replace(b'idr0=4c017e4c',b'idr0=4c017eac')
        truncated=truncated.replace(b'groups=76 scanned=76',b'groups=172 scanned=128').replace(b'truncated=0',b'truncated=1')
        self.assertFalse(m.verify_ufs_inventory(build,truncated)['ufs_candidate_bypass_register_shape'])
        line=b'POLARIS_UFS_MATCH n=1 slot=3 smr=800f0100 s2cr=00000004 coverage=ffff bank=4 shared=2 sctlr=000000e0 cbar=0001f000 cba2r=00000001\n'
        overlap=raw.replace(b'smmu_reads=170',b'smmu_reads=178').replace(b'valid=2',b'valid=3').replace(b'matches=1 overlap=0',b'matches=2 overlap=1')
        overlap=overlap.replace(b'shared=1',b'shared=2').replace(b'POLARIS_UFS_INVENTORY_END',line+b'POLARIS_UFS_INVENTORY_END')
        self.assertFalse(m.verify_ufs_inventory(build,overlap)['ufs_candidate_bypass_register_shape'])

    def test_read_only_accesses_and_all_failure_boundaries(self):
        source=r'''
#include "UfsInventory.h"
#include <cassert>
#include <map>
struct Fake {
 std::map<unsigned,uint32_t> gcc{{0x77000,0},{0x77004,0xf8222000},{0x77008,0x70400},
  {0x7700c,0x4221},{0x77010,1},{0x82024,1}};
 std::map<unsigned,uint32_t> ufs{{0x20,0},{0x24,0},{0x50,0x88000000},{0x54,0},
  {0x58,0},{0x60,1},{0x70,0x88001000},{0x74,0},{0x78,0},{0x80,1},{0x30,0x10f},{0x34,1}};
 std::map<unsigned,uint32_t> smmu{{0,0x2d0406},{0x20,0x4c017e4c},{0x24,0x5000002d},{0x28,0x5555},
  {0x44000,0xe0},{0x1010,0x1f000},{0x1810,1}};
 unsigned gateReads=0,queueReads=0,matchReads=0,contextReads=0,totalReads=0;
 bool changeSmmu=false,changeQueue=false,changeGate=false,mapFail=false;
 Fake(){for(unsigned i=0;i<128;i++){smmu[0x800+4*i]=0;smmu[0xc00+4*i]=4;}
  smmu[0x804]=0x800f0100;smmu[0x808]=0x80000740;}
 static bool uread(void *p,bool c,unsigned off,uint32_t *v){
  auto &f=*static_cast<Fake *>(p);auto &m=c?f.ufs:f.gcc;
  assert(m.count(off));f.totalReads++;*v=m.at(off);
  if(c){f.queueReads++;if(f.changeQueue&&f.queueReads>12&&off==0x58)*v=1;}
  else {f.gateReads++;if(f.changeGate&&f.gateReads>6&&off==0x77004)*v|=1;}
  return true;
 }
 static bool sread(void *p,unsigned off,uint32_t *v){
  auto &f=*static_cast<Fake *>(p);assert(f.smmu.count(off));f.totalReads++;
  if(f.mapFail)return false;
  *v=f.smmu.at(off);
  if(off>=0x800&&off<0xa00){f.matchReads++;if(f.changeSmmu&&f.matchReads>76&&off==0x804)*v^=1;}
  if(off==0x44000||off==0x1010||off==0x1810)f.contextReads++;
  return true;
 }
 PolarisUfsInventory probe(bool enabled=true,bool ready=true){return polaris_ufs_inventory({this,uread},{this,sread},enabled,ready);}
};
int main(){
 for(unsigned disabled=0;disabled<2;disabled++){
  Fake f;auto r=f.probe(disabled, !disabled);assert(!f.totalReads);
  assert(r.state==(disabled?PolarisUfsInventory::ControllerNotReady:PolarisUfsInventory::Disabled));
 }
 Fake f;auto r=f.probe();assert(r.state==PolarisUfsInventory::Snapshot);
 assert(r.gccReads==12&&r.controllerReads==24&&r.smmuReads==170);
 assert(r.groups==76&&r.scanned==76&&r.validGroups==2&&r.pages==64&&r.banks==45);
 assert(r.matches==1&&r.match[0].slot==1&&r.match[0].bank==4&&r.match[0].shared==1);
 assert(r.coverage==0xffff&&r.stable&&r.queuesStable&&!r.overlap&&!r.truncated);
 assert(f.contextReads==6&&f.matchReads==152);
 assert(polaris_ufs_sid_coverage(0x80000100)==1);
 assert(polaris_ufs_sid_coverage(0x800f0100)==0xffff);
 assert(!polaris_ufs_sid_coverage(0x80000740)&&!polaris_ufs_sid_coverage(0xf0100));
 Fake off;off.gcc[0x77008]=0;auto o=off.probe();assert(o.state==PolarisUfsInventory::GateNotReady&&!off.queueReads&&!off.matchReads);
 for(unsigned mode=0;mode<5;mode++){
  Fake bad;
  if(mode==0)bad.smmu[0x24]|=0x80000000;
  if(mode==1)bad.smmu[0]|=8;
  if(mode==2)bad.smmu[0x20]&=~(1u<<27);
  if(mode==3)bad.smmu[0x24]&=~255u;
  if(mode==4)bad.smmu[0xc04]=45;
  auto b=bad.probe();assert(b.state==PolarisUfsInventory::Unsupported&&!bad.contextReads);
 }
 Fake ext;ext.smmu[0x20]=(ext.smmu[0x20]&~255u)|172;auto e=ext.probe();
 assert(e.state==PolarisUfsInventory::Snapshot&&e.scanned==128&&e.truncated&&ext.matchReads==256);
 Fake ambiguous;ambiguous.smmu[0x80c]=0x800f0100;
 auto a=ambiguous.probe();assert(a.state==PolarisUfsInventory::Snapshot&&a.overlap&&a.matches==2);
 Fake many;for(unsigned i=0;i<17;i++)many.smmu[0x800+4*i]=0x800f0100;
 assert(many.probe().state==PolarisUfsInventory::TooManyMatches&&!many.contextReads);
 Fake changed;changed.changeSmmu=true;assert(!changed.probe().stable);
 Fake queue;queue.changeQueue=true;assert(!queue.probe().queuesStable);
 Fake gate;gate.changeGate=true;assert(gate.probe().state==PolarisUfsInventory::GateChanged);
 Fake fail;fail.mapFail=true;assert(fail.probe().state==PolarisUfsInventory::ReadFailed);
 Fake none;none.smmu[0x804]=0;auto n=none.probe();assert(n.state==PolarisUfsInventory::Snapshot&&!n.matches&&!n.coverage&&!none.contextReads);
 for(unsigned type=1;type<=2;type++){
  Fake bypass;bypass.smmu[0xc04]=type<<16;auto b=bypass.probe();
  assert(b.state==PolarisUfsInventory::Snapshot&&b.match[0].bank==255&&!bypass.contextReads);
 }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);(d/'inventory.cpp').write_text(source)
            result=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror',
                '-fsanitize=address,undefined','-I',ROOT/'drivers/PolarisUsb',d/'inventory.cpp','-o',d/'inventory'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([d/'inventory'],check=True,capture_output=True)
