/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Own second executable. Reads its named file, writes shared RAM state,
 * reports its real PID/PPID and exits with a status for PID 1 to reap.
 */
#include "polaris-runtime.h"
volatile u64 results[4],user_page_size,old_length=8;
volatile struct child_report probe_report;
__attribute__((noinline)) void checkpoint(void) { __asm__ volatile("":::"memory"); }
__attribute__((noinline)) void probe_checkpoint(void) { __asm__ volatile("":::"memory"); }
__attribute__((noreturn,noinline)) void reboot_return(void) { for(;;)__asm__ volatile("b ."); }
static void require(int condition,u64 stage)
{
 if(!condition) { (void)CALL(1,80+stage,0,0,0);reboot_return(); }
}
#ifdef POLARIS_STRESS_TEST
#include "polaris-stress-child.h"
#endif
POLARIS_ENTRY(polaris_probe_start)
__attribute__((noreturn,used)) void polaris_probe_start(u64 initial_sp)
{
 u64 aligned_sp;__asm__ volatile("mov %0, sp":"=r"(aligned_sp));
 probe_report.initial_sp=initial_sp;probe_report.c_stack_sp=aligned_sp;
 struct reply r=CALL(20,0,0,0,0);require(!ERROR(r)&&r.value>1,1);probe_report.pid=(u64)r.value;
 r=CALL(39,0,0,0,0);require(!ERROR(r)&&r.value==1,2);probe_report.ppid=(u64)r.value;
#ifdef POLARIS_STRESS_TEST
 stress_child_maybe(initial_sp,aligned_sp);
#endif
 r=CALL(5,"/etc/message",0,0,0);require(!ERROR(r),3);s64 message_fd=r.value;
 char data[80];static const char expected[]=PROBE_MESSAGE;
 r=CALL(3,message_fd,data,sizeof(expected)-1,0);
 require(!ERROR(r)&&r.value==sizeof(expected)-1&&same(data,expected,sizeof(expected)-1),4);
 require(!ERROR(CALL(6,message_fd,0,0,0)),5);
 r=CALL(5,"/probe.bin",2,0,0);require(!ERROR(r),6);s64 state_fd=r.value;
 static const char parent_data[]="RAM file roundtrip from PID 1\n";
 r=CALL(153,state_fd,data,sizeof(parent_data)-1,128);
 require(!ERROR(r)&&r.value==sizeof(parent_data)-1&&same(data,parent_data,sizeof(parent_data)-1),7);
 static const char marker[]=CHILD_MARKER;
 r=CALL(154,state_fd,marker,sizeof(marker)-1,1024);require(!ERROR(r)&&r.value==sizeof(marker)-1,8);
 probe_report.magic=CHILD_REPORT_MAGIC;probe_report.checks=4;
 r=CALL(154,state_fd,&probe_report,sizeof(probe_report),2048);require(!ERROR(r)&&r.value==sizeof(probe_report),9);
 require(!ERROR(CALL(6,state_fd,0,0,0)),10);
#ifdef POLARIS_TMP_TEST
 r=CALL(5,"/tmp/shared",0,0,0);require(!ERROR(r),11);s64 tmp_fd=r.value;
 static const char shared[]="POLARIS TMP SHARED\n";
 r=CALL(3,tmp_fd,data,sizeof(shared)-1,0);
 require(!ERROR(r)&&r.value==sizeof(shared)-1&&same(data,shared,sizeof(shared)-1),12);
 require(!ERROR(CALL(6,tmp_fd,0,0,0)),13);
 probe_report.checks=5;
 r=CALL(5,"/probe.bin",2,0,0);require(!ERROR(r),14);s64 report_fd=r.value;
 r=CALL(154,report_fd,&probe_report,sizeof(probe_report),2048);require(!ERROR(r)&&r.value==sizeof(probe_report),15);
 require(!ERROR(CALL(6,report_fd,0,0,0)),16);
 static const char tmp_ok[]="POLARIS_TMP_CHILD_OK shared=OK\n";
 (void)CALL(4,1,tmp_ok,sizeof(tmp_ok)-1,0);
#endif
 static const char prefix[]="POLARIS_CHILD_OK pid=";
 (void)CALL(4,1,prefix,sizeof(prefix)-1,0);print_number(probe_report.pid);
 static const char suffix[]=" ppid=1 file=OK\n";(void)CALL(4,1,suffix,sizeof(suffix)-1,0);
 probe_checkpoint();(void)CALL(1,42,0,0,0);reboot_return();
}
