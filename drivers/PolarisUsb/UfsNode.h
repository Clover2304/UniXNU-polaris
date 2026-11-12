// SPDX-License-Identifier: BSD-2-Clause-Patent
// Pure request lifecycle and fixed FAT-file offset mapping. Caller holds lock.
#pragma once
#include "UfsFat.h"
struct PolarisUfsNodeQueue {
    enum State {Idle,Pending,Working,Done,Abandoned} state=Idle;
    uint64_t offset=0;
    unsigned bytes=0;int error=0;
    uint8_t data[4096]{};
    bool submit(uint64_t off,unsigned count) {
        if(state!=Idle || !count || count>4096 || off>=8193 || count>8193-off)return false;
        offset=off;bytes=count;error=0;state=Pending;return true;
    }
    bool claim(){if(state!=Pending)return false;state=Working;return true;}
    void finish(int result){if(state==Abandoned)state=Idle;else if(state==Working){error=result;state=Done;}}
    void cancel(){if(state==Pending)state=Idle;else if(state==Working)state=Abandoned;}
    bool collect(uint8_t *out,int &result) {
        if(state!=Done)return false;result=error;
        if(!error)for(unsigned i=0;i<bytes;i++)out[i]=data[i];state=Idle;return true;
    }
};
static inline bool polaris_ufs_node_span(const PolarisFatResult &file,uint64_t offset,unsigned requested,
    uint32_t &block,unsigned &within,unsigned &count) {
    if(file.state!=PolarisFatResult::Success || file.fileBytes!=8193 || !requested || requested>4096 ||
       offset>=8193 || !file.sectorsPerCluster || file.sectorsPerCluster>64)return false;
    unsigned clusterBytes=file.sectorsPerCluster*512,which=unsigned(offset/clusterBytes);
    if(which>=file.clusterCount || which>=32)return false;
    uint32_t cluster=file.chain[which];if(cluster<2 || cluster-2>=file.clusters)return false;
    uint64_t position=(uint64_t(file.dataSector)+uint64_t(cluster-2)*file.sectorsPerCluster)*512+offset%clusterBytes;
    if(position>=uint64_t(UFS_ESP_BLOCKS)*4096)return false;
    block=uint32_t(position/4096);within=unsigned(position%4096);
    count=requested;
    unsigned tail=8193-unsigned(offset),clusterTail=clusterBytes-unsigned(offset%clusterBytes);
    if(count>tail)count=tail;if(count>clusterTail)count=clusterTail;if(count>4096-within)count=4096-within;
    return count!=0;
}
