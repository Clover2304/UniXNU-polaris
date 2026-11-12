// Separate CPU/device memory validates the actual bounded READ(10) transport.
#include "UfsRead.h"
#include <cassert>
#include <cstring>
#include <map>
#include <vector>
struct ReadPeer {
    enum Mode { Good, Timeout, WrongType, CheckCondition, Residual, NoInvalidate,
        DataMissing, PartialData, Overrun, CorruptPrdt, NoStop, NoStart, Changed, SenseTooLong, ReadFailure } mode=Good;
    uint64_t pa=0x102000000ULL;
    PolarisUfsNopResult owned;
    PolarisUfsDescriptor unit;
    PolarisUfsScsiResult capacity;
    alignas(4096) uint8_t cpu[UFS_NOP_BYTES]{},device[UFS_NOP_BYTES]{};
    std::map<unsigned,uint32_t> regs{{0x50,0x02000000},{0x54,1},{0x60,0},{0x58,0},
        {0x78,0},{0x80,0},{0x24,0},{0x34,1},{0x30,0x10f},{0x20,0x401}};
    unsigned polls=0,submissions=0,clean=0,invalidate=0,delays=0;
    bool complete=true;
    std::vector<std::pair<unsigned,uint32_t>> writes;
    ReadPeer() {
        owned.state=PolarisUfsNopResult::Success;owned.guards=owned.responseRead=owned.smmuUnchanged=owned.stopped=true;
        owned.dma=owned.finalBase=pa;
        unit.state=PolarisUfsDescriptor::Success;unit.idn=2;unit.length=35;
        unit.data[0]=35;unit.data[1]=2;unit.data[3]=1;unit.data[10]=12;unit.data[18]=100;
        capacity.state=PolarisUfsScsiResult::Success;capacity.kind=PolarisUfsScsiKind::Capacity10;
        capacity.guards=capacity.stopped=true;capacity.blockBytes=4096;capacity.lastLba=99;capacity.capacityBytes=409600;
    }
    static uint8_t expected(unsigned lba,unsigned at) {return static_cast<uint8_t>(lba*37u+at*13u+(at>>8));}
    void reply() {
        auto *p=device+UFS_NOP_RSP;std::memset(p,0,34);p[0]=mode==WrongType?0x36:0x21;
        if(mode==Residual)p[15]=1;
        if(mode==CheckCondition){p[7]=2;p[11]=20;p[33]=18;std::memset(p+34,0,18);p[34]=0x70;p[36]=6;p[46]=0x29;}
        if(mode==SenseTooLong){p[7]=2;p[11]=20;p[32]=p[33]=0xff;}
        unsigned lba=(unsigned(device[UFS_NOP_UCD+18])<<24)|(unsigned(device[UFS_NOP_UCD+19])<<16)|(unsigned(device[UFS_NOP_UCD+20])<<8)|device[UFS_NOP_UCD+21];
        if(mode!=DataMissing)for(unsigned i=0;i<(mode==PartialData?4095u:4096u);i++)device[UFS_SCSI_DATA+i]=expected(lba,i);
        if(mode==Overrun)device[UFS_SCSI_DATA+4096]^=1;
        if(mode==CorruptPrdt)device[UFS_SCSI_PRDT+12]^=1;
        if(mode==Changed)regs[0x50]+=4096;
        device[8]=0;regs[0x58]=0;complete=true;
    }
    static bool read(void *context,unsigned off,uint32_t *value) {
        auto &f=*static_cast<ReadPeer *>(context);assert(f.regs.count(off));
        if(f.mode==ReadFailure && off==0x34)return false;
        if(off==0x58 && !f.complete){f.polls++;if(f.mode!=Timeout && f.polls>=2)f.reply();}
        *value=f.regs.at(off);return true;
    }
    static void write(void *context,unsigned off,uint32_t value) {
        auto &f=*static_cast<ReadPeer *>(context);assert(off==0x60 || off==0x58);f.writes.push_back({off,value});
        if(off==0x60 && ((value==1 && f.mode==NoStart)||(value==0 && f.mode==NoStop)))return;
        if(off==0x58) {
            assert(value==1 && f.regs[0x60]==1 && f.clean==f.submissions+1);
            const uint8_t utrd[]={0,0,0,0x15,0,0,0,0,15,0,0,0,0,0,0,0,0,0x10,0,2,1,0,0,0,0x80,0,0x80,0,1,0,0,1};
            assert(!std::memcmp(utrd,f.device,32));
            uint8_t request[]={1,0x40,0,0,0,0,0,0,0,0,0,0,0,0,0x10,0,0x28,0,0,0,0,0,0,0,1,0,0,0,0,0,0,0};
            unsigned lba=(unsigned(f.device[UFS_NOP_UCD+18])<<24)|(unsigned(f.device[UFS_NOP_UCD+19])<<16)|(unsigned(f.device[UFS_NOP_UCD+20])<<8)|f.device[UFS_NOP_UCD+21];
            assert(lba<6 || (lba>=393216 && lba<425984));
            request[18]=uint8_t(lba>>24);request[19]=uint8_t(lba>>16);request[20]=uint8_t(lba>>8);request[21]=uint8_t(lba);
            assert(!std::memcmp(request,f.device+UFS_NOP_UCD,32));
            const uint8_t prdt[]={0,0x20,0,2,1,0,0,0,0,0,0,0,0xff,0x0f,0,0};
            assert(!std::memcmp(prdt,f.device+UFS_SCSI_PRDT,16));
            for(unsigned i=0;i<4096;i++)assert(f.device[UFS_SCSI_DATA+i]==0xd3);
            f.submissions++;f.polls=0;f.complete=false;
        }
        f.regs[off]=value;
    }
    static void delay(void *context,unsigned us) {
        auto &f=*static_cast<ReadPeer *>(context);assert(us==100);f.delays+=us;assert(f.delays<=700000);
    }
    static void cache(void *context,unsigned op) {
        auto &f=*static_cast<ReadPeer *>(context);
        if(op==1){f.clean++;std::memcpy(f.device,f.cpu,sizeof(f.cpu));}
        else {assert(op==2 && f.complete);f.invalidate++;if(f.mode!=NoInvalidate)std::memcpy(f.cpu,f.device,sizeof(f.cpu));}
    }
    void run(PolarisUfsSector &sector,unsigned lba) {
        polaris_ufs_read_sector(sector,{this,read,write,delay,cache},owned,unit,capacity,cpu,pa,lba);
    }
};
int main() {
    ReadPeer good;
    for(unsigned lba=0;lba<6;lba++) {
        PolarisUfsSector sector;good.run(sector,lba);const auto &r=sector.command;
        assert(r.state==PolarisUfsScsiResult::Success && r.lba==lba && r.bytes==4096 && r.guards && r.stopped);
        assert(r.writes==3 && r.doorbells==1 && r.polls==2 && r.finalRun==0 && r.finalDb==0);
        for(unsigned at=0;at<4096;at++)assert(sector.data[at]==ReadPeer::expected(lba,at));
    }
    assert(good.submissions==6 && good.writes.size()==18 && good.clean==6 && good.invalidate==6);
    for(unsigned lba:{393215u,393216u,425983u,425984u}) {
        ReadPeer f;f.unit.data[16]=7;f.unit.data[17]=0xa1;f.unit.data[18]=0x20;
        auto r=polaris_ufs_scsi_read({&f,ReadPeer::read,ReadPeer::write,ReadPeer::delay,ReadPeer::cache},
            f.owned,f.unit,f.cpu,f.pa,PolarisUfsScsiKind::EspRead10,true,lba);
        if(lba==393216 || lba==425983) {
            assert(r.state==PolarisUfsScsiResult::Success && f.submissions==1 && r.bytes==4096);
            for(unsigned at=0;at<4096;at++)assert(f.cpu[UFS_SCSI_DATA+at]==ReadPeer::expected(lba,at));
        } else assert(r.state==PolarisUfsScsiResult::InvalidRequest && f.writes.empty());
    }
    for(unsigned mode=0;mode<8;mode++) {
        ReadPeer f;PolarisUfsSector sector;
        if(mode==0)f.capacity.guards=false;if(mode==1)f.capacity.kind=PolarisUfsScsiKind::Inquiry;
        if(mode==2)f.capacity.blockBytes=512;if(mode==3)f.capacity.lastLba--;
        if(mode==4)f.owned.smmuUnchanged=false;if(mode==5)f.unit.data[10]=9;
        f.run(sector,mode==6?6:mode==7?0xffffffffu:0);
        assert(sector.command.state!=PolarisUfsScsiResult::Success && f.writes.empty() && f.clean==0);
    }
    for(unsigned mode=ReadPeer::Timeout;mode<=ReadPeer::ReadFailure;mode++) {
        ReadPeer f;f.mode=static_cast<ReadPeer::Mode>(mode);PolarisUfsSector sector;f.run(sector,3);
        const auto &r=sector.command;
        if(mode==ReadPeer::DataMissing || mode==ReadPeer::PartialData) {
            // GOOD alone cannot prove payload freshness; independent byte-for-
            // byte host reference qualification must reject these buffers.
            assert(r.state==PolarisUfsScsiResult::Success);
            assert(sector.data[4095]!=ReadPeer::expected(3,4095));
        } else assert(r.state!=PolarisUfsScsiResult::Success);
        assert(f.submissions<=1);
        assert(r.senseLength<=sizeof(r.sense));
        if(mode==ReadPeer::Timeout)assert(r.polls==5000 && r.finalDb==1 && r.stopped);
        if(mode==ReadPeer::CheckCondition)assert(r.senseLength==18 && r.sense[2]==6);
        if(mode==ReadPeer::NoStart || mode==ReadPeer::ReadFailure)assert(f.submissions==0);
    }
}
