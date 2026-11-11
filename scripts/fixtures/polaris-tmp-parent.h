/* Own syscall-level acceptance checks for the bounded writable /tmp. */
volatile u64 tmp_results[16];
static char tmp_directory[512];
static const char tmp_shared[]="POLARIS TMP SHARED\n";
__attribute__((noinline)) void tmp_checkpoint(void) {__asm__ volatile("":::"memory");}
static s64 tmp_create(const char *path)
{
 return must(CALL(5,path,0xa02,0600,0),60); /* O_CREAT|O_EXCL|O_RDWR */
}
static void tmp_before_child(void)
{
 s64 f=tmp_create("/tmp/shared");
 exact(CALL(4,f,tmp_shared,sizeof(tmp_shared)-1,0),sizeof(tmp_shared)-1,60);
 close_file(f,60);tmp_results[1]=1;
 expect_error(CALL(5,"/tmp/shared",0xa02,0600,0),17,60);
 expect_error(CALL(5,"/etc/new",0xa02,0600,0),30,60);
}
static void tmp_after_child(void)
{
 s64 f=open_file("/tmp/shared",2,61);
 exact(CALL(3,f,buffer,sizeof(tmp_shared)-1,0),sizeof(tmp_shared)-1,61);
 process_require(same(buffer,tmp_shared,sizeof(tmp_shared)-1),61,1);
 exact(CALL(3,f,buffer,1,0),0,61);close_file(f,61);tmp_results[2]=1;
 /* Enumerate with a short buffer, checking record lengths, names and EOF. */
 s64 d=open_file("/tmp",0,62);u64 base=0,seen=0,calls=0;
 for (;;) {
  s64 n=must(CALL(344,d,tmp_directory,48,&base),62);calls++;
  if (!n) break;
  process_require(n<=48 && calls<20,62,2);
  for (u64 at=0;at<(u64)n;) {
   u8 *p=(u8 *)tmp_directory+at;
   unsigned length=(unsigned)p[16]|((unsigned)p[17]<<8);
   unsigned names=(unsigned)p[18]|((unsigned)p[19]<<8);
   process_require(length>=24 && !(length%8) && at+length<=(u64)n && names<32 && 21+names<length && p[21+names]==0,62,3);
   if(names==1&&same(p+21,".",1))seen|=1;
   else if(names==2&&same(p+21,"..",2))seen|=2;
   else if(names==6&&same(p+21,"shared",6)&&p[20]==8)seen|=4;
   else process_failure(62,4);
   at+=length;
  }
 }
 close_file(d,62);process_require(seen==7,62,5);tmp_results[3]=seen;
 f=open_file("/tmp/shared",0xa,63); /* O_RDWR|O_APPEND */
 exact(CALL(199,f,0,0,0),0,63);
 exact(CALL(4,f,"+",1,0),1,63);close_file(f,63);
 f=open_file("/tmp/shared",2,63);
 exact(CALL(153,f,buffer,1,sizeof(tmp_shared)-1),1,63);process_require(buffer[0]=='+',63,1);tmp_results[4]=1;
 exact(CALL(201,f,4,0,0),0,64);exact(CALL(153,f,buffer,1,4),0,64);
 exact(CALL(201,f,8,0,0),0,64);exact(CALL(153,f,buffer,4,4),4,64);
 process_require(!(buffer[0]|buffer[1]|buffer[2]|buffer[3]),64,1);
 exact(CALL(199,f,12,0,0),12,64);exact(CALL(4,f,"Z",1,0),1,64);
 exact(CALL(153,f,buffer,5,8),5,64);process_require(!(buffer[0]|buffer[1]|buffer[2]|buffer[3])&&buffer[4]=='Z',64,2);
 expect_error(CALL(201,f,4097,0,0),27,64);
 expect_error(CALL(154,f,"ZZ",2,4095),28,64);
 expect_error(CALL(154,f,0,1,0),14,64);
 exact(CALL(153,f,buffer,4,0),4,64);process_require(same(buffer,"POLA",4),64,3);tmp_results[5]=1;
 /* The old descriptor must survive unlink and same-name creation. */
 exact(CALL(10,"/tmp/shared",0,0,0),0,65);
 expect_error(CALL(5,"/tmp/shared",0,0,0),2,65);
 s64 other=tmp_create("/tmp/shared");exact(CALL(4,other,"NEW",3,0),3,65);
 exact(CALL(153,f,buffer,4,0),4,65);process_require(same(buffer,"POLA",4),65,1);
 close_file(f,65);close_file(other,65);
 other=open_file("/tmp/shared",0x402,65);exact(CALL(3,other,buffer,1,0),0,65);close_file(other,65);
 exact(CALL(10,"/tmp/shared",0,0,0),0,65);tmp_results[6]=1;
 /* All 16 directory slots, then space recovery after deletion. */
 char path[]="/tmp/f00";
 for(unsigned i=0;i<16;i++) {path[6]='0'+i/10;path[7]='0'+i%10;f=tmp_create(path);close_file(f,66);}
 expect_error(CALL(5,"/tmp/full",0xa02,0600,0),28,66);
 for(unsigned i=0;i<16;i++) {path[6]='0'+i/10;path[7]='0'+i%10;exact(CALL(10,path,0,0,0),0,66);}
 tmp_results[7]=16;
 /* Exhaust the pool with 32 unlinked but open files, then release them. */
 s64 held[32];
 for(unsigned i=0;i<32;i++) {held[i]=tmp_create("/tmp/held");exact(CALL(4,held[i],&i,4,0),4,67);exact(CALL(10,"/tmp/held",0,0,0),0,67);}
 expect_error(CALL(5,"/tmp/full",0xa02,0600,0),28,67);
 for(unsigned i=0;i<32;i++) {unsigned v=99;exact(CALL(153,held[i],&v,4,0),4,67);process_require(v==i,67,1);close_file(held[i],67);}
 tmp_results[8]=32;
 for(unsigned i=0;i<64;i++) {
  f=tmp_create("/tmp/reuse");exact(CALL(3,f,buffer,1,0),0,68);
  exact(CALL(4,f,"R",1,0),1,68);close_file(f,68);exact(CALL(10,"/tmp/reuse",0,0,0),0,68);
 }
 tmp_results[9]=64;tmp_results[0]=100;tmp_checkpoint();
 static const char ok[]="POLARIS_TMP_OK create=OK dir=OK unlink=OK reuse=64\n";
 exact(CALL(4,1,ok,sizeof(ok)-1,0),sizeof(ok)-1,69);
}
