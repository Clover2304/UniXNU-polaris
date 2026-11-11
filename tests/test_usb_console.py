"""Exercise the actual bounded console buffers, CDC descriptors and evidence grader."""
import importlib.util
from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class UsbTests(unittest.TestCase):
    def test_ep0_bus_reset_preserves_setup_and_stall_restart_avoids_clearstall(self):
        text=(ROOT/'drivers/PolarisUsb/UsbTransfers.h').read_text()
        functions=text[text.index('static void ep0Restart('):text.index('static void ep0StartData(')]
        source=r'''
#include "Dwc3Regs.h"
#include <cstdint>
#include <cassert>
#include <vector>
struct UsbHw {
 int ep0Stage=0;bool ep0Zlp=true,ep0Three=true,ep0In=true;unsigned ep0StatusDep=0,armed=0;
 std::vector<unsigned> commands;
};
static int epCmd(UsbHw &hw,unsigned dep,uint32_t cmd,uint32_t,uint32_t,uint32_t) {
 assert(dep==0&&cmd!=DWC3_DEPCMD_CLEARSTALL);hw.commands.push_back(cmd);return 0;
}
static void ep0ArmSetup(UsbHw &hw){hw.ep0Stage=0;hw.armed++;}
''' + functions + r'''
int main(){
 for(int stage=0;stage<=3;stage++) {
  UsbHw hw;hw.ep0Stage=stage;ep0Reset(hw);
  assert(hw.ep0Stage==0&&!hw.ep0Zlp&&!hw.ep0Three&&!hw.ep0In&&hw.ep0StatusDep==1);
  assert(hw.armed==(stage?1u:0u));assert(hw.commands.size()==(stage?1u:0u));
  if(stage)assert(hw.commands[0]==DWC3_DEPCMD_SETSTALL);
  auto armed=hw.armed;ep0Reset(hw);assert(hw.armed==armed);
 }
 UsbHw hw;ep0Restart(hw);assert(hw.armed==1&&hw.commands.size()==1&&hw.commands[0]==DWC3_DEPCMD_SETSTALL);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);(d/'reset.cpp').write_text(source)
            result=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',ROOT/'drivers/PolarisUsb',d/'reset.cpp','-o',d/'reset'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([d/'reset'],check=True,capture_output=True)

    def test_disconnected_session_discards_partial_input_and_pending_reply(self):
        text=(ROOT/'drivers/PolarisUsb/UsbEvents.h').read_text()
        function=text[text.index('static void discardUsbSession('):text.index('static void handleDepEvent(')]
        source=r'''
#include "UsbBuffers.h"
#include <cassert>
#include <cstring>
struct TerminalSession {
 PolarisLine input;PolarisBytes<128> reply;size_t pending=0;
 struct {unsigned configured=1,lineState=3;bool hostReady=true,needReplay=false;} hw;
};
''' + function + r'''
int main(){
 TerminalSession s;char out[256];
 for(unsigned i=0;i<64;i++) {
  s.input.feed('X',out);s.input.overflow=true;
  assert(s.reply.append("old reply",9));s.pending=9;
  s.hw.configured=1;s.hw.lineState=3;s.hw.hostReady=true;s.hw.needReplay=false;
  discardUsbSession(s);
  assert(!s.pending&&!s.reply.available()&&!s.hw.configured&&!s.hw.hostReady&&!s.hw.lineState&&s.hw.needReplay);
  assert(s.input.feed('p',out)==PolarisLine::More);s.input.feed('i',out);s.input.feed('n',out);s.input.feed('g',out);
  assert(s.input.feed('\n',out)==PolarisLine::Ready&&!strcmp(out,"ping"));
  char full[128]{};assert(s.reply.append(full,sizeof(full)));discardUsbSession(s);
 }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);(d/'session.cpp').write_text(source)
            result=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',ROOT/'drivers/PolarisUsb',d/'session.cpp','-o',d/'session'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([d/'session'],check=True,capture_output=True)

    def test_actual_endpoint_command_timeout_and_status_preserve_diagnostics(self):
        text=(ROOT/'drivers/PolarisUsb/UsbTransfers.h').read_text()
        command=text[text.index('static int epCmd('):text.index('static void endDepTransfer(')]
        source=r'''
#include "Dwc3Regs.h"
#include <cstdint>
#include <cassert>
#include <map>
volatile uint64_t polaris_usb_stats[16];
static unsigned delays;
static void IODelay(unsigned amount){assert(amount==1);assert(++delays<=20000);}
static void console(const char *,...){}
struct UsbHw {
 std::map<unsigned,uint32_t> regs;
 unsigned polls=0,completeAfter=3,status=0,lastCmdDep=0,lastCmdWord=0,lastCmdRaw=0;
 int lastCmdSt=0;
 unsigned errorCmdDep=0,errorCmdWord=0,errorCmdRaw=0;int errorCmdStatus=0;
 const char *commandContext="test",*errorContext="none";
 void wr(unsigned off,uint32_t value){regs[off]=value;}
 uint32_t rd(unsigned off){
  if(off==DWC3_DEPCMD(1)&&completeAfter&&++polls>=completeAfter)
   regs[off]=(regs[off]&~DWC3_DEPCMD_CMDACT)|(status<<12)|(1u<<16);
  return regs[off];
 }
};
''' + command + r'''
int main(){
 for(unsigned mode=0;mode<3;mode++) {
  UsbHw hw;hw.regs[DWC3_GUSB2PHYCFG(0)]=0x140;
  if(mode==1)hw.status=9;if(mode==2)hw.completeAfter=0;
  delays=0;uint32_t raw=0;
  int result=epCmd(hw,1,DWC3_DEPCMD_STARTTRANSFER,1,0x23aa000,0,&raw);
  assert(result==(mode==0?0:mode==1?9:-1));assert(hw.lastCmdSt==result);
  assert(hw.lastCmdDep==1&&hw.lastCmdWord==DWC3_DEPCMD_STARTTRANSFER);
  if(mode)assert(hw.errorCmdDep==1&&hw.errorCmdWord==DWC3_DEPCMD_STARTTRANSFER&&hw.errorCmdRaw==hw.lastCmdRaw&&hw.errorCmdStatus==result&&hw.errorContext==hw.commandContext);
  assert(hw.regs[DWC3_GUSB2PHYCFG(0)]==0x140);
  if(mode==2)assert(delays==20000&&(hw.lastCmdRaw&DWC3_DEPCMD_CMDACT));
  else assert(delays==2&&raw==hw.lastCmdRaw&&!(raw&DWC3_DEPCMD_CMDACT));
 }
 assert(polaris_usb_stats[3]==2);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);(d/'command.cpp').write_text(source)
            result=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',ROOT/'drivers/PolarisUsb',d/'command.cpp','-o',d/'command'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([d/'command'],check=True,capture_output=True)

    def test_actual_ep0_start_stops_at_first_failed_command(self):
        text=(ROOT/'drivers/PolarisUsb/UsbTransfers.h').read_text()
        program=text[text.index('static bool programEp0('):text.index('static void enableDataEndpoints(')]
        source=r'''
#include "Dwc3Regs.h"
#include <cstdint>
#include <cassert>
struct UsbHw {
 unsigned calls=0,failAt=0,writes=0;
 int lastCmdSt=0;
 void wr(unsigned offset,uint32_t value){assert(offset==DWC3_DALEPENA&&value==3);++writes;}
};
static int epCmd(UsbHw &hw,unsigned,uint32_t,uint32_t,uint32_t,uint32_t) {
 ++hw.calls;hw.lastCmdSt=hw.calls==hw.failAt?9:0;return hw.lastCmdSt;
}
static void ep0ArmSetup(UsbHw &hw) {(void)epCmd(hw,0,DWC3_DEPCMD_STARTTRANSFER,0,0,0);}
''' + program + r'''
int main(){
 for(unsigned fail=1;fail<=6;fail++) {
  UsbHw hw;hw.failAt=fail;assert(!programEp0(hw));
  assert(hw.calls==fail&&hw.lastCmdSt==9);
  assert(hw.writes==(fail==6?1u:0u));
 }
 UsbHw hw;assert(programEp0(hw));assert(hw.calls==6&&hw.writes==1&&hw.lastCmdSt==0);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory);(d/'ep0.cpp').write_text(source)
            result=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',ROOT/'drivers/PolarisUsb',d/'ep0.cpp','-o',d/'ep0'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([d/'ep0'],check=True,capture_output=True)

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
 // CBA2R.VA64=0 cannot yield an unchanged successful shared-bank result.
 h=new Hardware();h->regs[0xc08/4]=4;h->regs[0x84000/4]=0xe0;h->regs[0x1010/4]=0x1f000;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);assert(!r.error&&r.bank==79&&r.remapped&&h->regs[0x1810/4]==0);delete h;
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
 // Captured SDM845 USB log: 64 global pages, 45 banks, 76 groups.
 h=new Hardware();h->regs[0x20/4]=0x4c017e4c;h->regs[0x24/4]=0x5000002d;h->regs[0x28/4]=0x5555;
 h->regs[0xc08/4]=4;h->regs[0x44000/4]=0xe0;h->regs[0x1010/4]=0x1f000;h->regs[0x1810/4]=1;
 r=polaris_usb_smmu(h->io(),0x1023aa000ULL,65536);
 assert(!r.error&&r.pages==64&&r.bank==4&&r.shared==1&&!r.remapped&&h->writes.empty());delete h;
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
 assert(polaris_usb_fifos(0x4020400a,0x0162493b,0x07ea0020,0x03080a65,0x40,fifos));
 assert(fifos[0]==0x40&&fifos[1]==0x00400100&&fifos[2]==0x01400010);
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
