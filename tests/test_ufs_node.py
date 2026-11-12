"""Fixed-file mapping and cancelled request lifetime, with real fragmented FAT."""
from pathlib import Path
import shutil,subprocess,tempfile,unittest
import test_ufs_fat
ROOT=Path(__file__).resolve().parents[1]
class UfsNodeTests(unittest.TestCase):
    def test_queue_and_fresh_physical_reads_on_fragmented_file(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);binary=d/'node-test'
            result=subprocess.run([shutil.which('clang++'),'-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                '-I',ROOT/'drivers/PolarisUsb',ROOT/'tests/ufs-node-model.cpp','-o',binary],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([binary],check=True,capture_output=True)
            image,entry,chain,put,offset,size,efi=test_ufs_fat.UfsFatTests.fixture(self,d)
            subprocess.run([binary,image],check=True,capture_output=True)
            with image.open('r+b') as f:
                f.seek(offset(chain[4]));data=f.read(size);f.seek(offset(100000));f.write(data)
            put(chain[3],100000);put(100000,chain[5]);put(chain[4],0)
            subprocess.run([binary,image],check=True,capture_output=True)

    def test_hardware_evidence_rejects_model_stale_crc_wrong_order_and_writes(self):
        import importlib.util,re,struct,zlib
        spec=importlib.util.spec_from_file_location('node_usb',ROOT/'scripts/check-polaris-usb.py')
        usb=importlib.util.module_from_spec(spec);spec.loader.exec_module(usb)
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);image,entry,chain,put,offset,size,efi=test_ufs_fat.UfsFatTests.fixture(self,d)
            with image.open('rb') as f:b=f.read(512)
            data=struct.unpack_from('<H',b,14)[0]+2*struct.unpack_from('<I',b,36)[0]
            requests=[(0,4096),(4096,4096),(8192,1),(1023,2050),(4095,2050),(8192,1),(0,4096),(0,4096),(37,16)]
            # Independent byte-address expansion: coalesce consecutive physical pages per request.
            blocks=[]
            for start,length in requests:
                pages=[(data*512+(chain[off//size]-2)*size+off%size)//4096 for off in range(start,start+length)]
                blocks.extend(page for i,page in enumerate(pages) if i==0 or page!=pages[i-1])
            n=len(blocks);build='a'*40;pa=0x102000000
            rows=[f'POLARIS_UFS_NODE build={build} state=ok mode=hardware ready=1 failed=0 error=0 queue=0 submitted=9 completed=9 cancelled=0 busy=0 errors=0 count={n} writes={3*n} doorbells={n} smmu_unchanged=1 media_writes=0 smmu_writes=0 pa={pa:016x}',
                  'POLARIS_UFS_NODE_VFS opens=2 reads=12 bytes=20502 denied=2 path=/dev/polaris-esp-file size=8193']
            upiu=(b'\x21'+bytes(33)).hex();prdt=struct.pack('<QII',pa+8192,0,4095).hex()
            with image.open('rb') as f:
                for i,block in enumerate(blocks):
                    f.seek(block*4096);crc=zlib.crc32(f.read(4096))
                    rows += [f'POLARIS_UFS_NODE_CMD n={i} lba={393216+block} opcode=28 lun=00 blocks=1 state=ok ocs=0 response=00 status=00 flags=00 bytes=4096 residual=0 segment=0 sense_len=0 guards=1 stopped=1 polls=2 run=00000000 db=00000000 is=00000401 upiu={upiu}',
                             f'POLARIS_UFS_NODE_DMA n={i} data_pa={pa+8192:016x} prdt={prdt} crc32={crc:08x}']
            rows+=['POLARIS_UFS_NODE_FAULT global_before=00000000 bank_before=00000400 global_after=00000000 bank_after=00000400','POLARIS_UFS_NODE_END']
            raw=('\n'.join(rows)+'\n').encode();log=b'POLARIS_UFS_NODE_OK bytes=8193 cross=3 repeat=2 eof=OK readonly=OK reopen=OK'
            prior=dict(source_commit=build,ufs_fat32_file_verified=True,ufs_nop_dma={'pa':pa},ufs_esp_chain=chain,ufs_esp_geometry={'spc':b[13],'data_sector':data})
            result=usb.verify_ufs_node(build,raw,prior,image,log)
            self.assertTrue(result['ufs_node_verified']);self.assertFalse(result['ufs_filesystem_mount_verified'])
            for broken in (raw.replace(b'mode=hardware',b'mode=model'),raw.replace(b'submitted=9',b'submitted=8'),
                           raw.replace(b'media_writes=0',b'media_writes=1'),raw.replace(b'denied=2',b'denied=0'),
                           raw.replace(b'guards=1',b'guards=0'),raw.replace(b'status=00',b'status=02'),
                           raw.replace(b'bank_after=00000400',b'bank_after=00000402'),
                           re.sub(rb'crc32=[0-9a-f]{8}',b'crc32=00000000',raw),raw[:-10]):
                with self.assertRaises(ValueError):usb.verify_ufs_node(build,broken,prior,image,log)
            with self.assertRaises(ValueError):usb.verify_ufs_node(build,raw,prior,image,b'')
            with self.assertRaises(ValueError):usb.verify_ufs_node(build,raw,{**prior,'ufs_fat32_file_verified':False},image,log)

    def test_actual_frontend_worker_timeout_and_error_paths(self):
        text=(ROOT/'drivers/PolarisUsb/PolarisUsb.cpp').read_text()
        actual=text[text.index('static void serviceUfsNode('):text.index('static void runUfsNode(')]
        source=r'''
#include "UfsNode.h"
#include <cassert>
#include <cstdarg>
#include <mutex>
#include <condition_variable>
#include <chrono>
#include <thread>
#include <atomic>
using IOLock=std::mutex;
static void IOLockLock(IOLock*p){p->lock();}
static void IOLockUnlock(IOLock*p){p->unlock();}
static constexpr unsigned THREAD_UNINT=0,kMillisecondScale=1;
static unsigned mode,diagnostics;
volatile uint64_t polaris_ufs_node_progress[4];
static void nodePhase(uint64_t p){__atomic_store_n(&polaris_ufs_node_progress[0],p,__ATOMIC_RELEASE);}
static void console(const char*,...){diagnostics++;}
struct TerminalSession {
 IOLock lock;IOLock*ufsNodeLock=&lock;PolarisUfsNodeQueue ufsNodeQueue;
 bool ufsNodeReady=true,ufsNodeFailed=false;int ufsNodeError=0;
 uint64_t ufsNodeSubmitted=0,ufsNodeCompleted=0,ufsNodeCancelled=0,ufsNodeBusy=0,ufsNodeErrors=0;
 PolarisFatResult ufsEspResult;uint8_t ufsNodeBlock[4096];
 TerminalSession(){auto &f=ufsEspResult;f.state=PolarisFatResult::Success;f.fileBytes=8193;f.sectorsPerCluster=2;f.clusters=130000;f.clusterCount=9;for(unsigned i=0;i<9;i++)f.chain[i]=i+2;}
};
static TerminalSession*gNodeSession;
static bool ufsNodePhysical(TerminalSession &s,uint32_t block){
 assert(s.lock.try_lock());s.lock.unlock();
 if(mode==2)return false;
 for(unsigned i=0;i<4096;i++){uint64_t off=uint64_t(block)*4096+i;s.ufsNodeBlock[i]=uint8_t(off*37+(off>>8)*11+0x51);}
 return true;
}
static void serviceUfsNode(TerminalSession &s);
static void IOSleep(unsigned){assert(!"polling must not be used for an enabled node");}
#ifdef REAL_THREADS
static std::condition_variable workEvent,doneEvent;
static std::atomic<bool> waiting;
static uint64_t mach_absolute_time(){return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count());}
static void clock_interval_to_deadline(unsigned ms,unsigned scale,uint64_t*out){assert(scale==kMillisecondScale);*out=mach_absolute_time()+uint64_t(ms)*1000000;}
static void IOLockWakeup(IOLock*,void*event,bool one){assert(one);(event==&gNodeSession->ufsNodeQueue?workEvent:doneEvent).notify_one();}
static int IOLockSleepDeadline(IOLock*lock,void*event,uint64_t deadline,unsigned inter){
 assert(inter==THREAD_UNINT);std::unique_lock<IOLock> held(*lock,std::adopt_lock);
 bool work=event==&gNodeSession->ufsNodeQueue;
 if(work){waiting.store(true,std::memory_order_release);deadline=mach_absolute_time()+5000000000ULL;}
 auto until=std::chrono::steady_clock::time_point(std::chrono::nanoseconds(deadline));
 bool ok=(work?workEvent:doneEvent).wait_until(held,until,[&]{return gNodeSession->ufsNodeQueue.state==(work?PolarisUfsNodeQueue::Pending:PolarisUfsNodeQueue::Done);});
 held.release();assert(ok);return 0;
}
#else
static uint64_t clockMs;static unsigned waits,workWakes,doneWakes;
static uint64_t mach_absolute_time(){return clockMs;}
static void clock_interval_to_deadline(unsigned ms,unsigned scale,uint64_t*out){assert(scale==kMillisecondScale);*out=clockMs+ms;}
static void IOLockWakeup(IOLock*,void*event,bool one){assert(one);if(event==&gNodeSession->ufsNodeQueue)workWakes++;else {assert(event==&gNodeSession->ufsNodeQueue.error);doneWakes++;}}
static int IOLockSleepDeadline(IOLock*lock,void*event,uint64_t deadline,unsigned inter){
 assert(inter==THREAD_UNINT);waits++;IOLockUnlock(lock);
 if(event==&gNodeSession->ufsNodeQueue){clockMs=deadline;IOLockLock(lock);return 1;}
 assert(event==&gNodeSession->ufsNodeQueue.error);
 if(mode==4)clockMs=deadline;
 if(mode==1||mode==2||mode==4||(mode==5&&waits>=3))serviceUfsNode(*gNodeSession);
 if(mode==3&&gNodeSession->ufsNodeQueue.state==PolarisUfsNodeQueue::Pending)assert(gNodeSession->ufsNodeQueue.claim());
 if(mode==0||mode==3)clockMs=deadline;else clockMs++;
 IOLockLock(lock);return mode==0||mode==3||mode==4?1:0;
}
#endif
''' + actual + r'''
int main(){
 uint8_t out[4096];
#ifdef REAL_THREADS
 for(unsigned round=0;round<100;round++){
  TerminalSession s;gNodeSession=&s;mode=round%2?2:1;waiting.store(false,std::memory_order_relaxed);
  std::thread owner([&]{nodeWorkerPause(s);serviceUfsNode(s);});
  while(!waiting.load(std::memory_order_acquire))std::this_thread::yield();
  int result=nodeCopy(0,4096,out);owner.join();assert(result==(mode==2?5:0));
  assert(s.ufsNodeSubmitted==s.ufsNodeCompleted&&s.ufsNodeCompleted==1&&!s.ufsNodeCancelled);
  if(mode==1)for(unsigned i=0;i<4096;i++)assert(out[i]==uint8_t(i*37+(i>>8)*11+0x51));
 }
#else
 for(mode=0;mode<=5;mode++){
  TerminalSession s;gNodeSession=&s;clockMs=0;diagnostics=waits=workWakes=doneWakes=0;
  int error=nodeCopy(0,4096,out);assert(workWakes==1);
  if(mode==1||mode==4||mode==5){assert(!error&&s.ufsNodeCompleted==1&&!s.ufsNodeCancelled&&doneWakes==1);for(unsigned i=0;i<4096;i++)assert(out[i]==uint8_t(i*37+(i>>8)*11+0x51));}
  if(mode==2){assert(error==5&&s.ufsNodeFailed&&s.ufsNodeErrors==1&&doneWakes==1);assert(nodeCopy(0,1,out)==5);assert(s.ufsNodeSubmitted==1);}
  if(mode==0||mode==3){assert(error==60&&s.ufsNodeCancelled==1&&diagnostics==1);assert(s.ufsNodeCompleted==0&&!doneWakes);}
  if(mode==0)assert(s.ufsNodeQueue.state==PolarisUfsNodeQueue::Idle);
  if(mode==3){assert(s.ufsNodeQueue.state==PolarisUfsNodeQueue::Abandoned);assert(!s.ufsNodeQueue.submit(0,1));s.ufsNodeQueue.finish(0);assert(s.ufsNodeQueue.state==PolarisUfsNodeQueue::Idle);}
 }
 TerminalSession s;gNodeSession=&s;s.ufsNodeSubmitted=64;assert(nodeCopy(0,1,out)==28);assert(nodeCopy(8193,1,out)==22);
 gNodeSession=nullptr;assert(nodeCopy(0,1,out)==6);
 TerminalSession idle;gNodeSession=&idle;clockMs=0;waits=0;nodeWorkerPause(idle);assert(waits==1&&clockMs==2);
 assert(idle.ufsNodeQueue.submit(0,1));nodeWorkerPause(idle);assert(waits==1);
#endif
}
'''
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);(d/'worker.cpp').write_text(source)
            for flags in ([],['-DREAL_THREADS=1']):
                result=subprocess.run([shutil.which('clang++'),'-std=c++17','-pthread','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',*flags,
                    '-I',ROOT/'drivers/PolarisUsb',d/'worker.cpp','-o',d/'worker'],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                subprocess.run([d/'worker'],check=True,capture_output=True,timeout=30)

    def test_actual_storage_failure_yields_without_panicking(self):
        text=(ROOT/'scripts/fixtures/polaris-io-init.c').read_text()
        function=text[text.index('__attribute__((noreturn,noinline)) void io_failure('):text.index('static s64 must(')]
        source=r'''
#include <assert.h>
#include <stdint.h>
#include <setjmp.h>
#include <string.h>
typedef uint64_t u64;typedef int64_t s64;
static u64 io_results[32];
#define STAGE_DIGIT (sizeof("POLARIS_IO_FAILED stage=")-1)
#define ERROR_DIGIT (sizeof("POLARIS_IO_FAILED stage=00 errno=")-1)
static char failure[]="POLARIS_IO_FAILED stage=00 errno=000";
static jmp_buf escape;static unsigned writes,sleeps,panics;
static long fake_call(long n,long a,long b,long c,long d){
 (void)d;
 if(n==4){assert(a==1&&c>0);assert(((const char*)b)[c-1]=='0'||c==1);writes++;}
 else {assert(n==55&&a==0xc00);panics++;}
 return 0;
}
#define CALL(n,a,b,c,d) fake_call(n,(long)(a),(long)(b),(long)(c),(long)(d))
static long sc(long n,long a,long b,long c,long d,long timeout,long f){
 assert(n==93&&!a&&!b&&!c&&!d&&!f&&*(s64*)timeout==10);if(++sleeps==3)longjmp(escape,1);return 0;
}
__attribute__((noreturn)) static void reboot_return(void){longjmp(escape,1);}
#define POLARIS_UFS_NODE_TEST 1
''' + function + r'''
int main(void){
 if(!setjmp(escape))io_failure(81,60);
 assert(io_results[0]==81&&io_results[1]==60&&!panics&&sleeps==3&&writes==6);
 assert(!strcmp(failure,"POLARIS_IO_FAILED stage=81 errno=060"));
 if(!setjmp(escape))io_failure(12,5);
 assert(panics==1&&sleeps==3&&writes==6);
}
'''
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);(d/'failure.c').write_text(source)
            result=subprocess.run([shutil.which('clang'),'-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',d/'failure.c','-o',d/'failure'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            subprocess.run([d/'failure'],check=True,capture_output=True)
