#include <initializer_list>
#include "UfsNode.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
struct Disk {
 FILE *file;unsigned reads=0;
 static bool read(void *p,uint32_t block,uint8_t *out){
  auto &d=*static_cast<Disk *>(p);assert(block<UFS_ESP_BLOCKS);d.reads++;
  return !std::fseek(d.file,long(block)*4096,SEEK_SET)&&std::fread(out,1,4096,d.file)==4096;
 }
};
static void lifecycle(){
 PolarisUfsNodeQueue q;uint8_t out[4096];int err=-1;
 assert(!q.submit(0,0)&&!q.submit(8193,1)&&!q.submit(8192,2)&&!q.submit(0,4097));
 assert(!q.claim()&&!q.collect(out,err));q.cancel();q.finish(0);assert(q.state==q.Idle);
 for(unsigned i=0;i<1000;i++){
  assert(q.submit(8192,1));assert(!q.submit(0,1));
  q.cancel();assert(q.state==q.Idle);assert(!q.claim());
  assert(q.submit(0,4096));assert(q.claim());q.data[0]=0xa5;
  q.cancel();assert(q.state==q.Abandoned);assert(!q.submit(0,1)&&!q.collect(out,err));
  q.finish(0);assert(q.state==q.Idle);assert(q.submit(0,1));assert(q.claim());
  q.finish(5);assert(q.collect(out,err)&&err==5&&q.state==q.Idle);
  assert(q.submit(8192,1));assert(q.claim());q.data[0]=0x42;q.finish(0);
  assert(!q.submit(0,1));assert(q.collect(out,err)&&!err&&out[0]==0x42&&q.state==q.Idle);
 }
}
int main(int argc,char **argv){
 lifecycle();if(argc==1)return 0;
 Disk disk{std::fopen(argv[1],"rb")};assert(disk.file);
 PolarisFatResult file;PolarisFatWork work;PolarisFatReader reader({&disk,Disk::read},work,file);assert(reader.run());
 uint8_t page[4096],result[4096];
 unsigned repeated=0;
 for(auto offset:{0u,37u,1023u,1024u,4095u,4096u,8192u}){
  unsigned length=8193-offset;if(length>4096)length=4096;
  for(unsigned attempt=0;attempt<2;attempt++){
   unsigned before=disk.reads,done=0;uint32_t cached=~0u;
   while(done<length){uint32_t block;unsigned within,n;
    assert(polaris_ufs_node_span(file,offset+done,length-done,block,within,n));
    if(block!=cached){assert(Disk::read(&disk,block,page));cached=block;}
    std::memcpy(result+done,page+within,n);done+=n;
   }
   assert(disk.reads>before);repeated++;
   for(unsigned i=0;i<length;i++)assert(result[i]==uint8_t((offset+i)*37+((offset+i)>>8)*11+0x51));
  }
 }
 assert(repeated==14);uint32_t block;unsigned within,n;
 assert(!polaris_ufs_node_span(file,8193,1,block,within,n));
 assert(!polaris_ufs_node_span(file,~0ull,1,block,within,n));
 assert(!polaris_ufs_node_span(file,0,4097,block,within,n));
 file.chain[0]=1;assert(!polaris_ufs_node_span(file,0,1,block,within,n));
 file.chain[0]=0xffffffff;assert(!polaris_ufs_node_span(file,0,1,block,within,n));
 file.chain[0]=2;file.dataSector=0xffffffff;assert(!polaris_ufs_node_span(file,0,1,block,within,n));
 std::fclose(disk.file);
}
