"""Exercise the actual read-only gate/probe and reject misleading USB evidence."""
import importlib.util
from pathlib import Path
import shutil, subprocess, tempfile, unittest

ROOT=Path(__file__).resolve().parents[1]

class UfsTests(unittest.TestCase):
    def test_gates_whitelist_mapping_failure_and_snapshot(self):
        source=r'''
#include "UfsProbe.h"
#include <cassert>
#include <map>
#include <vector>
struct Fake {
 std::map<unsigned,uint32_t> gcc{{0x77000,0},{0x77004,0x80000000},{0x77008,0x10000},
   {0x7700c,3},{0x77010,3},{0x82024,3}};
 std::map<unsigned,uint32_t> ufs{{0,0x0100001f},{8,0x210},{0x30,0xf},{0x34,1},{0x58,0},{0x78,0}};
 unsigned gccReads=0,controllerReads=0;bool mapFail=false,change=false;
 std::vector<unsigned> offsets;
 static bool read(void *ctx,bool controller,unsigned offset,uint32_t *v) {
  auto &f=*static_cast<Fake *>(ctx);auto &m=controller?f.ufs:f.gcc;
  assert(m.count(offset)); // No reset, queue-base, interrupt or UIC accesses.
  if(controller){f.controllerReads++;f.offsets.push_back(offset);if(f.mapFail)return false;}
  else f.gccReads++;
  *v=m.at(offset);
  if(!controller&&f.change&&f.controllerReads&&offset==0x77004)*v|=1;
  return true;
 }
 PolarisUfsSnapshot probe(bool enabled=true){return polaris_ufs_probe({this,read},enabled);}
};
int main(){
 Fake disabled;auto d=disabled.probe(false);assert(d.state==PolarisUfsSnapshot::Disabled);
 assert(!disabled.gccReads&&!disabled.controllerReads);
 for(unsigned mode=0;mode<10;mode++) {
  Fake f;
  if(mode==0)f.gcc[0x77004]|=1;
  if(mode==1)f.gcc[0x77008]=0; // PWR_ON bit alone must not grant access.
  if(mode==2)f.gcc[0x77008]=0xffffffff;
  if(mode==3)f.gcc[0x77000]=1;
  if(mode>=4&&mode<=6)f.gcc[mode==4?0x7700c:mode==5?0x77010:0x82024]=0;
  if(mode>=7)f.gcc[mode==7?0x7700c:mode==8?0x77010:0x82024]=0x80000001;
  auto s=f.probe();assert(s.state!=PolarisUfsSnapshot::Ready);
  assert(!f.controllerReads&&s.controllerReads==0&&s.gccReads==6);
 }
 Fake f;auto s=f.probe();assert(s.state==PolarisUfsSnapshot::Ready&&s.gccReads==12&&s.controllerReads==6);
 assert((f.offsets==std::vector<unsigned>{0,8,0x30,0x34,0x58,0x78}));
 Fake fail;fail.mapFail=true;assert(fail.probe().state==PolarisUfsSnapshot::ControllerReadFailed);
 Fake change;change.change=true;assert(change.probe().state==PolarisUfsSnapshot::GateChanged);
 for(unsigned mode=0;mode<5;mode++) {
  Fake bad;if(mode<2)bad.ufs[0]=mode?0xffffffff:0;
  if(mode==2)bad.ufs[8]=0xdeadbeef;
  if(mode==3)bad.ufs[0x30]=0xffffffff;
  if(mode==4)bad.ufs[0x34]=0xffffffff;
  assert(bad.probe().state==PolarisUfsSnapshot::Invalid);
 }
 Fake busy;busy.ufs[0x34]=3;busy.ufs[0x58]=1;busy.ufs[0x78]=1;
 assert(busy.probe().state==PolarisUfsSnapshot::Ready); // Report, never clear inherited activity.
}
'''
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);(d/'probe.cpp').write_text(source)
            result=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror',
                '-fsanitize=address,undefined','-I',ROOT/'drivers/PolarisUsb',d/'probe.cpp','-o',d/'probe'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([d/'probe'],check=True,capture_output=True)

    def test_usb_evidence_requires_exact_build_and_consistent_read_counts(self):
        spec=importlib.util.spec_from_file_location('usb',ROOT/'scripts/check-polaris-usb.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        build='a'*40
        raw=(f'POLARIS_UFS_SNAPSHOT build={build} state=snapshot gcc_reads=12 controller_reads=6 writes=0 media_reads=0\n'
             'POLARIS_UFS_GATE bcr=00000000 gdsc=80000000 cfg=00010000 axi=00000003 ahb=00000003 aggre=00000003\n'
             'POLARIS_UFS_REG cap=0100001f ver=00000210 hcs=0000000f hce=00000001 utrl=00000000 utmrl=00000000\nPOLARIS_UFS_END\n').encode()
        good=m.verify_ufs(build,raw)
        self.assertTrue(good['ufs_controller_registers_verified'])
        self.assertFalse(good['ufs_native_media_read_verified'])
        for broken in [raw.replace(build.encode(),b'b'*40),raw.replace(b'writes=0',b'writes=1'),
                       raw.replace(b'controller_reads=6',b'controller_reads=0'),
                       raw.replace(b'ver=00000210',b'ver=ffffffff'),raw.replace(b'cfg=00010000',b'cfg=00000000'),
                       raw.replace(b'state=snapshot',b'state=disabled'),raw[:-16]]:
            with self.assertRaises(ValueError):m.verify_ufs(build,broken)
        skipped=raw.replace(b'state=snapshot gcc_reads=12 controller_reads=6',b'state=power-not-ready gcc_reads=6 controller_reads=0')
        skipped=skipped.replace(b'cfg=00010000',b'cfg=00000000')
        skipped=skipped[:skipped.index(b'POLARIS_UFS_REG')]+b'POLARIS_UFS_REG cap=00000000 ver=00000000 hcs=00000000 hce=00000000 utrl=00000000 utmrl=00000000\nPOLARIS_UFS_END\n'
        self.assertFalse(m.verify_ufs(build,skipped)['ufs_controller_registers_verified'])
