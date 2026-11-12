/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Own static PID 1: bounded RAM-file I/O, standard descriptors and timed liveness.
 * No libc, dyld, Apple program, MMIO or plain reboot request.
 */
#include "polaris-runtime.h"
volatile u64 results[4], user_page_size, old_length = 8;
volatile u64 io_results[32], io_ticks;
static u8 buffer[128];
static const char seed[] = "POLARIS RAM IO SEED\n";
static const char roundtrip[] = "RAM file roundtrip from PID 1\n";
static const char stdin_text[] = "pipe-backed stdin from PID 1\n";
#ifdef POLARIS_PROCESS_INIT
#define LIVE_PREFIX "\rPOLARIS_PROC_LIVE tick="
#else
#define LIVE_PREFIX "\rPOLARIS_IO_LIVE tick="
#endif
static char pulse[] = LIVE_PREFIX "0 file=RW stdio=OK ";
#define PULSE_DIGIT (sizeof(LIVE_PREFIX)-1)
#define STAGE_DIGIT (sizeof("POLARIS_IO_FAILED stage=")-1)
#define ERROR_DIGIT (sizeof("POLARIS_IO_FAILED stage=00 errno=")-1)
static char failure[] = "POLARIS_IO_FAILED stage=00 errno=000";

__attribute__((noinline)) void checkpoint(void) { __asm__ volatile("":::"memory"); }
__attribute__((noinline)) void io_checkpoint(void) { __asm__ volatile("":::"memory"); }
__attribute__((noinline)) void resident_checkpoint(void) { __asm__ volatile("":::"memory"); }
__attribute__((noreturn,noinline)) void reboot_return(void) { for(;;)__asm__ volatile("b ."); }
__attribute__((noreturn,noinline)) void io_failure(u64 stage,u64 error)
{
 io_results[0]=stage;io_results[1]=error;
 failure[STAGE_DIGIT]=(char)('0'+(stage/10)%10);failure[STAGE_DIGIT+1]=(char)('0'+stage%10);
 failure[ERROR_DIGIT]=(char)('0'+(error/100)%10);failure[ERROR_DIGIT+1]=(char)('0'+(error/10)%10);failure[ERROR_DIGIT+2]=(char)('0'+error%10);
 (void)CALL(55,0xc00,failure,0,0);
 reboot_return();
}
static s64 must(struct reply r,u64 stage) { if(ERROR(r))io_failure(stage,(u64)r.value);return r.value; }
static void exact(struct reply r,s64 bytes,u64 stage) { if(ERROR(r)||r.value!=bytes)io_failure(stage,ERROR(r)?(u64)r.value:999); }
static s64 open_file(const char *name,s64 flags,u64 stage) { return must(CALL(5,name,flags,0,0),stage); }
static void close_file(s64 fd,u64 stage) { exact(CALL(6,fd,0,0,0),0,stage);io_results[16]++; }
static void expect_error(struct reply r,s64 error,u64 stage) { if(!ERROR(r)||r.value!=error)io_failure(stage,ERROR(r)?(u64)r.value:998); }

#ifdef POLARIS_PROCESS_INIT
#include "polaris-process-parent.h"
#endif

#ifdef POLARIS_UFS_NODE_TEST
#include "polaris-ufs-node.h"
#endif

/* The raw Darwin process stack carries argc/argv, not a C call frame.
 * Align it before any compiler-generated stack access (real A75 checks SP).
 * Preserve its original address in x0 for diagnostics; this init takes no argv.
 */
POLARIS_ENTRY(polaris_io_start)

__attribute__((noreturn,used)) void polaris_io_start(u64 initial_sp)
{
 u64 aligned_sp;
 __asm__ volatile("mov %0, sp":"=r"(aligned_sp));
 io_results[21]=initial_sp;io_results[22]=aligned_sp;
 struct reply r=CALL(20,0,0,0,0);results[0]=(u64)r.value;results[2]=r.flags;
 int mib[2]={6,7};old_length=8;user_page_size=0;
 r=sc(202,(s64)mib,2,(s64)&user_page_size,(s64)&old_length,0,0);
 results[1]=(u64)r.value;results[3]=r.flags;
 checkpoint();
 if(results[0]!=1||results[1]!=0||((results[2]|results[3])&(1UL<<29))||user_page_size!=4096||old_length!=4)io_failure(1,997);
 s64 console=open_file("/dev/console",2,2);io_results[2]=(u64)console;
 exact(CALL(90,console,1,0,0),1,3);exact(CALL(90,console,2,0,0),2,4);
 if(console>2)close_file(console,5);
 static const char hello[]="\033[2J\033[HPOLARIS_IO_START pid=1 pagesize=4096\n";
 exact(CALL(4,1,hello,sizeof(hello)-1,0),sizeof(hello)-1,6);io_results[3]=sizeof(hello)-1;
 static const char stderr_text[]="stderr: ready\n";
 exact(CALL(4,2,stderr_text,sizeof(stderr_text)-1,0),sizeof(stderr_text)-1,7);io_results[4]=sizeof(stderr_text)-1;
 r=CALL(42,0,0,0,0);s64 input=must(r,8),output=r.second;
 io_results[5]=(u64)input;io_results[6]=(u64)output;
 exact(CALL(4,output,stdin_text,sizeof(stdin_text)-1,0),sizeof(stdin_text)-1,9);
 exact(CALL(90,input,0,0,0),0,10);
 if(input!=0)close_file(input,11);close_file(output,12);
 exact(CALL(3,0,buffer,sizeof(stdin_text)-1,0),sizeof(stdin_text)-1,13);
 if(!same(buffer,stdin_text,sizeof(stdin_text)-1))io_failure(14,996);io_results[7]=sizeof(stdin_text)-1;
 exact(CALL(3,0,buffer,1,0),0,15);io_results[20]=1;
 s64 fd=open_file("/probe.bin",2,16);io_results[8]=(u64)fd;
 exact(CALL(153,fd,buffer,sizeof(seed)-1,0),sizeof(seed)-1,17);
 if(!same(buffer,seed,sizeof(seed)-1))io_failure(18,995);io_results[9]=sizeof(seed)-1;
 exact(CALL(154,fd,roundtrip,sizeof(roundtrip)-1,128),sizeof(roundtrip)-1,19);io_results[10]=sizeof(roundtrip)-1;
 exact(CALL(153,fd,buffer,sizeof(roundtrip)-1,128),sizeof(roundtrip)-1,20);
 if(!same(buffer,roundtrip,sizeof(roundtrip)-1))io_failure(21,994);io_results[11]=sizeof(roundtrip)-1;
 expect_error(CALL(154,fd,roundtrip,1,4096),28,22);io_results[12]=28;
 exact(CALL(153,fd,buffer,1,4096),0,23);io_results[14]=1;
 close_file(fd,24);fd=open_file("/probe.bin",2,25);
 exact(CALL(153,fd,buffer,sizeof(roundtrip)-1,128),sizeof(roundtrip)-1,26);
 if(!same(buffer,roundtrip,sizeof(roundtrip)-1))io_failure(27,993);io_results[15]=1;
 s64 self=open_file("/sbin/launchd",0,28);
 exact(CALL(153,self,buffer,4,0),4,29);
 static const u8 magic[4]={0xcf,0xfa,0xed,0xfe};if(!same(buffer,magic,4))io_failure(30,992);
 close_file(self,31);
 r=CALL(5,"/sbin/launchd",1,0,0);
 if(!ERROR(r)||(r.value!=30&&r.value!=26))io_failure(32,ERROR(r)?(u64)r.value:991);io_results[13]=(u64)r.value;
 io_results[0]=100;io_checkpoint();
#ifdef POLARIS_PROCESS_INIT
 process_run(fd);
#endif
#ifdef POLARIS_UFS_NODE_TEST
 node_run();
#endif
 static const char ready[]="stdin: pipe verified; file: RAM read/write verified\nPOLARIS_IO_OK pid=1 pagesize=4096\n";
 exact(CALL(4,1,ready,sizeof(ready)-1,0),sizeof(ready)-1,33);
 for(;;){
   io_ticks++;pulse[PULSE_DIGIT]=(char)('0'+io_ticks%10);
   exact(CALL(154,fd,pulse+PULSE_DIGIT,1,512),1,34);
   exact(CALL(153,fd,buffer,1,512),1,35);
   if(buffer[0]!=(u8)pulse[PULSE_DIGIT])io_failure(36,990);
   exact(CALL(4,1,pulse,sizeof(pulse)-1,0),sizeof(pulse)-1,37);io_results[17]++;
   resident_checkpoint();
   struct { s64 seconds; int micros,padding; } timeout={1,0,0};
   r=sc(93,0,0,0,0,(s64)&timeout,0);
   if(ERROR(r)&&r.value!=4)io_failure(38,(u64)r.value);
   io_results[18]=(u64)r.value;io_results[19]++;
 }
}
