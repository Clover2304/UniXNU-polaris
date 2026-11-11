// SPDX-License-Identifier: BSD-2-Clause-Patent
// One bounded NOP OUT using an owned UTRL/UCD. No SMMU or media writes.
#pragma once
#include "UfsInventory.h"

constexpr unsigned UFS_NOP_BYTES=0x4000, UFS_NOP_UCD=0x1000, UFS_NOP_RSP=0x1200;
// FSR[10:9] is FORMAT, not a fault. Linux ARM_SMMU_CB_FSR_FAULT:
// MULTI/SS (31:30), UUT/ASF/TLBLKF/TLBMCF/EF/PF/AFF/TF (8:1).
constexpr uint32_t UFS_NOP_CONTEXT_FAULT_MASK=0xc00001feu;
static inline bool polaris_ufs_nop_fault(uint32_t global,uint32_t context) {
    return global || (context&UFS_NOP_CONTEXT_FAULT_MASK);
}
struct PolarisUfsNopIo {
    void *context;
    bool (*read)(void *,unsigned,uint32_t *);
    void (*write)(void *,unsigned,uint32_t);
    void (*delay)(void *,unsigned);
    void (*cache)(void *,unsigned); // 1=clean+invalidate before DMA; 2=invalidate after completion
};
struct PolarisUfsNopResult {
    enum State { Disabled, AllocationFailed, PreflightFailed, DmaInvalid, ReadFailed,
                 StateChanged, StopTimeout, BaseReadback, StartTimeout, CommandTimeout,
                 CommandError, ResponseError, GuardError, CleanupFailed, SmmuChanged, Success } state=Disabled;
    uint64_t dma=0,oldBase=0,finalBase=0;
    unsigned reads=0,writes=0,doorbells=0,polls=0,ocs=15;
    uint32_t rsp[3]{},isBefore=0,isAfter=0,finalRun=0,finalDb=0,finalIe=0,finalHcs=0,finalHce=0;
    bool cachePrepared=false,responseRead=false,guards=false,stopped=false,smmuUnchanged=false;
};
static inline const char *polaris_ufs_nop_state(PolarisUfsNopResult::State s) {
    switch(s) {
        case PolarisUfsNopResult::Disabled:return "disabled";
        case PolarisUfsNopResult::AllocationFailed:return "allocation-failed";
        case PolarisUfsNopResult::PreflightFailed:return "preflight-failed";
        case PolarisUfsNopResult::DmaInvalid:return "dma-invalid";
        case PolarisUfsNopResult::ReadFailed:return "read-failed";
        case PolarisUfsNopResult::StateChanged:return "state-changed";
        case PolarisUfsNopResult::StopTimeout:return "stop-timeout";
        case PolarisUfsNopResult::BaseReadback:return "base-readback";
        case PolarisUfsNopResult::StartTimeout:return "start-timeout";
        case PolarisUfsNopResult::CommandTimeout:return "command-timeout";
        case PolarisUfsNopResult::CommandError:return "command-error";
        case PolarisUfsNopResult::ResponseError:return "response-error";
        case PolarisUfsNopResult::GuardError:return "guard-error";
        case PolarisUfsNopResult::CleanupFailed:return "cleanup-failed";
        case PolarisUfsNopResult::SmmuChanged:return "smmu-changed";
        case PolarisUfsNopResult::Success:return "ok";
    }
    return "unknown";
}
static inline void polaris_ufs_le32(uint8_t *p,uint32_t v) {
    for(unsigned i=0;i<4;i++)p[i]=static_cast<uint8_t>(v>>(i*8));
}
static inline uint32_t polaris_ufs_be32(const uint8_t *p) {
    return (uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3];
}
static inline void polaris_ufs_nop_descriptor(uint8_t *d,uint64_t pa) {
    for(unsigned i=0;i<32;i++)d[i]=0;
    polaris_ufs_le32(d,0x11000000); // CT=UFS_STORAGE, interrupt command, no data, crypto off
    d[8]=15; // device must replace INVALID OCS
    polaris_ufs_le32(d+16,static_cast<uint32_t>(pa+UFS_NOP_UCD));
    polaris_ufs_le32(d+20,static_cast<uint32_t>((pa+UFS_NOP_UCD)>>32));
    polaris_ufs_le32(d+24,0x00800080); // 512-byte response at UCD+512, doubleword units
    polaris_ufs_le32(d+28,0x01000000); // PRDT offset 1024/4, length zero
}
static inline void polaris_ufs_nop_prepare(uint8_t *p,uint64_t pa) {
    for(unsigned i=0;i<UFS_NOP_BYTES;i++)p[i]=0xa5;
    for(unsigned i=0;i<1024;i++)p[i]=0;
    for(unsigned i=UFS_NOP_UCD;i<UFS_NOP_UCD+512;i++)p[i]=0;
    for(unsigned i=UFS_NOP_RSP;i<UFS_NOP_RSP+512;i++)p[i]=0x5a;
    polaris_ufs_nop_descriptor(p,pa); // slot 0, NOP OUT transaction=0, LUN=0, task tag=0
}
static inline bool polaris_ufs_nop_guards(const uint8_t *p,uint64_t pa) {
    uint8_t descriptor[32];polaris_ufs_nop_descriptor(descriptor,pa);
    for(unsigned i=0;i<UFS_NOP_BYTES;i++) {
        if(i==8 || (i>=UFS_NOP_RSP && i<UFS_NOP_RSP+512))continue;
        uint8_t expected=i<32?descriptor[i]:i<1024?0:
            (i>=UFS_NOP_UCD && i<UFS_NOP_UCD+512)?0:0xa5;
        if(p[i]!=expected)return false;
    }
    return true;
}
static inline bool polaris_ufs_nop_preflight(const PolarisUfsSnapshot &base,const PolarisUfsInventory &v) {
    if(base.state!=PolarisUfsSnapshot::Ready || base.regs[1]!=0x210 || !(base.regs[0]&(1u<<24)) ||
       v.state!=PolarisUfsInventory::Snapshot || !v.stable || !v.queuesStable || v.truncated ||
       v.overlap || v.matches!=1 || v.coverage!=0xffff || (v.cr0&1u))return false;
    const auto &m=v.match[0];
    // This stage is restricted to the previously observed polaris bank-2 handoff.
    if(m.smr!=0x800f0100 || m.s2cr!=2 || m.bank!=2 || (m.sctlr&1u) ||
       ((m.cbar>>16)&3u)!=1 || !(m.cba2r&1u))return false;
    for(unsigned shift=0;shift<=8;shift+=4)if(((v.idr[2]>>shift)&15u)!=5)return false; // observed 48-bit widths
    const auto *q=v.queues[1];
    return q[1]==0 && q[4]==0 && q[8]==0 && q[5]==1 && q[9]==0 &&
        (q[10]&0xbu)==0xbu && q[11]==1 && !(q[0]&0x71885u);
}
static inline bool polaris_ufs_nop_same_smmu(const PolarisUfsInventory &a,const PolarisUfsInventory &b) {
    if(b.state!=PolarisUfsInventory::Snapshot || !b.stable || b.truncated || b.overlap ||
       a.cr0!=b.cr0 || a.groups!=b.groups || a.scanned!=b.scanned || a.validGroups!=b.validGroups ||
       a.pages!=b.pages || a.banks!=b.banks || a.matches!=b.matches || a.coverage!=b.coverage)return false;
    for(unsigned i=0;i<3;i++)if(a.idr[i]!=b.idr[i])return false;
    for(unsigned i=0;i<a.matches;i++) {
        const auto &x=a.match[i],&y=b.match[i];
        if(x.slot!=y.slot || x.smr!=y.smr || x.s2cr!=y.s2cr || x.bank!=y.bank || x.shared!=y.shared ||
           x.sctlr!=y.sctlr || x.cbar!=y.cbar || x.cba2r!=y.cba2r)return false;
    }
    return true;
}
static inline PolarisUfsNopResult polaris_ufs_nop(const PolarisUfsNopIo &io,
    const PolarisUfsSnapshot &base,const PolarisUfsInventory &inventory,
    uint8_t *buffer,uint64_t pa,uint64_t usbPa,bool enabled) {
    PolarisUfsNopResult r;if(!enabled)return r;
    r.state=PolarisUfsNopResult::PreflightFailed;
    if(!polaris_ufs_nop_preflight(base,inventory))return r;
    r.state=PolarisUfsNopResult::DmaInvalid;r.dma=pa;
    if(!buffer || (pa&4095u) || pa<0x100000000ULL || pa>0x140000000ULL-UFS_NOP_BYTES ||
       usbPa>UINT64_MAX-0x10000u || (pa<usbPa+0x10000u && usbPa<pa+UFS_NOP_BYTES))return r;
    auto read=[&](unsigned off,uint32_t &v){r.reads++;return io.read(io.context,off,&v);};
    auto write=[&](unsigned off,uint32_t v){r.writes++;io.write(io.context,off,v);};
    auto wait=[&](unsigned off,uint32_t value,unsigned limit) {
        for(unsigned i=0;i<limit;i++) {
            uint32_t v=0;if(!read(off,v))return false;
            if(v==value)return true;
            io.delay(io.context,100);
        }
        return false;
    };
    constexpr unsigned offsets[]={0x20,0x24,0x50,0x54,0x58,0x60,0x70,0x74,0x78,0x80,0x30,0x34};
    r.state=PolarisUfsNopResult::ReadFailed;
    for(unsigned i=0;i<12;i++) {
        uint32_t v=0;if(!read(offsets[i],v))return r;
        if(v!=inventory.queues[1][i]){r.state=PolarisUfsNopResult::StateChanged;return r;}
    }
    r.oldBase=(uint64_t(inventory.queues[1][3])<<32)|inventory.queues[1][2];
    r.isBefore=inventory.queues[1][0];
    polaris_ufs_nop_prepare(buffer,pa);io.cache(io.context,1);r.cachePrepared=true;
    write(0x60,0);
    if(!wait(0x60,0,1000)){r.state=PolarisUfsNopResult::StopTimeout;return r;}
    uint32_t db=0,tmdb=0,ie=0,hce=0;
    if(!read(0x58,db)||!read(0x78,tmdb)||!read(0x24,ie)||!read(0x34,hce)){r.state=PolarisUfsNopResult::ReadFailed;return r;}
    if(db||tmdb||ie||hce!=1){r.state=PolarisUfsNopResult::StateChanged;return r;}
    write(0x50,static_cast<uint32_t>(pa));write(0x54,static_cast<uint32_t>(pa>>32));
    auto command=[&]() {
        uint32_t lo=0,hi=0;
        if(!read(0x50,lo)||!read(0x54,hi)){r.state=PolarisUfsNopResult::ReadFailed;return;}
        if(((uint64_t(hi)<<32)|lo)!=pa){r.state=PolarisUfsNopResult::BaseReadback;return;}
        write(0x60,1);
        if(!wait(0x60,1,1000)){r.state=PolarisUfsNopResult::StartTimeout;return;}
        write(0x58,1);r.doorbells++;
        bool done=false;
        for(unsigned i=0;i<5000;i++) {
            uint32_t value=0;r.polls++;
            if(!read(0x58,value)){r.state=PolarisUfsNopResult::ReadFailed;return;}
            if(value==0){done=true;break;}
            if(value!=1){r.state=PolarisUfsNopResult::StateChanged;return;}
            io.delay(io.context,100);
        }
        if(!done){r.state=PolarisUfsNopResult::CommandTimeout;return;}
        io.cache(io.context,2);r.responseRead=true;
        r.ocs=buffer[8]&15u;
        for(unsigned i=0;i<3;i++)r.rsp[i]=polaris_ufs_be32(buffer+UFS_NOP_RSP+i*4);
        r.guards=polaris_ufs_nop_guards(buffer,pa);
        if(r.ocs){r.state=PolarisUfsNopResult::CommandError;return;}
        const auto *p=buffer+UFS_NOP_RSP;
        if(p[0]!=0x20 || (p[1]&0x60) || p[2] || p[3] || p[6] || p[7] || p[8] || p[10] || p[11]) {
            r.state=PolarisUfsNopResult::ResponseError;return;
        }
        r.state=r.guards?PolarisUfsNopResult::Success:PolarisUfsNopResult::GuardError;
    };
    command();
    // Never restore firmware addresses or free buffers: even a timed-out
    // transaction may still DMA. Leave our queue installed and request stop.
    write(0x60,0);
    r.stopped=wait(0x60,0,1000);
    uint32_t lo=0,hi=0;
    if(!read(0x50,lo)||!read(0x54,hi)||!read(0x60,r.finalRun)||!read(0x58,r.finalDb)||
       !read(0x24,r.finalIe)||!read(0x20,r.isAfter)||!read(0x30,r.finalHcs)||!read(0x34,r.finalHce)) {
        r.state=PolarisUfsNopResult::ReadFailed;return r;
    }
    r.finalBase=(uint64_t(hi)<<32)|lo;
    if(!r.stopped || (r.state==PolarisUfsNopResult::Success &&
       (r.finalRun || r.finalDb || r.finalIe || r.finalHce!=1 || r.finalBase!=pa ||
        (r.isAfter&0x71884u) || (r.finalHcs&0xbu)!=0xbu)))r.state=PolarisUfsNopResult::CleanupFailed;
    return r;
}
