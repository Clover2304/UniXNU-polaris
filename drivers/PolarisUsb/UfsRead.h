// SPDX-License-Identifier: BSD-2-Clause-Patent
// Single-block READ(10), fixed LUN 0 and LBA 0..5, gated by verified capacity.
#pragma once
#include "UfsScsi.h"
constexpr unsigned UFS_READ_SECTORS=6, UFS_READ_BYTES=4096;
struct PolarisUfsSector {
    PolarisUfsScsiResult command;
    uint8_t data[UFS_READ_BYTES]{};
};
static inline void polaris_ufs_read_sector(PolarisUfsSector &sector,const PolarisUfsNopIo &io,
    const PolarisUfsNopResult &owned,const PolarisUfsDescriptor &unit,const PolarisUfsScsiResult &capacity,
    uint8_t *buffer,uint64_t pa,unsigned lba) {
    sector.command.state=PolarisUfsScsiResult::Preflight;
    if(lba>=UFS_READ_SECTORS || capacity.state!=PolarisUfsScsiResult::Success ||
       capacity.kind!=PolarisUfsScsiKind::Capacity10 || !capacity.guards || !capacity.stopped ||
       capacity.finalRun || capacity.finalDb || capacity.blockBytes!=UFS_READ_BYTES ||
       capacity.lastLba+1!=polaris_ufs_be64(unit.data+11) || capacity.lastLba<lba)return;
    sector.command=polaris_ufs_scsi_read(io,owned,unit,buffer,pa,PolarisUfsScsiKind::Read10,true,lba);
    if(sector.command.state!=PolarisUfsScsiResult::Success)return;
    // Queue is stopped and cache invalidation completed before retaining data.
    for(unsigned i=0;i<UFS_READ_BYTES;i++)sector.data[i]=buffer[UFS_SCSI_DATA+i];
}
