// SPDX-License-Identifier: BSD-2-Clause-Patent
// Firmware-owned USB stream only. Register sequence adapted from the BSD
// UniXNU-j716f SmmuBypass.h; Qualcomm bypass semantics also documented in
// Linux arm-smmu-qcom.c. Keep S2CR.TYPE=TRANSLATE and disable stage 1 in the
// USB context instead of writing TYPE=BYPASS (which QHEE can turn into FAULT).
#pragma once
#include <stdint.h>
struct PolarisSmmuIo {
    void *context;
    uint32_t (*read)(void *,unsigned);
    void (*write)(void *,unsigned,uint32_t);
};
struct PolarisSmmuResult {
    unsigned error=0,bank=0;
    uint32_t smr=0,s2cr=0,before=0,after=0,cbar=0,cba2r=0,idr0=0,idr1=0,idr2=0;
};
static inline unsigned polaris_smmu_bits(unsigned code) {
    const unsigned widths[]={32,36,40,42,44,48};return code<6?widths[code]:0;
}
static inline PolarisSmmuResult polaris_usb_smmu(const PolarisSmmuIo &io,uint64_t dma,uint64_t length) {
    PolarisSmmuResult r;
    auto read=[&](unsigned off){return io.read(io.context,off);};
    auto write=[&](unsigned off,uint32_t value){io.write(io.context,off,value);};
    r.idr0=read(0x20);r.idr1=read(0x24);r.idr2=read(0x28);
    unsigned count=r.idr0&255u;if(count>128)count=128;
    unsigned banks=r.idr1&255u,pages=1u<<(((r.idr1>>28)&7u)+1);
    if(!count || !banks || banks>128 || pages>128 || (r.idr1>>31) || (pages+banks)*4096u>0x100000u) {r.error=1;return r;}
    for(unsigned shift=0;shift<=8;shift+=4) {
        unsigned bits=polaris_smmu_bits((r.idr2>>shift)&15);
        if(!bits || !length || dma>=(1ULL<<bits) || length>(1ULL<<bits)-dma){r.error=2;return r;}
    }
    unsigned matches=0,slot=0;
    for(unsigned i=0;i<count;i++) {
        uint32_t smr=read(0x800+4*i);if(!(smr&0x80000000u))continue;
        unsigned mask=(smr>>16)&0x7fffu;
        if(((smr^0x740u)&0xffffu&~mask)!=0)continue;
        if(mask || ++matches>1){r.error=3;return r;}
        slot=i;r.smr=smr;r.s2cr=read(0xc00+4*i);
    }
    if(!matches) {r.error=(read(0)&(1u<<10))?4:0;return r;}
    unsigned type=(r.s2cr>>16)&3u;
    if(type==1)return r; // Already a hardware bypass; leave it untouched.
    if(type!=0){r.error=5;return r;}
    r.bank=r.s2cr&255u;
    if(r.bank>=banks){r.error=6;return r;}
    unsigned cb=(pages+r.bank)*4096u,cbar=0x1000+4*r.bank,cba2r=0x1800+4*r.bank;
    r.before=read(cb);uint32_t oldCbar=read(cbar),oldCba2r=read(cba2r);
    bool ready=r.before==0 && oldCbar==0x10000u && oldCba2r==1;
    if(!ready) {
        for(unsigned i=0;i<count;i++) {
            if(i==slot || !(read(0x800+4*i)&0x80000000u))continue;
            uint32_t s2cr=read(0xc00+4*i);
            if(((s2cr>>16)&3u)==0 && (s2cr&255u)==r.bank){r.error=7;return r;}
        }
        if(!(r.idr0&(1u<<30)) || !(r.idr2&(1u<<12))){r.error=8;return r;}
        write(cb,0);write(cba2r,1);write(cbar,0x10000u);
    }
    r.after=read(cb);r.cbar=read(cbar);r.cba2r=read(cba2r);
    if(r.after!=0 || r.cbar!=0x10000u || r.cba2r!=1 || read(0x800+4*slot)!=r.smr || read(0xc00+4*slot)!=r.s2cr) {
        if(!ready){write(cbar,oldCbar);write(cba2r,oldCba2r);write(cb,r.before);}
        r.error=9;
    }
    return r;
}
