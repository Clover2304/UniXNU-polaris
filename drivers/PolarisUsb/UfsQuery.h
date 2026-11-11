// SPDX-License-Identifier: BSD-2-Clause-Patent
// Bounded READ_DESCRIPTOR only, on the stopped queue installed by UfsNop.h.
#pragma once
#include "UfsNop.h"
struct PolarisUfsDescriptor {
    enum State { Disabled, Preflight, InvalidRequest, ReadFailed, StateChanged,
        StartTimeout, Timeout, OcsError, ResponseError, GuardError, CleanupFailed, Success } state=Disabled;
    unsigned idn=0,requested=255,length=0,osfLength=0,polls=0,writes=0,doorbells=0,ocs=15,response=255;
    bool guards=false,stopped=false;
    uint32_t isAfter=0,finalDb=0,finalRun=0;
    uint8_t upiu[32]{},data[255]{};
};
static inline const char *polaris_ufs_descriptor_state(PolarisUfsDescriptor::State s) {
    switch(s) {
        case PolarisUfsDescriptor::Disabled:return "disabled";
        case PolarisUfsDescriptor::Preflight:return "preflight-failed";
        case PolarisUfsDescriptor::InvalidRequest:return "invalid-request";
        case PolarisUfsDescriptor::ReadFailed:return "read-failed";
        case PolarisUfsDescriptor::StateChanged:return "state-changed";
        case PolarisUfsDescriptor::StartTimeout:return "start-timeout";
        case PolarisUfsDescriptor::Timeout:return "timeout";
        case PolarisUfsDescriptor::OcsError:return "ocs-error";
        case PolarisUfsDescriptor::ResponseError:return "response-error";
        case PolarisUfsDescriptor::GuardError:return "guard-error";
        case PolarisUfsDescriptor::CleanupFailed:return "cleanup-failed";
        case PolarisUfsDescriptor::Success:return "ok";
    }
    return "unknown";
}
static inline void polaris_ufs_query_request(uint8_t *p,unsigned idn) {
    for(unsigned i=0;i<32;i++)p[i]=0;
    p[0]=0x16;p[5]=1; // QUERY REQUEST, STANDARD READ REQUEST; slot/tag/LUN 0
    p[12]=1;p[13]=static_cast<uint8_t>(idn);p[19]=255; // READ_DESCRIPTOR, index/selector 0
}
static inline unsigned polaris_ufs_be16(const uint8_t *p){return (unsigned(p[0])<<8)|p[1];}
static inline uint64_t polaris_ufs_be64(const uint8_t *p) {
    uint64_t v=0;for(unsigned i=0;i<8;i++)v=(v<<8)|p[i];return v;
}
static inline bool polaris_ufs_query_guards(const uint8_t *p,uint64_t pa,unsigned idn) {
    uint8_t descriptor[32],request[32];polaris_ufs_nop_descriptor(descriptor,pa);polaris_ufs_query_request(request,idn);
    for(unsigned i=0;i<UFS_NOP_BYTES;i++) {
        if(i==8 || (i>=UFS_NOP_RSP && i<UFS_NOP_RSP+512))continue;
        uint8_t expected=i<32?descriptor[i]:i<1024?0:
            (i>=UFS_NOP_UCD && i<UFS_NOP_UCD+32)?request[i-UFS_NOP_UCD]:
            (i>=UFS_NOP_UCD && i<UFS_NOP_UCD+512)?0:0xa5;
        if(p[i]!=expected)return false;
    }
    return true;
}
static inline bool polaris_ufs_query_parse(PolarisUfsDescriptor &r,const uint8_t *p) {
    if(r.idn!=0 && r.idn!=2)return false;
    for(unsigned i=0;i<32;i++)r.upiu[i]=p[i];
    r.response=p[6];r.length=polaris_ufs_be16(p+10);r.osfLength=polaris_ufs_be16(p+18);
    if(p[0]!=0x36 || (p[1]&0x60) || p[2] || p[3] || p[6] || p[7] || p[8] ||
       p[12]!=1 || p[13]!=r.idn || p[14] || p[15] || r.length>255 ||
       r.osfLength<r.length || r.osfLength>255 || r.length<(r.idn==0?26u:19u))return false;
    // The complete descriptor must fit the advertised data segment. No strings,
    // RPMB, writes, attributes or flags can be requested through this interface.
    const uint8_t *d=p+32;
    if(d[0]!=r.length || d[1]!=r.idn || (r.idn==2 && d[2]!=0))return false;
    if(r.idn==0 && (!d[6] || d[6]>32))return false;
    if(r.idn==2) {
        if(d[3]>1)return false;
        if(d[3] && (d[10]<9 || d[10]>16 || !polaris_ufs_be64(d+11)))return false;
        if(d[3] && polaris_ufs_be64(d+11)>(UINT64_MAX>>d[10]))return false;
    }
    for(unsigned i=0;i<r.length;i++)r.data[i]=d[i];
    return true;
}
static inline PolarisUfsDescriptor polaris_ufs_read_descriptor(const PolarisUfsNopIo &io,
    const PolarisUfsNopResult &owned,uint8_t *buffer,uint64_t pa,unsigned idn,bool enabled) {
    PolarisUfsDescriptor r;r.idn=idn;if(!enabled)return r;
    r.state=PolarisUfsDescriptor::InvalidRequest;if(idn!=0 && idn!=2)return r;
    r.state=PolarisUfsDescriptor::Preflight;
    if(owned.state!=PolarisUfsNopResult::Success || !owned.guards || !owned.responseRead ||
       !owned.smmuUnchanged || !owned.stopped || owned.finalRun || owned.finalDb ||
       owned.finalBase!=pa || owned.dma!=pa || !buffer || (pa&4095u) ||
       pa<0x100000000ULL || pa>0x140000000ULL-UFS_NOP_BYTES)return r;
    auto read=[&](unsigned off,uint32_t &v){return io.read(io.context,off,&v);};
    auto write=[&](unsigned off,uint32_t v){r.writes++;io.write(io.context,off,v);};
    auto wait=[&](unsigned off,uint32_t expected) {
        for(unsigned i=0;i<1000;i++){uint32_t v=0;if(!read(off,v))return false;if(v==expected)return true;io.delay(io.context,100);}
        return false;
    };
    uint32_t lo=0,hi=0,run=0,db=0,tmdb=0,tmrun=0,ie=0,hce=0,hcs=0,is=0;
    r.state=PolarisUfsDescriptor::ReadFailed;
    if(!read(0x50,lo)||!read(0x54,hi)||!read(0x60,run)||!read(0x58,db)||!read(0x78,tmdb)||
       !read(0x80,tmrun)||!read(0x24,ie)||!read(0x34,hce)||!read(0x30,hcs)||!read(0x20,is))return r;
    if(((uint64_t(hi)<<32)|lo)!=pa || run || db || tmdb || tmrun || ie || hce!=1 ||
       (hcs&0xbu)!=0xbu || (is&0x71884u)){r.state=PolarisUfsDescriptor::StateChanged;return r;}
    polaris_ufs_nop_prepare(buffer,pa);polaris_ufs_query_request(buffer+UFS_NOP_UCD,idn);io.cache(io.context,1);
    write(0x60,1);
    auto transfer=[&]() {
        if(!wait(0x60,1)){r.state=PolarisUfsDescriptor::StartTimeout;return;}
        write(0x58,1);r.doorbells++;
        bool done=false;
        for(unsigned i=0;i<5000;i++) {
            uint32_t v=0;r.polls++;
            if(!read(0x58,v)){r.state=PolarisUfsDescriptor::ReadFailed;return;}
            if(v==0){done=true;break;}
            if(v!=1){r.state=PolarisUfsDescriptor::StateChanged;return;}
            io.delay(io.context,100);
        }
        if(!done){r.state=PolarisUfsDescriptor::Timeout;return;}
        io.cache(io.context,2);r.ocs=buffer[8]&15u;
        if(r.ocs){r.state=PolarisUfsDescriptor::OcsError;return;}
        r.guards=polaris_ufs_query_guards(buffer,pa,idn);
        if(!polaris_ufs_query_parse(r,buffer+UFS_NOP_RSP)){r.state=PolarisUfsDescriptor::ResponseError;return;}
        r.state=r.guards?PolarisUfsDescriptor::Success:PolarisUfsDescriptor::GuardError;
    };
    transfer();write(0x60,0);r.stopped=wait(0x60,0);
    if(!read(0x60,r.finalRun)||!read(0x58,r.finalDb)||!read(0x20,r.isAfter)||!read(0x24,ie)||
       !read(0x34,hce)||!read(0x30,hcs)||!read(0x50,lo)||!read(0x54,hi)){r.state=PolarisUfsDescriptor::ReadFailed;return r;}
    if(!r.stopped || (r.state==PolarisUfsDescriptor::Success &&
       (r.finalRun || r.finalDb || ie || hce!=1 || (hcs&0xbu)!=0xbu || (r.isAfter&0x71884u) ||
        ((uint64_t(hi)<<32)|lo)!=pa)))r.state=PolarisUfsDescriptor::CleanupFailed;
    return r;
}
