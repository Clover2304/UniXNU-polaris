"""Exercise the actual bounded console buffers, CDC descriptors and evidence grader."""
import importlib.util
from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class UsbTests(unittest.TestCase):
    def test_firmware_usb_context_bypass_preserves_other_devices_and_s2cr(self):
        source=r'''
#include "UsbSmmu.h"
#include <cassert>
#include <vector>
#include <utility>
#include <cstring>
struct Hardware {
 uint32_t regs[0x100000/4]{};
 std::vector<std::pair<unsigned,uint32_t>> writes;
 bool rejectCbar=false,rejectRemap=false,corruptRemap=false,failRestore=false;
 Hardware(){regs[0x20/4]=(1u<<30)|128;regs[0x24/4]=(6u<<28)|80;regs[0x28/4]=0x1222;
  regs[(0x800+7*4)/4]=0x80000740;regs[(0xc00+7*4)/4]=4;
  regs[0x84000/4]=0x79;regs[(0x1000+16)/4]=0;regs[(0x1800+16)/4]=0;
  regs[(0x800+2*4)/4]=0x80000060;regs[(0xc00+2*4)/4]=5;regs[0x85000/4]=0x7d;
 }
 static uint32_t read(void *p,unsigned off){assert(!(off%4)&&off<0x100000);
  assert(!(off>=0xa00&&off<0xc00));assert(!(off>=0xe00&&off<0x1000));
  return ((Hardware*)p)->regs[off/4];}
 static void write(void *p,unsigned off,uint32_t value){auto &h=*(Hardware*)p;h.writes.emplace_back(off,value);
  // Model the documented QHEE quirk: direct BYPASS would become FAULT.
  if(off>=0xc00&&off<0xe00&&(value&0x30000)==0x10000)value=(value&~0x30000)|0x20000;
  if(h.rejectCbar&&off==0x1010)return;
  if(h.failRestore&&off==0xc1c){if(value!=4)h.regs[off/4]=value|0x08000000u;return;}
  if(h.rejectRemap&&off==0xc1c&&value!=4){if(h.corruptRemap)h.regs[off/4]=3;return;}
  h.regs[off/4]=value;
 }
 PolarisSmmuIo io(){return {this,read,write};}
};
int main(){
 auto *h=new Hardware();auto r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);
 assert(!r.error&&r.bank==4&&r.before==0x79&&r.after==0&&r.cbar==0x10000&&r.cba2r==1);
 assert(h->regs[(0xc00+7*4)/4]==4&&h->regs[(0x800+7*4)/4]==0x80000740);
 assert(h->regs[0x85000/4]==0x7d&&h->writes.size()==3);
 for(auto w:h->writes)assert(w.first==0x84000||w.first==0x1010||w.first==0x1810);
 h->writes.clear();r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);assert(!r.error&&h->writes.empty());delete h;
 h=new Hardware();h->regs[(0xc00+2*4)/4]=4;
 h->regs[0x84000/4]=0xe0;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);
 assert(!r.error&&r.bank==79&&r.originalBank==4&&r.remapped&&r.shared==1);
 assert(h->regs[0x84000/4]==0xe0&&h->regs[0xc08/4]==4&&h->regs[0xc1c/4]==79);
 assert(h->regs[0xcf000/4]==0&&h->regs[0x113c/4]==0x10000&&h->regs[0x193c/4]==1);
 assert(h->writes.size()==4);for(auto w:h->writes)assert(w.first==0xcf000||w.first==0x113c||w.first==0x193c||w.first==0xc1c);
 delete h;
 // Reuse an existing shared bypass without clearing fault/interrupt flags.
 h=new Hardware();h->regs[0xc08/4]=4;h->regs[0x84000/4]=0xe0;h->regs[0x1010/4]=0x1f300;h->regs[0x1810/4]=1;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);assert(!r.error&&r.bank==4&&!r.remapped&&h->writes.empty()&&r.after==0xe0);delete h;
 // An occupied or enabled final bank is never repurposed.
 h=new Hardware();h->regs[0xc08/4]=4;h->regs[0x80c/4]=0x80000123;h->regs[0xc0c/4]=79;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);assert(r.error==7&&h->writes.empty());delete h;
 h=new Hardware();h->regs[0xc08/4]=4;h->regs[0xcf000/4]=1;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);assert(r.error==7&&h->writes.empty());delete h;
 // USB mapping refusal restores the candidate bank, preserving shared bank 4.
 for(bool corrupt:{false,true}) {
  h=new Hardware();h->regs[0xc08/4]=4;h->rejectRemap=true;h->corruptRemap=corrupt;
  h->regs[0xcf000/4]=0xe0;h->regs[0x113c/4]=0x123;h->regs[0x193c/4]=0x40000;
  r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);
  assert(r.error==11&&h->regs[0xc1c/4]==4&&h->regs[0x84000/4]==0x79&&h->regs[0xc08/4]==4);
  assert(h->regs[0xcf000/4]==0xe0&&h->regs[0x113c/4]==0x123&&h->regs[0x193c/4]==0x40000);delete h;
 }
 // Failed restoration must leave the candidate bypass usable, never clobber peers.
 h=new Hardware();h->regs[0xc08/4]=4;h->failRestore=true;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);assert(r.error==12);
 assert((h->regs[0xc1c/4]&255)==79&&h->regs[0x113c/4]==0x10000&&h->regs[0x193c/4]==1);
 assert(h->regs[0x84000/4]==0x79&&h->regs[0xc08/4]==4);delete h;
 // Reuse a final bypass shared by another device, without writes to that bank.
 h=new Hardware();h->regs[0xc08/4]=4;h->regs[0xc1c/4]=0x08000004;
 h->regs[0x80c/4]=0x80000123;h->regs[0xc0c/4]=79;
 h->regs[0xcf000/4]=0xe0;h->regs[0x113c/4]=0x10000;h->regs[0x193c/4]=1;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);assert(!r.error&&r.remapped&&h->writes.size()==1);
 assert(h->regs[0xc1c/4]==0x0800004f&&h->regs[0xcf000/4]==0xe0&&h->regs[0xc08/4]==4);delete h;
 h=new Hardware();h->regs[(0x800+7*4)/4]=0x80010740;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);assert(r.error==3&&h->writes.empty());delete h;
 h=new Hardware();h->regs[0x28/4]=0x1000;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);assert(r.error==2&&h->writes.empty());delete h;
 h=new Hardware();h->rejectCbar=true;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);assert(r.error==9&&h->regs[0x84000/4]==0x79&&h->regs[0x1810/4]==0);delete h;
 h=new Hardware();h->regs[0x20/4]=(1u<<30)|168;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);assert(!r.error);delete h;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);(d/'smmu.cpp').write_text(source)
            result=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',ROOT/'drivers/PolarisUsb',d/'smmu.cpp','-o',d/'smmu'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([d/'smmu'],check=True,capture_output=True)

    def test_real_buffers_randomized_streams_command_limits_and_cdc_layout(self):
        source=r'''
#include "UsbBuffers.h"
#include "UsbWire.h"
#include "UsbFifo.h"
#include <deque>
#include <random>
#include <string>
#include <cstring>
#include <cassert>
int main(){
 PolarisBytes<257> ring;std::deque<uint8_t> model;std::mt19937 random(845);
 uint64_t dropped=0;
 for(unsigned i=0;i<100000;i++) {
  if(random()%3) {auto c=(uint8_t)random();ring.capture(c);if(model.size()==257){model.pop_front();dropped++;}model.push_back(c);}
  else {uint8_t data[200];size_t n=ring.peek(data,random()%201);assert(n<=model.size());for(size_t j=0;j<n;j++){assert(data[j]==model.front());model.pop_front();}ring.consume(n);}
  assert(ring.available()==model.size()&&ring.dropped==dropped);
 }
 uint8_t before[257],after[257],huge[258]={};size_t count=ring.peek(before,257);
 assert(!ring.append(huge,258));assert(ring.peek(after,257)==count&&!memcmp(before,after,count));
 PolarisLine line;char out[256];
 for(char c:std::string("status"))assert(line.feed(c,out)==PolarisLine::More);
 for(unsigned i=0;i<300;i++)assert(line.feed('X',out)==PolarisLine::More);
 assert(line.feed('\n',out)==PolarisLine::TooLong);assert(line.feed('\r',out)==PolarisLine::More);
 for(char c:std::string("ping"))assert(line.feed(c,out)==PolarisLine::More);
 assert(line.feed('\n',out)==PolarisLine::Ready&&!strcmp(out,"ping"));
 assert(line.feed('\0',out)==PolarisLine::More);assert(line.feed('\n',out)==PolarisLine::TooLong);
 assert(sizeof(kUsbCdcDeviceDesc)==18&&kUsbCdcDeviceDesc[7]==64&&kUsbCdcDeviceDesc[17]==1);
 for(uint16_t mps:{64,512}) {
  uint8_t config[80];memset(config,0xa5,80);assert(usb_cdc_build_config_desc(config,mps)==67);
  assert(config[2]==67&&config[3]==0&&config[4]==2);
  unsigned endpoints=0,interfaces=0;
  for(unsigned at=0;at<67;){unsigned len=config[at];assert(len>=2&&at+len<=67);
   if(config[at+1]==4){interfaces++;assert(config[at+5]==(interfaces==1?2:10));}
   if(config[at+1]==5){endpoints++;if(config[at+3]==2)assert((config[at+4]|config[at+5]<<8)==mps);}
   at+=len;
  }
  assert(endpoints==3&&interfaces==2);for(unsigned i=67;i<80;i++)assert(config[i]==0xa5);
 }
 uint32_t fifos[3];
 assert(polaris_usb_fifos(64<<8,0,0,512,0,fifos));
 assert((fifos[0]>>16)==0&&(fifos[1]>>16)==64&&(fifos[2]>>16)==320);
 assert(polaris_usb_fifos(64<<8,1u<<23,600u<<16,0,128u<<16,fifos));
 assert((fifos[0]>>16)==128&&(fifos[1]>>16)==192&&(fifos[2]>>16)==448);
 assert(!polaris_usb_fifos(64<<8,0,0,335,0,fifos));
 assert(!polaris_usb_fifos(64<<8,1u<<23,400u<<16,0,128u<<16,fifos));
 assert(!polaris_usb_fifos(0,0,0,65535,0,fifos));
}
'''
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);(d/'usb.cpp').write_text(source)
            result=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',ROOT/'drivers/PolarisUsb',d/'usb.cpp','-o',d/'usb'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([d/'usb'],check=True,capture_output=True)

    def test_usb_evidence_requires_nonce_build_identity_and_complete_runtime_log(self):
        spec=importlib.util.spec_from_file_location('usb_check',ROOT/'scripts/check-polaris-usb.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        build='a'*40;nonce='1234567890';pong=b'POLARIS_PONG\n';echo=b'POLARIS_ECHO 1234567890\n'
        status=('POLARIS_USB_STATUS build='+build+' stage=6 core=5533310a configured=1 cmd_errors=0\n').encode()
        log=('POLARIS_LOG_BEGIN build='+build+' capacity=65536 scope=post-console-open end=100 dropped=0\n'
             'POLARIS_STRESS_OK total=1000 reaped=1000 maxlive=8\n'
             'POLARIS_STRESS_CLEAN nochild=OK fd=OK tmp=OK cow=OK\n'
             '\nPOLARIS_LOG_END end=100 dropped=0\n').encode()
        self.assertEqual(m.verify_response(build,nonce,pong,echo,status,log)['dropped'],0)
        cases=[(build,'wrong',pong,echo,status,log),(build,nonce,pong,echo,status.replace(b'a'*40,b'b'*40),log),
               (build,nonce,pong,echo,status,log[:-10]),(build,nonce,pong,echo,status,log.replace(b'STRESS_OK',b'STRESS_FAIL')),
               (build,nonce,pong,echo,status,log.replace(b'LOG_END end=100',b'LOG_END end=99'))]
        for args in cases:
            with self.assertRaises(ValueError):m.verify_response(*args)
