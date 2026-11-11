"""Exercise the actual bounded console buffers, CDC descriptors and evidence grader."""
import importlib.util
from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class UsbTests(unittest.TestCase):
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
