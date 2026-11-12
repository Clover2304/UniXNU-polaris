/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Actual EL0 syscalls against the fixed read-only devfs file view.
 */
volatile u64 ufs_node_results[16];
static u8 node_buffer[4096];
static void node_pattern(u64 offset,u64 bytes,u64 stage)
{
 for(u64 i=0;i<bytes;i++)if(node_buffer[i]!=(u8)((offset+i)*37+((offset+i)>>8)*11+0x51))io_failure(stage,989);
}
static void node_run(void)
{
 s64 n=-1;
 for(unsigned attempt=0;attempt<750;attempt++){
  struct reply r=CALL(5,"/dev/polaris-esp-file",0,0,0);
  if(!ERROR(r)){n=r.value;break;}
  if(r.value!=2)io_failure(80,(u64)r.value);
  if(!attempt){static const char waiting[]="POLARIS_UFS_NODE_WAIT\n";exact(CALL(4,1,waiting,sizeof(waiting)-1,0),sizeof(waiting)-1,80);}
  struct {s64 seconds;int micros,padding;} timeout={0,20000,0};
  r=sc(93,0,0,0,0,(s64)&timeout,0);if(ERROR(r)&&r.value!=4)io_failure(80,(u64)r.value);
 }
 if(n<0)io_failure(80,2);
 ufs_node_results[1]=1;
 u64 off=0;
 while(off<8193){u64 count=8193-off;if(count>4096)count=4096;
  exact(CALL(3,n,node_buffer,count,0),(s64)count,81);node_pattern(off,count,82);off+=count;
 }
 ufs_node_results[2]=off;
 exact(CALL(3,n,node_buffer,1,0),0,83);
 exact(CALL(153,n,node_buffer,1,8193),0,83);
 exact(CALL(153,n,node_buffer,1,99999),0,83);ufs_node_results[3]=1;
 exact(CALL(153,n,node_buffer,0,0),0,84);
 expect_error(CALL(153,n,node_buffer,1,-1),22,84);ufs_node_results[4]=1;
 const u64 offsets[]={1023,4095,8192};const u64 lengths[]={2050,2050,1};
 for(unsigned i=0;i<3;i++){
  exact(CALL(153,n,node_buffer,lengths[i],offsets[i]),(s64)lengths[i],85);
  node_pattern(offsets[i],lengths[i],86);
 }ufs_node_results[5]=3;
 for(unsigned i=0;i<2;i++){
  exact(CALL(153,n,node_buffer,4096,0),4096,87);node_pattern(0,4096,88);
 }ufs_node_results[6]=2;
 expect_error(CALL(5,"/dev/polaris-esp-file",1,0,0),30,89);
 expect_error(CALL(5,"/dev/polaris-esp-file",2,0,0),30,89);ufs_node_results[7]=2;
 expect_error(CALL(4,n,node_buffer,1,0),9,90);ufs_node_results[8]=9;
 close_file(n,91);n=open_file("/dev/polaris-esp-file",0,91);
 exact(CALL(153,n,node_buffer,16,37),16,92);node_pattern(37,16,92);
 close_file(n,93);ufs_node_results[9]=1;ufs_node_results[0]=100;
 static const char ready[]="POLARIS_UFS_NODE_OK bytes=8193 cross=3 repeat=2 eof=OK readonly=OK reopen=OK\n";
 exact(CALL(4,1,ready,sizeof(ready)-1,0),sizeof(ready)-1,94);
}
