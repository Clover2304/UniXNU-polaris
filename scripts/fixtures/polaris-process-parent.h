/* Own PID 1 process test; included only by the process-stage init. */
volatile u64 process_results[32];
static volatile u64 fork_cookie=0x123456789abcdef0UL;
static char exec_path[32];
static int child_status;
__attribute__((noinline)) void process_checkpoint(void) { __asm__ volatile("":::"memory"); }
__attribute__((noreturn,noinline)) void process_failure(u64 stage,u64 detail)
{
 process_results[0]=stage;process_results[1]=detail;io_failure(stage,detail);
}
static void process_require(int condition,u64 stage,u64 detail)
{
 if(!condition)process_failure(stage,detail);
}
#ifdef POLARIS_TMP_TEST
#include "polaris-tmp-parent.h"
#endif
static void process_run(s64 state_fd)
{
 static const char message[]=PROBE_MESSAGE;
 s64 f=open_file("/etc/message",0,40);
 exact(CALL(3,f,buffer,sizeof(message)-1,0),sizeof(message)-1,40);
 process_require(same(buffer,message,sizeof(message)-1),40,1);
 process_results[14]=sizeof(message)-1;close_file(f,40);
 exact(CALL(12,"/etc",0,0,0),0,40);
 f=open_file("message",0,40);close_file(f,40);
 exact(CALL(12,"/",0,0,0),0,40);
 expect_error(CALL(5,"/bin/probe_extra",0,0,0),2,41);
 expect_error(CALL(5,"/sbin/probe",0,0,0),2,41);process_results[15]=2;
 f=open_file("/etc/target",0,41);
 s64 count=must(CALL(3,f,exec_path,sizeof(exec_path)-1,0),41);close_file(f,41);
 process_require(count>0&&count<(s64)sizeof(exec_path),41,2);
 if(exec_path[count-1]=='\n')exec_path[count-1]=0;
 process_require(same(exec_path,"/bin/probe",11)||same(exec_path,"/sbin/launchd",14),41,3);
#ifdef POLARIS_TMP_TEST
 tmp_before_child();
#endif
 struct reply forked=CALL(2,0,0,0,0);
 if(ERROR(forked))process_failure(42,(u64)forked.value);
 if(forked.second) {
  fork_cookie=0xfedcba9876543210UL;
  char *argv[]={exec_path,"polaris-test",0};char *envp[]={0};
  (void)CALL(59,exec_path,argv,envp,0);
  (void)CALL(1,71,0,0,0);reboot_return();
 }
 process_results[2]=(u64)forked.value;process_results[17]=1;
 process_require(forked.value>1,42,4);
 struct reply waited=CALL(7,forked.value,&child_status,0,0);
 if(ERROR(waited))process_failure(43,(u64)waited.value);
 process_results[3]=(u64)waited.value;process_results[4]=(unsigned)child_status;
 process_results[5]=((unsigned)child_status>>8)&255;
 process_require(waited.value==forked.value && (child_status&127)==0 && process_results[5]==42,44,(unsigned)child_status);
 process_results[6]=1;
 process_require(fork_cookie==0x123456789abcdef0UL,45,1);process_results[7]=1;
 static const char marker[]=CHILD_MARKER;
 exact(CALL(153,state_fd,buffer,sizeof(marker)-1,1024),sizeof(marker)-1,46);
 process_require(same(buffer,marker,sizeof(marker)-1),46,1);
 struct child_report child;
 exact(CALL(153,state_fd,&child,sizeof(child),2048),sizeof(child),47);
 process_results[8]=sizeof(child);process_results[9]=child.pid;process_results[10]=child.ppid;
 process_results[11]=child.initial_sp;process_results[12]=child.c_stack_sp;process_results[13]=child.checks;
 process_require(child.magic==CHILD_REPORT_MAGIC && child.pid==(u64)forked.value && child.ppid==1 &&
                 child.initial_sp && child.c_stack_sp && child.c_stack_sp%16==0 && child.checks==
#ifdef POLARIS_TMP_TEST
                 5,
#else
                 4,
#endif
                 47,1);
 process_results[0]=100;process_checkpoint();
 static const char prefix[]="POLARIS_PROC_OK child=";
 (void)CALL(4,1,prefix,sizeof(prefix)-1,0);print_number(child.pid);
 static const char suffix[]=" exit=42 reaped=1 cow=OK\n";
 (void)CALL(4,1,suffix,sizeof(suffix)-1,0);
#ifdef POLARIS_TMP_TEST
 tmp_after_child();
#endif
}
