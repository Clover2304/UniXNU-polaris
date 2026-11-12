/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Real file-signature registration, native page faults and cleanup on an
 * unchanged original subcache. No libc or host-injected syscall results.
 */
#include "polaris-runtime.h"
#ifndef POLARIS_CACHE_EXPECTED_XOR
#error Original cache metadata is required
#endif
volatile u64 results[4],user_page_size,old_length=8;
volatile u64 cache_results[16];
static char live[]="\rPOLARIS_CACHE_LIVE tick=0 ";
static char failed[]="POLARIS_CACHE_FAILED stage=00 errno=000\n";
__attribute__((noinline)) void checkpoint(void){__asm__ volatile("":::"memory");}
__attribute__((noinline)) void cache_checkpoint(void){__asm__ volatile("":::"memory");}
__attribute__((noinline)) void cache_resident_checkpoint(void){__asm__ volatile("":::"memory");}
__attribute__((noreturn,noinline)) void reboot_return(void){for(;;)__asm__ volatile("b .");}
static void emit(const char *s,u64 n){(void)CALL(4,1,s,n,0);}
static int check(struct reply r,u64 stage){
 if(!ERROR(r))return 1;
 cache_results[0]=stage;cache_results[1]=(u64)r.value;return 0;
}
static void run_cache(void){
 struct reply r=CALL(5,"/cache.bin",0,0,0);
 if(!check(r,10))return;
 s64 fd=r.value;cache_results[2]=(u64)fd;
 u64 signature[3]={0,32768,16384};
 r=CALL(92,fd,97,signature,0);cache_results[3]=(u64)r.value;cache_results[4]=signature[0];
 if(!check(r,20)){(void)CALL(6,fd,0,0,0);return;}
 if(signature[0]!=32768){cache_results[0]=21;cache_results[1]=999;(void)CALL(6,fd,0,0,0);return;}
 r=sc(197,0,32768,5,2,fd,0);cache_results[5]=(u64)r.value;
 if(!check(r,30)){(void)CALL(6,fd,0,0,0);return;}
 volatile const u64 *mapped=(volatile const u64 *)(u64)r.value;
 u64 checksum=0,pages=0;
 for(u64 offset=0;offset<32768;offset+=4096){checksum^=mapped[offset/8];pages++;}
 cache_results[6]=pages;cache_results[7]=checksum;
 if(pages!=8 || checksum!=POLARIS_CACHE_EXPECTED_XOR){
  cache_results[0]=40;cache_results[1]=998;(void)CALL(73,mapped,32768,0,0);(void)CALL(6,fd,0,0,0);return;
 }
 r=CALL(73,mapped,32768,0,0);cache_results[8]=(u64)r.value;
 if(!check(r,50)){(void)CALL(6,fd,0,0,0);return;}
 r=CALL(6,fd,0,0,0);cache_results[9]=(u64)r.value;
 if(!check(r,60))return;
 cache_results[0]=100;
}
POLARIS_ENTRY(polaris_cache_start)
__attribute__((noreturn,used)) void polaris_cache_start(u64 initial_sp){
 u64 stack;__asm__ volatile("mov %0,sp":"=r"(stack));
 cache_results[12]=initial_sp;cache_results[13]=stack;
 struct reply r=CALL(20,0,0,0,0);results[0]=(u64)r.value;results[2]=r.flags;
 int mib[2]={6,7};old_length=8;
 r=sc(202,(s64)mib,2,(s64)&user_page_size,(s64)&old_length,0,0);
 results[1]=(u64)r.value;results[3]=r.flags;checkpoint();
 if(results[0]!=1 || results[1] || ((results[2]|results[3])&(1UL<<29)) || user_page_size!=4096 || old_length!=4){
  cache_results[0]=1;cache_results[1]=997;
 }else{
  r=CALL(5,"/dev/console",2,0,0);
  if(check(r,2)){
   s64 console=r.value;
   r=CALL(90,console,1,0,0);
   if(check(r,3)){
    (void)CALL(90,console,2,0,0);if(console>2)(void)CALL(6,console,0,0,0);
    static const char start[]="\nPOLARIS_CACHE_START pid=1 pagesize=4096\n";emit(start,sizeof(start)-1);
    run_cache();
   }
  }
 }
 if(cache_results[0]==100){
  static const char ok[]="POLARIS_CACHE_OK original=1 blocks16k=2 pages4k=8 map=RX cleanup=OK\n";emit(ok,sizeof(ok)-1);
 }else{
  u64 stage=cache_results[0],error=cache_results[1];
  failed[sizeof("POLARIS_CACHE_FAILED stage=")-1]=(char)('0'+stage/10%10);
  failed[sizeof("POLARIS_CACHE_FAILED stage=")]=(char)('0'+stage%10);
  failed[sizeof("POLARIS_CACHE_FAILED stage=00 errno=")-1]=(char)('0'+error/100%10);
  failed[sizeof("POLARIS_CACHE_FAILED stage=00 errno=")]=(char)('0'+error/10%10);
  failed[sizeof("POLARIS_CACHE_FAILED stage=00 errno=")+1]=(char)('0'+error%10);emit(failed,sizeof(failed)-1);
 }
 cache_checkpoint();
 for(;;){
  cache_results[10]++;live[sizeof("\rPOLARIS_CACHE_LIVE tick=")-1]=(char)('0'+cache_results[10]%10);
  emit(live,sizeof(live)-1);cache_resident_checkpoint();
  struct{s64 sec;int usec,pad;} timeout={1,0,0};
  (void)sc(93,0,0,0,0,(s64)&timeout,0);
 }
}
