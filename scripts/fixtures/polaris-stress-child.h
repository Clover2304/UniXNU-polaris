/* SPDX-License-Identifier: BSD-2-Clause-Patent */
static u64 stress_number(const char *text)
{
 u64 value=0;unsigned n=0;
 while(text[n]) {require(n<6 && text[n]>='0' && text[n]<='9',21);value=value*10+text[n++]-'0';}
 require(n>0,21);return value;
}
static void stress_child_maybe(u64 initial_sp,u64 aligned_sp)
{
 u64 *stack=(u64 *)initial_sp;
 if(stack[0]!=5 || !same((void *)stack[2],"stress",7))return;
 u64 token=stress_number((char *)stack[3]),slot=stress_number((char *)stack[4]),inherited=stress_number((char *)stack[5]);
 require(token>=1 && token<=1000 && slot<8 && inherited>=3 && inherited<64,22);
 struct reply r=CALL(92,inherited,1,0,0);require(!ERROR(r),23); /* fcntl(F_GETFD) */
 char data[80];static const char parent[]="RAM file roundtrip from PID 1\n";
 r=CALL(153,inherited,data,sizeof(parent)-1,128);
 require(!ERROR(r)&&r.value==sizeof(parent)-1&&same(data,parent,sizeof(parent)-1),23);
 char path[]="/tmp/stress0";path[11]='0'+slot;
 r=CALL(5,path,0xa02,0600,0);require(!ERROR(r),24);s64 temporary=r.value;
 r=CALL(4,temporary,&token,sizeof(token),0);require(!ERROR(r)&&r.value==sizeof(token),24);
 u64 got=0;r=CALL(153,temporary,&got,sizeof(got),0);require(!ERROR(r)&&r.value==sizeof(got)&&got==token,25);
 require(!ERROR(CALL(10,path,0,0,0)),26);
 /* Deliberately leave both these descriptors open: exit must release them. */
 r=CALL(5,"/etc/message",0,0,0);require(!ERROR(r),27);
 u64 record[8]={0x504f4c4152535452UL,token,probe_report.pid,probe_report.ppid,slot,inherited,aligned_sp,3};
 r=CALL(154,inherited,record,sizeof(record),2560+slot*sizeof(record));require(!ERROR(r)&&r.value==sizeof(record),28);
 (void)CALL(1,42+slot,0,0,0);reboot_return();
}
