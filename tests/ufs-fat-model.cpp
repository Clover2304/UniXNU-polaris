#include "UfsFat.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
struct Disk {
    FILE *file;unsigned reads=0,failAt=~0u;
    static bool read(void *context,uint32_t block,uint8_t *out) {
        auto &d=*static_cast<Disk *>(context);assert(block<UFS_ESP_BLOCKS);assert(++d.reads<=128);
        if(d.reads==d.failAt)return false;
        assert(!std::fseek(d.file,long(block)*4096,SEEK_SET));bool ok=std::fread(out,1,4096,d.file)==4096;
        if(ok)std::printf("BLOCK %u %08x\n",block,polaris_fat_crc(out,4096));return ok;
    }
};
int main(int argc,char **argv) {
    assert(argc>=3);FILE *file=std::fopen(argv[1],"rb");assert(file);bool expected=std::atoi(argv[2])!=0;
    if(argc>=4 && !std::strcmp(argv[3],"gpt")) {
        PolarisUfsSector sectors[6];
        for(unsigned i=0;i<6;i++) {
            assert(std::fread(sectors[i].data,1,4096,file)==4096);auto &r=sectors[i].command;
            r.state=PolarisUfsScsiResult::Success;r.kind=PolarisUfsScsiKind::Read10;
            r.lba=i;r.bytes=4096;r.guards=r.stopped=true;
        }
        PolarisUfsScsiResult capacity;capacity.state=PolarisUfsScsiResult::Success;capacity.kind=PolarisUfsScsiKind::Capacity10;
        capacity.guards=capacity.stopped=true;capacity.blockBytes=4096;capacity.lastLba=499999;
        bool ok=polaris_ufs_esp_gpt(sectors,6,capacity);assert(ok==expected);std::fclose(file);return 0;
    }
    Disk d{file};if(argc>=4)d.failAt=unsigned(std::atoi(argv[3]));
    PolarisFatWork work;PolarisFatResult result;if(argc>=5)result.reads=128;
    PolarisFatReader reader({&d,Disk::read},work,result);bool ok=reader.run();assert(ok==expected);
    if(ok) {
        assert(result.state==PolarisFatResult::Success && result.fileBytes==8193 && result.clusterCount>1);
        for(unsigned i=0;i<8193;i++)assert(result.data[i]==uint8_t(i*37+(i>>8)*11+0x51));
        for(unsigned i=0;i<result.clusterCount;i++)std::printf("CHAIN %u\n",result.chain[i]);
    } else assert(result.state!=PolarisFatResult::Success);
    std::printf("state=%u reads=%u clusters=%u\n",unsigned(result.state),result.reads,result.clusterCount);
    std::fclose(file);
}
