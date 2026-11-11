// SPDX-License-Identifier: BSD-2-Clause-Patent
// Fixed read-only SCSI identity/capacity commands; no sector or media writes.
#pragma once
#include "UfsQuery.h"

constexpr unsigned UFS_SCSI_PRDT=0x1400, UFS_SCSI_DATA=0x2000;
enum class PolarisUfsScsiKind { Inquiry, Capacity10 };
struct PolarisUfsScsiResult {
    enum State { Disabled, InvalidRequest, Preflight, ReadFailed, StateChanged,
        StartTimeout, Timeout, OcsError, ResponseError, ScsiStatus, PayloadError,
        GuardError, CleanupFailed, Success } state=Disabled;
    PolarisUfsScsiKind kind=PolarisUfsScsiKind::Inquiry;
    unsigned bytes=0,polls=0,writes=0,doorbells=0,ocs=15,response=255,status=255,flags=0,segment=0,senseLength=0;
    uint32_t residual=0,isAfter=0,finalRun=0,finalDb=0,blockBytes=0;
    uint64_t dataPa=0,lastLba=0,capacityBytes=0;
    bool guards=false,stopped=false;
    uint8_t upiu[34]{},prdt[16]{},sense[18]{},data[36]{};
};
static inline const char *polaris_ufs_scsi_state(PolarisUfsScsiResult::State s) {
    switch(s) {
        case PolarisUfsScsiResult::Disabled:return "disabled";
        case PolarisUfsScsiResult::InvalidRequest:return "invalid-request";
        case PolarisUfsScsiResult::Preflight:return "preflight-failed";
        case PolarisUfsScsiResult::ReadFailed:return "read-failed";
        case PolarisUfsScsiResult::StateChanged:return "state-changed";
        case PolarisUfsScsiResult::StartTimeout:return "start-timeout";
        case PolarisUfsScsiResult::Timeout:return "timeout";
        case PolarisUfsScsiResult::OcsError:return "ocs-error";
        case PolarisUfsScsiResult::ResponseError:return "response-error";
        case PolarisUfsScsiResult::ScsiStatus:return "scsi-status";
        case PolarisUfsScsiResult::PayloadError:return "payload-error";
        case PolarisUfsScsiResult::GuardError:return "guard-error";
        case PolarisUfsScsiResult::CleanupFailed:return "cleanup-failed";
        case PolarisUfsScsiResult::Success:return "ok";
    }
    return "unknown";
}
static inline unsigned polaris_ufs_scsi_bytes(PolarisUfsScsiKind kind) {
    return kind==PolarisUfsScsiKind::Inquiry?36:kind==PolarisUfsScsiKind::Capacity10?8:0;
}
static inline void polaris_ufs_scsi_descriptor(uint8_t *d,uint64_t pa) {
    polaris_ufs_nop_descriptor(d,pa);
    polaris_ufs_le32(d,0x15000000); // CT=1, DD=DEVICE_TO_HOST, I=1, crypto off
    polaris_ufs_le32(d+28,0x01000001); // PRDT offset 1024/4, exactly one entry
}
static inline void polaris_ufs_scsi_request(uint8_t *p,PolarisUfsScsiKind kind) {
    for(unsigned i=0;i<32;i++)p[i]=0;
    p[0]=1;p[1]=0x40;p[15]=static_cast<uint8_t>(polaris_ufs_scsi_bytes(kind)); // COMMAND, READ, LUN/tag 0
    p[16]=kind==PolarisUfsScsiKind::Inquiry?0x12:0x25;
    if(kind==PolarisUfsScsiKind::Inquiry)p[20]=36; // EVPD=0, standard 36-byte INQUIRY only
}
static inline void polaris_ufs_scsi_prdt(uint8_t *p,uint64_t pa,unsigned bytes) {
    polaris_ufs_le32(p,static_cast<uint32_t>(pa+UFS_SCSI_DATA));
    polaris_ufs_le32(p+4,static_cast<uint32_t>((pa+UFS_SCSI_DATA)>>32));
    polaris_ufs_le32(p+8,0);polaris_ufs_le32(p+12,bytes-1); // DBC is zero based, DWORD granularity
}
static inline void polaris_ufs_scsi_prepare(uint8_t *p,uint64_t pa,PolarisUfsScsiKind kind) {
    polaris_ufs_nop_prepare(p,pa);
    polaris_ufs_scsi_descriptor(p,pa);polaris_ufs_scsi_request(p+UFS_NOP_UCD,kind);
    polaris_ufs_scsi_prdt(p+UFS_SCSI_PRDT,pa,polaris_ufs_scsi_bytes(kind));
    // A non-ASCII sentinel also detects missing DMA into a text-field tail.
    for(unsigned i=0;i<polaris_ufs_scsi_bytes(kind);i++)p[UFS_SCSI_DATA+i]=0xd3;
}
static inline bool polaris_ufs_scsi_guards(const uint8_t *p,uint64_t pa,PolarisUfsScsiKind kind) {
    uint8_t descriptor[32],request[32],prdt[16];unsigned bytes=polaris_ufs_scsi_bytes(kind);
    polaris_ufs_scsi_descriptor(descriptor,pa);polaris_ufs_scsi_request(request,kind);polaris_ufs_scsi_prdt(prdt,pa,bytes);
    for(unsigned i=0;i<UFS_NOP_BYTES;i++) {
        if(i==8 || (i>=UFS_NOP_RSP && i<UFS_NOP_RSP+512) || (i>=UFS_SCSI_DATA && i<UFS_SCSI_DATA+bytes))continue;
        uint8_t expected=i<32?descriptor[i]:i<1024?0:
            (i>=UFS_NOP_UCD && i<UFS_NOP_UCD+32)?request[i-UFS_NOP_UCD]:
            (i>=UFS_NOP_UCD && i<UFS_NOP_UCD+512)?0:
            (i>=UFS_SCSI_PRDT && i<UFS_SCSI_PRDT+16)?prdt[i-UFS_SCSI_PRDT]:0xa5;
        if(p[i]!=expected)return false;
    }
    return true;
}
static inline bool polaris_ufs_scsi_text(const uint8_t *data,unsigned length) {
    bool padded=false;
    for(unsigned i=0;i<length;i++) {
        uint8_t c=data[i];
        if(!c){padded=true;continue;}
        if(c<0x20 || c>0x7e || (padded && c!=0x20))return false;
    }
    return true;
}
static inline PolarisUfsScsiResult::State polaris_ufs_scsi_parse(PolarisUfsScsiResult &r,
    const uint8_t *buffer,const PolarisUfsDescriptor &unit) {
    if(!r.bytes || r.bytes!=polaris_ufs_scsi_bytes(r.kind))return PolarisUfsScsiResult::PayloadError;
    const auto *p=buffer+UFS_NOP_RSP;
    for(unsigned i=0;i<34;i++)r.upiu[i]=p[i];
    for(unsigned i=0;i<16;i++)r.prdt[i]=buffer[UFS_SCSI_PRDT+i];
    for(unsigned i=0;i<r.bytes;i++)r.data[i]=buffer[UFS_SCSI_DATA+i];
    r.flags=p[1];r.response=p[6];r.status=p[7];r.segment=polaris_ufs_be16(p+10);r.residual=polaris_ufs_be32(p+12);
    if(p[0]!=0x21 || p[2] || p[3] || p[6] || p[8] || r.segment>20 || r.segment==1)return PolarisUfsScsiResult::ResponseError;
    if(r.segment) {
        r.senseLength=polaris_ufs_be16(p+32);
        if(r.senseLength>18 || r.senseLength+2!=r.segment)return PolarisUfsScsiResult::ResponseError;
        for(unsigned i=0;i<r.senseLength;i++)r.sense[i]=p[34+i];
    }
    // Error status is diagnostic only. Never retry, issue REQUEST SENSE or
    // proceed to another command after a non-GOOD SCSI response.
    if(r.status)return PolarisUfsScsiResult::ScsiStatus;
    if((r.flags&0x60) || r.residual || r.senseLength)return PolarisUfsScsiResult::ResponseError;
    if(r.kind==PolarisUfsScsiKind::Inquiry) {
        if(r.data[0] || (r.data[3]&15u)!=2 || r.data[4]<31)return PolarisUfsScsiResult::PayloadError;
        if(!polaris_ufs_scsi_text(r.data+8,8) || !polaris_ufs_scsi_text(r.data+16,16) ||
           !polaris_ufs_scsi_text(r.data+32,4))return PolarisUfsScsiResult::PayloadError;
    } else {
        r.lastLba=polaris_ufs_be32(r.data);r.blockBytes=polaris_ufs_be32(r.data+4);
        if(unit.data[10]<9 || unit.data[10]>16 || r.lastLba==0xffffffffu || r.blockBytes!=(1u<<unit.data[10]) ||
           r.lastLba+1!=polaris_ufs_be64(unit.data+11))return PolarisUfsScsiResult::PayloadError;
        r.capacityBytes=(r.lastLba+1)*r.blockBytes;
    }
    return PolarisUfsScsiResult::Success;
}
static inline PolarisUfsScsiResult polaris_ufs_scsi_read(const PolarisUfsNopIo &io,
    const PolarisUfsNopResult &owned,const PolarisUfsDescriptor &unit,uint8_t *buffer,
    uint64_t pa,PolarisUfsScsiKind kind,bool enabled) {
    PolarisUfsScsiResult r;r.kind=kind;if(!enabled)return r;
    r.state=PolarisUfsScsiResult::InvalidRequest;r.bytes=polaris_ufs_scsi_bytes(kind);if(!r.bytes)return r;
    r.state=PolarisUfsScsiResult::Preflight;
    if(owned.state!=PolarisUfsNopResult::Success || !owned.guards || !owned.responseRead || !owned.smmuUnchanged ||
       !owned.stopped || owned.finalRun || owned.finalDb || owned.finalBase!=pa || owned.dma!=pa ||
       unit.state!=PolarisUfsDescriptor::Success || unit.idn!=2 || unit.length<19 || unit.data[1]!=2 || unit.data[2] ||
       unit.data[3]!=1 || unit.data[10]<9 || unit.data[10]>16 || !polaris_ufs_be64(unit.data+11) ||
       polaris_ufs_be64(unit.data+11)>(UINT64_MAX>>unit.data[10]) ||
       !buffer || (pa&4095u) || pa<0x100000000ULL || pa>0x140000000ULL-UFS_NOP_BYTES)return r;
    r.dataPa=pa+UFS_SCSI_DATA;
    auto read=[&](unsigned off,uint32_t &v){return io.read(io.context,off,&v);};
    auto write=[&](unsigned off,uint32_t v){r.writes++;io.write(io.context,off,v);};
    auto wait=[&](unsigned off,uint32_t expected) {
        for(unsigned i=0;i<1000;i++){uint32_t v=0;if(!read(off,v))return false;if(v==expected)return true;io.delay(io.context,100);}
        return false;
    };
    uint32_t lo=0,hi=0,run=0,db=0,tmdb=0,tmrun=0,ie=0,hce=0,hcs=0,is=0;
    r.state=PolarisUfsScsiResult::ReadFailed;
    if(!read(0x50,lo)||!read(0x54,hi)||!read(0x60,run)||!read(0x58,db)||!read(0x78,tmdb)||
       !read(0x80,tmrun)||!read(0x24,ie)||!read(0x34,hce)||!read(0x30,hcs)||!read(0x20,is))return r;
    if(((uint64_t(hi)<<32)|lo)!=pa || run || db || tmdb || tmrun || ie || hce!=1 ||
       (hcs&0xbu)!=0xbu || (is&0x71884u)){r.state=PolarisUfsScsiResult::StateChanged;return r;}
    polaris_ufs_scsi_prepare(buffer,pa,kind);io.cache(io.context,1);
    write(0x60,1);
    auto transfer=[&]() {
        if(!wait(0x60,1)){r.state=PolarisUfsScsiResult::StartTimeout;return;}
        write(0x58,1);r.doorbells++;
        bool done=false;
        for(unsigned i=0;i<5000;i++) {
            uint32_t v=0;r.polls++;
            if(!read(0x58,v)){r.state=PolarisUfsScsiResult::ReadFailed;return;}
            if(!v){done=true;break;}
            if(v!=1){r.state=PolarisUfsScsiResult::StateChanged;return;}
            io.delay(io.context,100);
        }
        if(!done){r.state=PolarisUfsScsiResult::Timeout;return;}
        io.cache(io.context,2);r.ocs=buffer[8]&15u;r.guards=polaris_ufs_scsi_guards(buffer,pa,kind);
        auto parsed=polaris_ufs_scsi_parse(r,buffer,unit);
        r.state=r.ocs?PolarisUfsScsiResult::OcsError:!r.guards?PolarisUfsScsiResult::GuardError:parsed;
    };
    transfer();write(0x60,0);r.stopped=wait(0x60,0);
    if(!read(0x60,r.finalRun)||!read(0x58,r.finalDb)||!read(0x20,r.isAfter)||!read(0x24,ie)||
       !read(0x34,hce)||!read(0x30,hcs)||!read(0x50,lo)||!read(0x54,hi)){r.state=PolarisUfsScsiResult::ReadFailed;return r;}
    if(!r.stopped || (r.state==PolarisUfsScsiResult::Success &&
       (r.finalRun || r.finalDb || ie || hce!=1 || (hcs&0xbu)!=0xbu || (r.isAfter&0x71884u) ||
        ((uint64_t(hi)<<32)|lo)!=pa)))r.state=PolarisUfsScsiResult::CleanupFailed;
    return r;
}
