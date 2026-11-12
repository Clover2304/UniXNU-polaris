/* Own offline process; original read-only data mounts and small cache mapping. */
#include "polaris-runtime.h"
#include "storage-input.h"
#ifdef STORAGE_PREBOOT_CACHE
#define STORAGE_CACHE_MOUNT "/dev/osroot/private/preboot/Cryptexes"
#define STORAGE_CACHE_DIR "/private/preboot/Cryptexes/System/Library/Caches/com.apple.dyld"
#else
#define STORAGE_CACHE_MOUNT "/dev/osroot/dev"
#define STORAGE_CACHE_DIR "/dev/System/Library/Caches/com.apple.dyld"
#endif
void bzero(void *p,u64 n){volatile u8 *b=p;for(u64 i=0;i<n;i++)b[i]=0;}
volatile u64 results[4],user_page_size,old_length=8,storage_results[24];
__attribute__((noinline)) void checkpoint(void){__asm__ volatile("":::"memory");}
__attribute__((noinline)) void storage_checkpoint(void){__asm__ volatile("":::"memory");}
__attribute__((noinline)) void storage_exec_checkpoint(void){__asm__ volatile("":::"memory");}
__attribute__((noinline)) void storage_resident_checkpoint(void){__asm__ volatile("":::"memory");}
__attribute__((noreturn,noinline)) void reboot_return(void){for(;;)__asm__ volatile("b .");}
static void emit(const char *s,u64 n){(void)CALL(4,1,s,n,0);}
static int check(struct reply r,u64 stage){
 if(!ERROR(r))return 1;
 storage_results[0]=stage;storage_results[1]=(u64)r.value;return 0;
}
static void pause_second(void){struct{s64 sec;int usec,pad;} timeout={1,0,0};(void)sc(93,0,0,0,0,(s64)&timeout,0);}
static int mount_volume(const char *device,const char *point,u64 stage){
 u8 args[0x150] __attribute__((aligned(8)))={0};*(u64 *)(void *)args=(u64)device;*(u64 *)(void *)(args+8)=1;
 *(unsigned short *)(void *)(args+16)=1;*(unsigned *)(void *)(args+0x144)=99;*(unsigned *)(void *)(args+0x148)=99;
 struct reply r=CALL(167,"apfs",point,1,args);
 storage_results[stage]=r.value;storage_results[stage+1]=r.flags;
 return check(r,stage);
}
static int prefix_file(const char *path,const u8 *expected,u64 expected_size,u64 stage){
 struct reply r=CALL(5,path,0,0,0);if(!check(r,stage))return 0;s64 fd=r.value;u8 data[256];
 r=CALL(3,fd,data,sizeof(data),0);
 if(!check(r,stage+1) || r.value!=sizeof(data) || !same(data,expected,sizeof(data))){
  storage_results[0]=stage+1;if(!storage_results[1])storage_results[1]=990;(void)CALL(6,fd,0,0,0);return 0;
 }
 r=CALL(199,fd,0,2,0);storage_results[stage]=r.value;
 if(!check(r,stage+2) || (u64)r.value!=expected_size){storage_results[0]=stage+2;(void)CALL(6,fd,0,0,0);return 0;}
 return check(CALL(6,fd,0,0,0),stage+3);
}
static void probe(void){
 if(!check(CALL(136,"/dev/osroot",0755,0,0),2))return;
#ifndef STORAGE_LAUNCH_APPLE
 if(!check(CALL(136,"/dev/cachefs",0755,0,0),3))return;
#endif
 if(!mount_volume(STORAGE_OS_DEVICE,"/dev/osroot",4))return;
#ifdef STORAGE_LAUNCH_APPLE
#ifdef STORAGE_PREBOOT_CACHE
 struct reply preboot=CALL(167,"devfs","/dev/osroot/private/preboot",0,0);
 storage_results[8]=preboot.value;storage_results[9]=preboot.flags;
 if(!check(preboot,47))return;
 if(!check(CALL(136,STORAGE_CACHE_MOUNT,0755,0,0),48))return;
#endif
 if(!mount_volume(STORAGE_CACHE_DEVICE,STORAGE_CACHE_MOUNT,6))return;
#else
 if(!mount_volume(STORAGE_CACHE_DEVICE,"/dev/cachefs",6))return;
#endif
 if(!prefix_file("/dev/osroot/sbin/launchd",expected_launchd,561856,10))return;
 if(!prefix_file("/dev/osroot/usr/lib/dyld",expected_dyld,937056,14))return;
#ifdef STORAGE_LAUNCH_APPLE
 if(!check(CALL(61,"/dev/osroot",0,0,0),40))return;
 if(!check(CALL(12,"/",0,0,0),41))return;
 struct reply cache=CALL(5,STORAGE_CACHE_DIR "/dyld_shared_cache_arm64",0,0,0);
 if(!check(cache,42))return;
 if(!check(CALL(6,cache.value,0,0,0),43))return;
#ifdef STORAGE_SKIP_IGNITION
 /* Existing DEVELOPMENT sysctl publishes the same value to the commpage. */
 int lookup[2]={0,3},oid[12];u64 oid_bytes=sizeof(oid),flags=8;
 if(!check(sc(202,(s64)lookup,2,(s64)oid,(s64)&oid_bytes,(s64)"kern.dyld_flags",15),45))return;
 if(!oid_bytes || oid_bytes>sizeof(oid) || oid_bytes%sizeof(int)){storage_results[0]=45;storage_results[1]=997;return;}
 if(!check(sc(202,(s64)oid,oid_bytes/sizeof(int),0,0,(s64)&flags,sizeof(flags)),46))return;
#endif
 storage_results[0]=200;storage_exec_checkpoint();
#ifndef STORAGE_STOP_BEFORE_EXEC
 const char *argv[]={"/sbin/launchd",0};
#ifdef STORAGE_PRIVATE_CACHE
 const char *envp[]={"DYLD_SHARED_CACHE_DIR=" STORAGE_CACHE_DIR,"DYLD_SHARED_REGION=private",0};
#else
 const char *envp[]={"DYLD_SHARED_CACHE_DIR=" STORAGE_CACHE_DIR,0};
#endif
 struct reply exec=CALL(59,argv[0],argv,envp,0);
 storage_results[0]=44;storage_results[1]=ERROR(exec)?exec.value:994;
#endif
 return;
#else
 struct reply r=CALL(5,"/dev/cachefs/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64.02",0,0,0);
 if(!check(r,30))return;s64 fd=r.value;u64 signature[3]={0,32768,16384};
 r=CALL(92,fd,97,signature,0);storage_results[18]=r.value;storage_results[19]=signature[0];
 if(!check(r,31)){(void)CALL(6,fd,0,0,0);return;}
 if(signature[0]!=32768){storage_results[0]=32;storage_results[1]=991;(void)CALL(6,fd,0,0,0);return;}
 r=sc(197,0,32768,5,2,fd,0);storage_results[20]=r.value;
 if(!check(r,33)){(void)CALL(6,fd,0,0,0);return;}
 volatile u64 *data=(volatile u64 *)(u64)r.value;u64 checksum=0;
 for(u64 i=0;i<8;i++)checksum^=data[i*512];storage_results[21]=checksum;
 if(checksum!=4687520156310079700UL){storage_results[0]=34;storage_results[1]=992;(void)CALL(73,data,32768,0,0);(void)CALL(6,fd,0,0,0);return;}
 if(!check(CALL(73,data,32768,0,0),35)){(void)CALL(6,fd,0,0,0);return;}
 if(!check(CALL(6,fd,0,0,0),36))return;
 r=CALL(5,"/dev/osroot/sbin/launchd",1,0,0);storage_results[2]=r.flags;storage_results[23]=r.value;
 if(!ERROR(r)){(void)CALL(6,r.value,0,0,0);storage_results[0]=37;storage_results[1]=993;return;}
 if(r.value!=30){storage_results[0]=38;storage_results[1]=r.value;return;}
 storage_results[0]=100;
#endif
}
POLARIS_ENTRY(storage_start)
__attribute__((noreturn,used)) void storage_start(u64 sp){
 (void)sp;struct reply r=CALL(20,0,0,0,0);results[0]=r.value;results[2]=r.flags;
 int mib[2]={6,7};r=sc(202,(s64)mib,2,(s64)&user_page_size,(s64)&old_length,0,0);results[1]=r.value;results[3]=r.flags;checkpoint();
 r=CALL(5,"/dev/console",2,0,0);
 if(check(r,1)){
  s64 fd=r.value;(void)CALL(90,fd,1,0,0);(void)CALL(90,fd,2,0,0);if(fd>2)(void)CALL(6,fd,0,0,0);
  static const char start[]="POLARIS_STORAGE_START offline=1 readonly=1\n";emit(start,sizeof(start)-1);
  for(unsigned i=0;i<5;i++)pause_second();
  if(results[0]==1 && !results[1] && user_page_size==4096){
#ifdef STORAGE_DISCOVERY
   storage_results[0]=101;
#else
   probe();
#endif
  }
 }
 static const char ok[]="POLARIS_STORAGE_OK mounts=2 original_prefixes=2 cache16k=2 pages4k=8 cleanup=OK readonly=OK\n";
 static const char fail[]="POLARIS_STORAGE_FAILED stage=";
 static const char prepared[]="POLARIS_STORAGE_PREPARED mounts=2 chroot=OK cache_path=OK\n";
 if(storage_results[0]==100)emit(ok,sizeof(ok)-1);
 else if(storage_results[0]==200)emit(prepared,sizeof(prepared)-1);
 else{emit(fail,sizeof(fail)-1);print_number(storage_results[0]);emit(" errno=",7);print_number(storage_results[1]);emit("\n",1);}
 storage_checkpoint();
 for(;;){storage_results[22]++;storage_resident_checkpoint();pause_second();}
}
