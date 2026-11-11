/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * 200 serial children, then 100 batches of eight children held at a pipe barrier.
 * Exact waits, ECHILD, inherited descriptors, four COW pages and exit-time cleanup.
 */
volatile u64 stress_results[24];
static volatile u8 stress_cow[16384] __attribute__((aligned(4096)));
__attribute__((noinline)) void stress_checkpoint(void) {__asm__ volatile("":::"memory");}
__attribute__((noinline)) void stress_sample_checkpoint(void) {__asm__ volatile("":::"memory");}
__attribute__((noinline)) void stress_batch_ready(void) {__asm__ volatile("":::"memory");}
static void stress_quiesce_sample(void)
{
 /* wait4 finishes before all deferred proc/task references must disappear.
  * Let the reaper run before taking each quiescent resource sample. */
 struct {s64 seconds;int micros,padding;} delay={0,20000,0};
 exact(sc(93,0,0,0,0,(s64)&delay,0),0,80);stress_sample_checkpoint();
}
static void stress_decimal(char *text,u64 value)
{
 char digits[24];unsigned n=0;
 do {digits[n++]='0'+value%10;value/=10;}while(value);
 for(unsigned i=0;i<n;i++)text[i]=digits[n-1-i];text[n]=0;
}
static void stress_check_fd(u64 baseline)
{
 s64 fd=open_file("/etc/message",0,80);
 process_require((u64)fd==baseline,80,(u64)fd);close_file(fd,80);stress_results[5]++;
 /* This fixture's maximum live FD is below 64 (32 temporary files).
  * Checking only the first free FD would miss a leaked higher descriptor. */
 for(unsigned descriptor=0;descriptor<64;descriptor++) {
  struct reply r=CALL(92,descriptor,1,0,0);
  if(descriptor<baseline)process_require(!ERROR(r),80,descriptor);
  else expect_error(r,9,80); /* EBADF */
 }
}
static void stress_run(s64 state_fd)
{
 s64 baseline=open_file("/etc/message",0,80);close_file(baseline,80);
 stress_results[11]=(u64)baseline;
 for(unsigned p=0;p<4;p++)stress_cow[p*4096]=(u8)(0x31+p);
 expect_error(CALL(7,-1,&child_status,1,0),10,80);
 stress_results[0]=1;stress_quiesce_sample();
 unsigned done=0;
 while(done<1000) {
  unsigned width=done<200?1:8;
  struct reply pipe=CALL(42,0,0,0,0);s64 reader=must(pipe,81),writer=pipe.second;
  s64 children[8];
  for(unsigned slot=0;slot<width;slot++) {
   char token_text[24],slot_text[24],fd_text[24];
   stress_decimal(token_text,done+slot+1);stress_decimal(slot_text,slot);stress_decimal(fd_text,state_fd);
   struct reply child=CALL(2,0,0,0,0);
   if(ERROR(child))process_failure(82,(u64)child.value);
   if(child.second) {
    for(unsigned p=0;p<4;p++)stress_cow[p*4096]=(u8)(0xa0+p);
    for(unsigned p=0;p<4;p++)if(stress_cow[p*4096]!=(u8)(0xa0+p))process_failure(83,p);
    close_file(writer,83);char go=0;
    exact(CALL(3,reader,&go,1,0),1,83);
    if(go!='g')process_failure(83,1);close_file(reader,83);
    char *argv[]={"/bin/probe","stress",token_text,slot_text,fd_text,0};char *envp[]={0};
    (void)CALL(59,"/bin/probe",argv,envp,0);
    (void)CALL(1,71,0,0,0);reboot_return();
   }
   children[slot]=child.value;process_require(child.value>1,82,1);stress_results[2]++;
  }
  stress_results[10]=width;
  if(width==8 && done==200)stress_batch_ready();
  static const char go[]="gggggggg";
  exact(CALL(4,writer,go,width,0),width,84);close_file(reader,84);close_file(writer,84);
  for(unsigned slot=0;slot<width;slot++) {
   struct reply wait=CALL(7,children[slot],&child_status,0,0);
   if(ERROR(wait))process_failure(85,(u64)wait.value);
   process_require(wait.value==children[slot] && (unsigned)child_status==(unsigned)((42+slot)<<8),85,(unsigned)child_status);
   stress_results[3]++;
   u64 report[8];exact(CALL(153,state_fd,report,sizeof(report),2560+slot*sizeof(report)),sizeof(report),86);
   process_require(report[0]==0x504f4c4152535452UL && report[1]==done+slot+1 && report[2]==(u64)children[slot] &&
                   report[3]==1 && report[4]==slot && report[5]==(u64)state_fd && report[6] && !(report[6]%16) && report[7]==3,86,slot);
   stress_results[4]++;
  }
  /* No waitable or still running children may remain at a completed batch. */
  expect_error(CALL(7,-1,&child_status,1,0),10,87);stress_results[6]++;
  expect_error(CALL(7,children[0],&child_status,1,0),10,87);stress_results[7]++;
  expect_error(CALL(37,children[0],0,0,0),3,87);stress_results[8]++;
  for(unsigned p=0;p<4;p++)process_require(stress_cow[p*4096]==(u8)(0x31+p),88,p);
  stress_results[9]++;
  done+=width;stress_results[1]=done;stress_results[12]++;
  stress_check_fd((u64)baseline);
  if(done==200 || done==208 || !(done%200))stress_quiesce_sample();
  if(!(done%200)) {
   static const char prefix[]="POLARIS_STRESS_RUN done=";
   (void)CALL(4,1,prefix,sizeof(prefix)-1,0);print_number(done);
   static const char suffix[]="/1000\n";(void)CALL(4,1,suffix,sizeof(suffix)-1,0);
  }
 }
 /* Every pool slot must be available after child-owned unlinked FDs exit. */
 s64 held[32];
 for(unsigned i=0;i<32;i++) {
  held[i]=tmp_create("/tmp/exit-cleanup");exact(CALL(10,"/tmp/exit-cleanup",0,0,0),0,89);
 }
 expect_error(CALL(5,"/tmp/full",0xa02,0600,0),28,89);
 for(unsigned i=0;i<32;i++)close_file(held[i],89);
 stress_results[13]=32;stress_check_fd((u64)baseline);
 stress_results[0]=100;stress_checkpoint();
 static const char ok[]="POLARIS_STRESS_OK total=1000 reaped=1000 maxlive=8\n"
                        "POLARIS_STRESS_CLEAN nochild=OK fd=OK tmp=OK cow=OK\n";
 exact(CALL(4,1,ok,sizeof(ok)-1,0),sizeof(ok)-1,89);
}
