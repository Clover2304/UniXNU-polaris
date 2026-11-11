// SPDX-License-Identifier: BSD-2-Clause-Patent
// Read-only firmware queue and candidate stream inventory. See SOURCES.md.
#pragma once
#include "UfsProbe.h"

struct PolarisUfsSmmuIo {
    void *context;
    bool (*read)(void *,unsigned offset,uint32_t *value);
};
struct PolarisUfsMatch {
    unsigned slot=0,bank=255,shared=0;
    uint32_t smr=0,s2cr=0,coverage=0,sctlr=0,cbar=0,cba2r=0;
};
struct PolarisUfsInventory {
    enum State { Disabled, ControllerNotReady, ReadFailed, GateNotReady,
                 Unsupported, TooManyMatches, GateChanged, Snapshot } state=Disabled;
    uint32_t queues[2][12]{};
    uint32_t idr[3]{},cr0=0;
    unsigned groups=0,scanned=0,validGroups=0,pages=0,banks=0,matches=0;
    unsigned gccReads=0,controllerReads=0,smmuReads=0;
    bool truncated=false,stable=true,overlap=false,queuesStable=true;
    uint32_t coverage=0;
    PolarisUfsMatch match[16]{};
};
static inline const char *polaris_ufs_inventory_state(PolarisUfsInventory::State state) {
    switch(state) {
        case PolarisUfsInventory::Disabled:return "disabled";
        case PolarisUfsInventory::ControllerNotReady:return "controller-not-ready";
        case PolarisUfsInventory::ReadFailed:return "read-failed";
        case PolarisUfsInventory::GateNotReady:return "gate-not-ready";
        case PolarisUfsInventory::Unsupported:return "unsupported-geometry";
        case PolarisUfsInventory::TooManyMatches:return "too-many-matches";
        case PolarisUfsInventory::GateChanged:return "gate-changed";
        case PolarisUfsInventory::Snapshot:return "snapshot";
    }
    return "unknown";
}
static inline uint32_t polaris_ufs_sid_coverage(uint32_t smr) {
    if(!(smr&0x80000000u))return 0;
    unsigned mask=(smr>>16)&0x7fffu;uint32_t bits=0;
    for(unsigned i=0;i<16;i++)if(!((smr^(0x100u+i))&0xffffu&~mask))bits|=1u<<i;
    return bits;
}
static inline PolarisUfsInventory polaris_ufs_inventory(const PolarisUfsIo &ufs,
    const PolarisUfsSmmuIo &smmu,bool enabled,bool controllerReady) {
    PolarisUfsInventory r;
    if(!enabled)return r;
    if(!controllerReady){r.state=PolarisUfsInventory::ControllerNotReady;return r;}
    r.state=PolarisUfsInventory::ReadFailed;
    constexpr unsigned gates[]={0x77000,0x77004,0x77008,0x7700c,0x77010,0x82024};
    constexpr unsigned queueOffsets[]={0x20,0x24,0x50,0x54,0x58,0x60,0x70,0x74,0x78,0x80,0x30,0x34};
    uint32_t gate[6]{};
    for(unsigned i=0;i<6;i++) {
        if(!ufs.read(ufs.context,false,gates[i],&gate[i]))return r;
        r.gccReads++;
    }
    if(polaris_ufs_gate(gate)!=PolarisUfsSnapshot::Ready){r.state=PolarisUfsInventory::GateNotReady;return r;}
    auto queues=[&](unsigned sample) {
        for(unsigned i=0;i<12;i++) {
            if(!ufs.read(ufs.context,true,queueOffsets[i],&r.queues[sample][i]))return false;
            r.controllerReads++;
        }
        return true;
    };
    auto read=[&](unsigned offset,uint32_t &v) {
        if(!smmu.read(smmu.context,offset,&v))return false;
        r.smmuReads++;return true;
    };
    if(!queues(0) || !read(0,r.cr0))return r;
    for(unsigned i=0;i<3;i++)if(!read(0x20+4*i,r.idr[i]))return r;
    r.groups=r.idr[0]&255u;r.banks=r.idr[1]&255u;
    r.pages=1u<<(((r.idr[1]>>28)&7u)+1u);
    // Only the known 4 KiB, non-extended stream matching layout. Qualcomm
    // firmware may not emulate extended groups reliably: never touch >=128.
    if(!(r.idr[0]&(1u<<27)) || (r.cr0&(1u<<3)) || !r.groups || !r.banks ||
       r.banks>128 || r.pages>128 || (r.idr[1]>>31) ||
       (r.pages+r.banks)*4096u>0x100000u) {
        r.state=PolarisUfsInventory::Unsupported;return r;
    }
    r.scanned=r.groups>128?128:r.groups;r.truncated=r.groups>r.scanned;
    uint32_t smr[128]{},s2cr[128]{};
    for(unsigned i=0;i<r.scanned;i++) {
        if(!read(0x800+4*i,smr[i]))return r;
        if(!(smr[i]&0x80000000u))continue;
        r.validGroups++;
        if(!read(0xc00+4*i,s2cr[i]))return r;
        uint32_t coverage=polaris_ufs_sid_coverage(smr[i]);
        if(!coverage)continue;
        if(r.matches==16){r.state=PolarisUfsInventory::TooManyMatches;return r;}
        auto &m=r.match[r.matches++];m.slot=i;m.smr=smr[i];m.s2cr=s2cr[i];m.coverage=coverage;
        r.overlap|=(r.coverage&coverage)!=0;r.coverage|=coverage;
    }
    for(unsigned i=0;i<r.matches;i++) {
        auto &m=r.match[i];
        if(((m.s2cr>>16)&3u)!=0)continue;
        m.bank=m.s2cr&255u;
        if(m.bank>=r.banks){r.state=PolarisUfsInventory::Unsupported;return r;}
        for(unsigned j=0;j<r.scanned;j++)
            if(j!=m.slot && (smr[j]&0x80000000u) && ((s2cr[j]>>16)&3u)==0 &&
               (s2cr[j]&255u)==m.bank)m.shared++;
        if(!read((r.pages+m.bank)*4096u,m.sctlr) ||
           !read(0x1000+4*m.bank,m.cbar) || !read(0x1800+4*m.bank,m.cba2r))return r;
    }
    // A second bounded sample detects visible changes. No queue memory is
    // mapped, no pointer followed, and no fault/interrupt/doorbell is cleared.
    if(!queues(1))return r;
    for(unsigned i=0;i<12;i++)if(r.queues[0][i]!=r.queues[1][i])r.queuesStable=false;
    uint32_t value=0;
    if(!read(0,value))return r;
    r.stable&=value==r.cr0;
    for(unsigned i=0;i<3;i++){if(!read(0x20+4*i,value))return r;r.stable&=value==r.idr[i];}
    for(unsigned i=0;i<r.scanned;i++) {
        if(!read(0x800+4*i,value))return r;
        r.stable&=value==smr[i];
        // Read only entries valid in the first sample; newly valid entries
        // invalidate stability without expanding the access set mid-snapshot.
        if(!(smr[i]&0x80000000u))continue;
        if(!read(0xc00+4*i,value))return r;
        r.stable&=value==s2cr[i];
    }
    for(unsigned i=0;i<r.matches;i++) {
        auto &m=r.match[i];if(m.bank==255)continue;
        const unsigned offsets[]={(r.pages+m.bank)*4096u,0x1000+4*m.bank,0x1800+4*m.bank};
        const uint32_t expected[]={m.sctlr,m.cbar,m.cba2r};
        for(unsigned j=0;j<3;j++){if(!read(offsets[j],value))return r;r.stable&=value==expected[j];}
    }
    for(unsigned i=0;i<6;i++) {
        if(!ufs.read(ufs.context,false,gates[i],&value))return r;
        r.gccReads++;
        if(value!=gate[i]){r.state=PolarisUfsInventory::GateChanged;return r;}
    }
    r.state=PolarisUfsInventory::Snapshot;return r;
}
