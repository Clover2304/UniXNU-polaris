// SPDX-License-Identifier: BSD-2-Clause-Patent
// Bounded FAT32 reader for one fixed 8.3 path; not a mounted filesystem.
#pragma once
#include "UfsRead.h"
constexpr unsigned UFS_FAT_FILE_BYTES=8193, UFS_FAT_MAX_READS=128;
static inline uint16_t polaris_fat_le16(const uint8_t *p){return uint16_t(p[0])|(uint16_t(p[1])<<8);}
static inline uint32_t polaris_fat_le32(const uint8_t *p){return uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24);}
static inline uint64_t polaris_fat_le64(const uint8_t *p){return uint64_t(polaris_fat_le32(p))|(uint64_t(polaris_fat_le32(p+4))<<32);}
static inline uint32_t polaris_fat_crc(const uint8_t *p,unsigned bytes,bool header=false) {
    uint32_t crc=~0u;
    for(unsigned i=0;i<bytes;i++) {
        crc^=header && i>=16 && i<20?0:p[i];
        for(unsigned b=0;b<8;b++)crc=(crc>>1)^((crc&1u)?0xedb88320u:0);
    }
    return ~crc;
}
static inline bool polaris_fat_equal(const uint8_t *a,const uint8_t *b,unsigned bytes) {
    for(unsigned i=0;i<bytes;i++)if(a[i]!=b[i])return false;
    return true;
}
static inline bool polaris_fat_nonzero(const uint8_t *a,unsigned bytes) {
    for(unsigned i=0;i<bytes;i++)if(a[i])return true;
    return false;
}
static inline bool polaris_ufs_esp_gpt(const PolarisUfsSector *sectors,unsigned count,
    const PolarisUfsScsiResult &capacity) {
    if(count!=6 || capacity.state!=PolarisUfsScsiResult::Success || capacity.kind!=PolarisUfsScsiKind::Capacity10 ||
       !capacity.guards || !capacity.stopped || capacity.finalRun || capacity.finalDb || capacity.blockBytes!=4096)return false;
    for(unsigned i=0;i<6;i++) {
        const auto &r=sectors[i].command;
        if(r.state!=PolarisUfsScsiResult::Success || r.kind!=PolarisUfsScsiKind::Read10 ||
           r.lba!=i || r.bytes!=4096 || !r.guards || !r.stopped || r.finalRun || r.finalDb)return false;
    }
    const auto *m=sectors[0].data,*h=sectors[1].data,*table=sectors[2].data;
    bool protective=false;for(unsigned i=0;i<4;i++)protective|=m[446+16*i+4]==0xee;
    const uint8_t signature[]={'E','F','I',' ','P','A','R','T'};
    if(m[510]!=0x55 || m[511]!=0xaa || !protective || !polaris_fat_equal(h,signature,8) ||
       polaris_fat_le32(h+8)!=0x10000 || polaris_fat_le32(h+12)!=92 || polaris_fat_le32(h+20) ||
       polaris_fat_crc(h,92,true)!=polaris_fat_le32(h+16) || polaris_fat_le64(h+24)!=1 ||
       polaris_fat_le64(h+32)!=capacity.lastLba || polaris_fat_le64(h+72)!=2 ||
       polaris_fat_le32(h+80)!=32 || polaris_fat_le32(h+84)!=128 ||
       polaris_fat_crc(table,4096)!=polaris_fat_le32(h+88))return false;
    uint64_t first=polaris_fat_le64(h+40),last=polaris_fat_le64(h+48);
    if(first<6 || first>last || last>=capacity.lastLba)return false;
    unsigned matches=0;
    for(unsigned i=0;i<32;i++) {
        const auto *e=table+128*i;if(!polaris_fat_nonzero(e,16))continue;
        uint64_t start=polaris_fat_le64(e+32),end=polaris_fat_le64(e+40);
        if(start<first || end<start || end>last || !polaris_fat_nonzero(e+16,16))return false;
        for(unsigned j=0;j<i;j++) {
            const auto *prior=table+128*j;if(!polaris_fat_nonzero(prior,16))continue;
            if(polaris_fat_equal(e+16,prior+16,16) ||
               (start<=polaris_fat_le64(prior+40) && end>=polaris_fat_le64(prior+32)))return false;
        }
        const char name[]="userdata";bool same=true;
        for(unsigned k=0;k<36;k++)if(polaris_fat_le16(e+56+2*k)!=(k<8?unsigned(name[k]):0))same=false;
        if(same){matches++;if(start!=UFS_ESP_START || end-start+1<UFS_ESP_BLOCKS)return false;}
    }
    return matches==1;
}
struct PolarisFatIo {void *context;bool (*read)(void *,uint32_t,uint8_t *);};
struct PolarisFatWork {uint8_t block[4096]{};uint32_t cached=~0u;};
struct PolarisFatResult {
    enum State {Disabled,Preflight,Boot,Directory,Fat,Loop,File,Io,Budget,Success} state=Disabled;
    unsigned reads=0,fileBytes=0,clusterCount=0,sectorBytes=0,sectorsPerCluster=0;
    uint32_t reserved=0,fatSectors=0,totalSectors=0,dataSector=0,rootCluster=0,clusters=0;
    uint32_t chain[32]{};
    uint8_t data[UFS_FAT_FILE_BYTES]{};
};
class PolarisFatReader {
    PolarisFatIo io;PolarisFatWork &w;PolarisFatResult &r;
    bool bytes(uint32_t offset,uint8_t *out,unsigned size) {
        if(offset>UFS_ESP_BLOCKS*4096u || size>UFS_ESP_BLOCKS*4096u-offset){r.state=PolarisFatResult::Io;return false;}
        while(size) {
            uint32_t block=offset/4096,at=offset%4096;
            if(w.cached!=block) {
                if(r.reads>=UFS_FAT_MAX_READS){r.state=PolarisFatResult::Budget;return false;}
                r.reads++;if(!io.read(io.context,block,w.block)){r.state=PolarisFatResult::Io;return false;}w.cached=block;
            }
            unsigned n=size<4096-at?size:4096-at;
            for(unsigned i=0;i<n;i++)out[i]=w.block[at+i];
            size-=n;offset+=n;out+=n;
        }
        return true;
    }
    bool valid(uint32_t cluster) const {return cluster>=2 && cluster-2<r.clusters;}
    bool next(uint32_t cluster,uint32_t &value) {
        if(!valid(cluster)){r.state=PolarisFatResult::Fat;return false;}
        uint8_t a[4],b[4];
        if(!bytes(r.reserved*512+cluster*4,a,4) || !bytes((r.reserved+r.fatSectors)*512+cluster*4,b,4))return false;
        value=polaris_fat_le32(a)&0x0fffffffu;
        if(value!=(polaris_fat_le32(b)&0x0fffffffu) || (value<0x0ffffff8u && !valid(value))){r.state=PolarisFatResult::Fat;return false;}
        return true;
    }
    uint32_t clusterOffset(uint32_t cluster) const {return (r.dataSector+(cluster-2)*r.sectorsPerCluster)*512;}
    bool find(uint32_t start,const uint8_t *name,bool directory,uint32_t &cluster,uint32_t &size) {
        r.state=PolarisFatResult::Directory;uint32_t visited[16]{};unsigned visits=0;bool found=false;
        while(true) {
            if(!valid(start) || visits==16){r.state=PolarisFatResult::Loop;return false;}
            for(unsigned i=0;i<visits;i++)if(visited[i]==start){r.state=PolarisFatResult::Loop;return false;}
            visited[visits++]=start;
            for(unsigned off=0;off<r.sectorsPerCluster*512;off+=32) {
                uint8_t entry[32];if(!bytes(clusterOffset(start)+off,entry,32))return false;
                if(!entry[0])return found;
                if(entry[0]==0xe5 || (entry[11]&0x0f)==0x0f || (entry[11]&8))continue;
                if(!polaris_fat_equal(entry,name,11))continue;
                if(found || bool(entry[11]&0x10)!=directory || (entry[11]&0xc0) || (polaris_fat_le16(entry+20)&0xf000))return false;
                cluster=(uint32_t(polaris_fat_le16(entry+20))<<16)|polaris_fat_le16(entry+26);
                size=polaris_fat_le32(entry+28);if(!valid(cluster) || (directory && size))return false;found=true;
            }
            uint32_t after=0;if(!next(start,after))return false;if(after>=0x0ffffff8u)return found;start=after;
        }
    }
public:
    PolarisFatReader(PolarisFatIo input,PolarisFatWork &work,PolarisFatResult &result):io(input),w(work),r(result){}
    bool run() {
        r.state=PolarisFatResult::Boot;uint8_t b[512];if(!bytes(0,b,sizeof(b)))return false;
        r.sectorBytes=polaris_fat_le16(b+11);r.sectorsPerCluster=b[13];r.reserved=polaris_fat_le16(b+14);
        r.totalSectors=polaris_fat_le32(b+32);r.fatSectors=polaris_fat_le32(b+36);r.rootCluster=polaris_fat_le32(b+44);
        if(b[510]!=0x55 || b[511]!=0xaa || r.sectorBytes!=512 || !r.sectorsPerCluster ||
           r.sectorsPerCluster>64 || (r.sectorsPerCluster&(r.sectorsPerCluster-1)) || !r.reserved || b[16]!=2 ||
           polaris_fat_le16(b+17) || polaris_fat_le16(b+19) || polaris_fat_le16(b+22) ||
           polaris_fat_le16(b+40) || polaris_fat_le16(b+42) || r.totalSectors!=UFS_ESP_BLOCKS*8u ||
           !r.fatSectors || r.fatSectors>4096 || r.reserved>r.totalSectors ||
           uint64_t(r.reserved)+2*uint64_t(r.fatSectors)>=r.totalSectors)return false;
        r.dataSector=r.reserved+2*r.fatSectors;r.clusters=(r.totalSectors-r.dataSector)/r.sectorsPerCluster;
        if(r.clusters<65525 || r.clusters>=0x0ffffff5u || uint64_t(r.clusters+2)*4>uint64_t(r.fatSectors)*512 || !valid(r.rootCluster))return false;
        uint32_t efi=0,unixnu=0,file=0,size=0;
        const uint8_t efiName[]={'E','F','I',' ',' ',' ',' ',' ',' ',' ',' '};
        const uint8_t unixnuName[]={'U','N','I','X','N','U',' ',' ',' ',' ',' '};
        const uint8_t fileName[]={'R','E','A','D','T','E','S','T','B','I','N'};
        if(!find(r.rootCluster,efiName,true,efi,size) || !find(efi,unixnuName,true,unixnu,size) || !find(unixnu,fileName,false,file,size))return false;
        r.state=PolarisFatResult::File;if(size!=UFS_FAT_FILE_BYTES)return false;r.fileBytes=size;
        unsigned written=0;
        while(true) {
            if(!valid(file) || r.clusterCount==32){r.state=PolarisFatResult::Loop;return false;}
            for(unsigned i=0;i<r.clusterCount;i++)if(r.chain[i]==file){r.state=PolarisFatResult::Loop;return false;}
            r.chain[r.clusterCount++]=file;
            unsigned amount=size-written,clusterBytes=r.sectorsPerCluster*512;if(amount>clusterBytes)amount=clusterBytes;
            if(!bytes(clusterOffset(file),r.data+written,amount))return false;written+=amount;
            uint32_t after=0;if(!next(file,after))return false;
            if(written==size){if(after<0x0ffffff8u){r.state=PolarisFatResult::File;return false;}break;}
            if(after>=0x0ffffff8u){r.state=PolarisFatResult::File;return false;}file=after;
        }
        r.state=PolarisFatResult::Success;return true;
    }
};
