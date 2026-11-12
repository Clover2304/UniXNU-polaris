// SPDX-License-Identifier: BSD-2-Clause-Patent
// MIX 2S polling CDC diagnostic channel. Transport attribution: SOURCES.md.
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOLocks.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <libkern/c++/OSSharedPtr.h>
#include <kern/task.h>
#include <kern/thread.h>
#include <kern/clock.h>
#include <pexpert/device_tree.h>
#include <pexpert/pexpert.h>
#include <libkern/libkern.h>
#include <stdarg.h>
#include "Dwc3Regs.h"
#include "UsbWire.h"
#include "UsbBuffers.h"
#include "UsbFifo.h"
#include "UsbSmmu.h"
#include "UfsProbe.h"
#include "UfsInventory.h"
#include "UfsNop.h"
#include "UfsQuery.h"
#include "UfsScsi.h"
#include "UfsRead.h"
#include "UfsFat.h"
#include "UfsNode.h"

#ifndef POLARIS_USB_BUILD_ID
#define POLARIS_USB_BUILD_ID "local-unqualified"
#endif
extern "C" {
volatile uint64_t polaris_usb_stats[16];
// phase, loop count, claimed requests, started physical reads; diagnostic only.
volatile uint64_t polaris_ufs_node_progress[4];
void polaris_usb_start(void);
void polaris_usb_capture(const char *,unsigned);
int kmputc(int,char);
int polaris_ufs_node_start(int) __attribute__((weak_import));
int polaris_ufs_node_copy(uint64_t,unsigned,void *);
int polaris_ufs_node_model_enable(void);
extern volatile uint64_t polaris_node_reads,polaris_node_bytes,polaris_node_opens,polaris_node_denied;
}
namespace {
#include "UsbHw.h"
static IOSimpleLock *gLogLock=nullptr;
static PolarisBytes<65536> gLog;
static void console(const char *format,...) __attribute__((format(printf,1,2)));
static void console(const char *format,...) {
    char text[512];va_list args;va_start(args,format);vsnprintf(text,sizeof(text),format,args);va_end(args);
    for(const char *p=text;*p;p++)(void)kmputc(0,*p);
}
static const char *const kUsbStrings[]={"","UniXNU","MIX 2S Debug CDC","POLARISDBG01"};
static uint64_t nowMs() {
    uint64_t cnt,frq;
    __asm__ volatile("isb; mrs %0,cntvct_el0":"=r"(cnt)::"memory");
    __asm__ volatile("mrs %0,cntfrq_el0":"=r"(frq));
    return frq?(cnt/frq)*1000+((cnt%frq)*1000)/frq:0;
}
static void logStage(const char *text) {console("POLARIS_USB %s\n",text);}
#include "UsbTransfers.h"
struct TerminalSession {
    UsbHw hw;
    OSSharedPtr<IOMemoryMap> ufsMap;
    volatile uint32_t *ufs=nullptr;
    PolarisUfsSnapshot ufsSnapshot;
    PolarisUfsInventory ufsInventory;
    IOBufferMemoryDescriptor *ufsDmaDesc=nullptr;
    uint8_t *ufsDma=nullptr;
    uint64_t ufsDmaPhys=0;
    PolarisUfsNopResult ufsNop;
    uint32_t ufsFaultBefore[2]{},ufsFaultAfter[5]{};
    PolarisUfsDescriptor ufsDescriptors[2];
    unsigned ufsQueryCount=0;
    bool ufsQueryEnabled=false,ufsQuerySmmuUnchanged=false;
    uint32_t ufsQueryFaultBefore[2]{},ufsQueryFaultAfter[2]{};
    PolarisUfsScsiResult ufsScsi[2];
    unsigned ufsScsiCount=0;
    bool ufsScsiEnabled=false,ufsScsiSmmuUnchanged=false;
    uint32_t ufsScsiFaultBefore[2]{},ufsScsiFaultAfter[2]{};
    PolarisUfsSector ufsSectors[UFS_READ_SECTORS];
    unsigned ufsReadCount=0;
    bool ufsReadEnabled=false,ufsReadSmmuUnchanged=false;
    uint32_t ufsReadFaultBefore[2]{},ufsReadFaultAfter[2]{};
    PolarisFatWork ufsEspWork;
    PolarisFatResult ufsEspResult;
    PolarisUfsScsiResult ufsEspCommands[UFS_FAT_MAX_READS];
    unsigned ufsEspCount=0;
    uint32_t ufsEspBlockCrc[UFS_FAT_MAX_READS]{};
    bool ufsEspEnabled=false,ufsEspGpt=false,ufsEspSmmuUnchanged=false;
    uint64_t ufsEspStarted=0;
    uint32_t ufsEspFaultBefore[2]{},ufsEspFaultAfter[2]{};
    IOLock *ufsNodeLock=nullptr;
    PolarisUfsNodeQueue ufsNodeQueue;
    bool ufsNodeEnabled=false,ufsNodeReady=false,ufsNodeFailed=false,ufsNodeSmmuUnchanged=false,ufsNodeModel=false;
    int ufsNodeError=0;
    uint64_t ufsNodeSubmitted=0,ufsNodeCompleted=0,ufsNodeCancelled=0,ufsNodeBusy=0,ufsNodeErrors=0;
    PolarisUfsScsiResult ufsNodeCommands[128];
    uint32_t ufsNodeBlockCrc[128]{},ufsNodeFaultBefore[2]{},ufsNodeFaultAfter[2]{};
    unsigned ufsNodeCount=0;
    uint8_t ufsNodeBlock[4096]{};
    PolarisBytes<131072> reply;
    PolarisLine input;
    uint8_t tx[DMA_EP3_MAX_CHUNK]{};
    uint8_t logScratch[4096]{};
    size_t pending=0;
};
static TerminalSession *gNodeSession=nullptr;
static bool ufsRead(void *context,bool controller,unsigned offset,uint32_t *value) {
    auto &s=*static_cast<TerminalSession *>(context);
    if(controller && !s.ufs)
        s.ufs=reinterpret_cast<volatile uint32_t *>(mapRangeUncached(0x01d84000,0x2500,s.ufsMap));
    volatile uint32_t *base=controller?s.ufs:s.hw.gcc;
    if(!base)return false;
    __asm__ volatile("dsb sy":::"memory");
    *value=base[offset/4];
    __asm__ volatile("dsb sy":::"memory");
    return true;
}
static void ufsText(const TerminalSession &s,char *text,size_t size) {
    const auto &u=s.ufsSnapshot;
    snprintf(text,size,"POLARIS_UFS_SNAPSHOT build=%s state=%s gcc_reads=%u controller_reads=%u writes=0 media_reads=0\n"
        "POLARIS_UFS_GATE bcr=%08x gdsc=%08x cfg=%08x axi=%08x ahb=%08x aggre=%08x\n"
        "POLARIS_UFS_REG cap=%08x ver=%08x hcs=%08x hce=%08x utrl=%08x utmrl=%08x\nPOLARIS_UFS_END\n",
        POLARIS_USB_BUILD_ID,polaris_ufs_state(u.state),u.gccReads,u.controllerReads,
        u.gate[0],u.gate[1],u.gate[2],u.gate[3],u.gate[4],u.gate[5],
        u.regs[0],u.regs[1],u.regs[2],u.regs[3],u.regs[4],u.regs[5]);
}
static void respond(TerminalSession &s,const char *text) {
    if(!s.reply.append(text,strlen(text)))polaris_usb_stats[8]=polaris_usb_stats[8]+1;
}
static bool ufsSmmuRead(void *context,unsigned offset,uint32_t *value) {
    auto &s=*static_cast<TerminalSession *>(context);
    if(!s.hw.smmu || offset>=0x100000 || (offset&3u))return false;
    __asm__ volatile("dsb sy":::"memory");
    *value=s.hw.smmu[offset/4];
    __asm__ volatile("dsb sy":::"memory");
    return true;
}
static void ufsInventoryText(TerminalSession &s,bool response) {
    const auto &r=s.ufsInventory;char text[512];
    auto emit=[&](){if(response)respond(s,text);else console("%s",text);};
    snprintf(text,sizeof(text),"POLARIS_UFS_INVENTORY build=%s state=%s gcc_reads=%u controller_reads=%u smmu_reads=%u writes=0 media_commands=0\n",
        POLARIS_USB_BUILD_ID,polaris_ufs_inventory_state(r.state),r.gccReads,r.controllerReads,r.smmuReads);emit();
    for(unsigned i=0;i<2;i++) {
        const auto *q=r.queues[i];
        snprintf(text,sizeof(text),"POLARIS_UFS_QUEUE sample=%u is=%08x ie=%08x trl=%08x%08x tr_db=%08x tr_run=%08x tmrl=%08x%08x tm_db=%08x tm_run=%08x hcs=%08x hce=%08x\n",
            i,q[0],q[1],q[3],q[2],q[4],q[5],q[7],q[6],q[8],q[9],q[10],q[11]);emit();
    }
    snprintf(text,sizeof(text),"POLARIS_UFS_IOMMU idr0=%08x idr1=%08x idr2=%08x cr0=%08x groups=%u scanned=%u valid=%u pages=%u banks=%u truncated=%u stable=%u queues_stable=%u matches=%u overlap=%u coverage=%04x sid_base=0100 sid_count=16\n",
        r.idr[0],r.idr[1],r.idr[2],r.cr0,r.groups,r.scanned,r.validGroups,r.pages,r.banks,r.truncated,r.stable,r.queuesStable,r.matches,r.overlap,r.coverage);emit();
    for(unsigned i=0;i<r.matches;i++) {
        const auto &m=r.match[i];
        snprintf(text,sizeof(text),"POLARIS_UFS_MATCH n=%u slot=%u smr=%08x s2cr=%08x coverage=%04x bank=%u shared=%u sctlr=%08x cbar=%08x cba2r=%08x\n",
            i,m.slot,m.smr,m.s2cr,m.coverage,m.bank,m.shared,m.sctlr,m.cbar,m.cba2r);emit();
    }
    snprintf(text,sizeof(text),"POLARIS_UFS_INVENTORY_END\n");emit();
}
static bool ufsNopRead(void *context,unsigned offset,uint32_t *value) {
    return ufsRead(context,true,offset,value);
}
static void ufsNopWrite(void *context,unsigned offset,uint32_t value) {
    auto &s=*static_cast<TerminalSession *>(context);
    // No callback exists for SMMU, PHY, reset, interrupt or media writes.
    if(offset!=0x50 && offset!=0x54 && offset!=0x58 && offset!=0x60)return;
    s.ufs[offset/4]=value;__asm__ volatile("dsb sy":::"memory");
}
static void ufsNopDelay(void *,unsigned microseconds){IODelay(microseconds);}
static void ufsNopCache(void *context,unsigned op) {
    auto &s=*static_cast<TerminalSession *>(context);usbCacheRange(s.ufsDma,UFS_NOP_BYTES,static_cast<int>(op));
}
static void runUfsNop(TerminalSession &s,bool enabled) {
    if(!enabled)return;
    if(!polaris_ufs_nop_preflight(s.ufsSnapshot,s.ufsInventory)){s.ufsNop.state=PolarisUfsNopResult::PreflightFailed;return;}
    s.ufsDmaDesc=IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task,
        static_cast<IOOptionBits>(kIODirectionInOut)|static_cast<IOOptionBits>(kIOMemoryPhysicallyContiguous),UFS_NOP_BYTES,0xfffffff000ULL);
    if(!s.ufsDmaDesc || s.ufsDmaDesc->prepare()!=kIOReturnSuccess){s.ufsNop.state=PolarisUfsNopResult::AllocationFailed;return;}
    addr64_t length=0;s.ufsDmaPhys=s.ufsDmaDesc->getPhysicalSegment(0,&length);
    s.ufsDma=static_cast<uint8_t *>(s.ufsDmaDesc->getBytesNoCopy());
    if(!s.ufsDma || length<UFS_NOP_BYTES || (reinterpret_cast<uintptr_t>(s.ufsDma)&4095u)) {
        s.ufsNop.state=PolarisUfsNopResult::DmaInvalid;return;
    }
    unsigned cb=(s.ufsInventory.pages+2u)*4096u;
    if(!ufsSmmuRead(&s,0x48,&s.ufsFaultBefore[0]) || !ufsSmmuRead(&s,cb+0x58,&s.ufsFaultBefore[1])) {
        s.ufsNop.state=PolarisUfsNopResult::ReadFailed;return;
    }
    if(polaris_ufs_nop_fault(s.ufsFaultBefore[0],s.ufsFaultBefore[1])){s.ufsNop.state=PolarisUfsNopResult::PreflightFailed;return;}
    s.ufsNop=polaris_ufs_nop({&s,ufsNopRead,ufsNopWrite,ufsNopDelay,ufsNopCache},
        s.ufsSnapshot,s.ufsInventory,s.ufsDma,s.ufsDmaPhys,s.hw.dmaPhys,true);
    if(!s.ufsNop.writes)return;
    auto after=polaris_ufs_inventory({&s,ufsRead},{&s,ufsSmmuRead},true,true);
    s.ufsNop.smmuUnchanged=polaris_ufs_nop_same_smmu(s.ufsInventory,after);
    const unsigned offsets[]={0x48,cb+0x58,cb+0x60,cb+0x64,cb+0x68};
    for(unsigned i=0;i<5;i++)if(!ufsSmmuRead(&s,offsets[i],&s.ufsFaultAfter[i]))s.ufsNop.smmuUnchanged=false;
    if(s.ufsNop.state==PolarisUfsNopResult::Success &&
       (!s.ufsNop.smmuUnchanged || polaris_ufs_nop_fault(s.ufsFaultAfter[0],s.ufsFaultAfter[1])))s.ufsNop.state=PolarisUfsNopResult::SmmuChanged;
    // Descriptor and pages intentionally retained until reboot, including failures.
}
static void ufsNopText(TerminalSession &s,bool response) {
    const auto &r=s.ufsNop;char text[512];auto emit=[&](){if(response)respond(s,text);else console("%s",text);};
    snprintf(text,sizeof(text),"POLARIS_UFS_NOP build=%s state=%s bytes=%u writes=%u doorbells=%u polls=%u cache_prepared=%u response_read=%u guards=%u stopped=%u smmu_unchanged=%u media_commands=0 smmu_writes=0 retained=%u\n",
        POLARIS_USB_BUILD_ID,polaris_ufs_nop_state(r.state),UFS_NOP_BYTES,r.writes,r.doorbells,r.polls,
        r.cachePrepared,r.responseRead,r.guards,r.stopped,r.smmuUnchanged,s.ufsDmaDesc!=nullptr);emit();
    snprintf(text,sizeof(text),"POLARIS_UFS_NOP_DMA pa=%016llx old_base=%016llx final_base=%016llx usb_pa=%016llx ocs=%u rsp0=%08x rsp1=%08x rsp2=%08x\n",
        r.dma,r.oldBase,r.finalBase,s.hw.dmaPhys,r.ocs,r.rsp[0],r.rsp[1],r.rsp[2]);emit();
    snprintf(text,sizeof(text),"POLARIS_UFS_NOP_REG is_before=%08x is_after=%08x run=%08x db=%08x ie=%08x hcs=%08x hce=%08x\n",
        r.isBefore,r.isAfter,r.finalRun,r.finalDb,r.finalIe,r.finalHcs,r.finalHce);emit();
    snprintf(text,sizeof(text),"POLARIS_UFS_NOP_FAULT global_before=%08x bank_before=%08x global_after=%08x bank_after=%08x far=%08x%08x syn=%08x\nPOLARIS_UFS_NOP_END\n",
        s.ufsFaultBefore[0],s.ufsFaultBefore[1],s.ufsFaultAfter[0],s.ufsFaultAfter[1],s.ufsFaultAfter[3],s.ufsFaultAfter[2],s.ufsFaultAfter[4]);emit();
}
static void runUfsQueries(TerminalSession &s,bool enabled) {
    s.ufsQueryEnabled=enabled;
    if(!enabled || s.ufsNop.state!=PolarisUfsNopResult::Success || !s.ufsNop.smmuUnchanged)return;
    unsigned cb=(s.ufsInventory.pages+2u)*4096u;
    if(!ufsSmmuRead(&s,0x48,&s.ufsQueryFaultBefore[0]) || !ufsSmmuRead(&s,cb+0x58,&s.ufsQueryFaultBefore[1]) ||
       polaris_ufs_nop_fault(s.ufsQueryFaultBefore[0],s.ufsQueryFaultBefore[1]))return;
    const unsigned ids[]={0,2};
    for(unsigned i=0;i<2;i++) {
        s.ufsQueryCount++;
        s.ufsDescriptors[i]=polaris_ufs_read_descriptor({&s,ufsNopRead,ufsNopWrite,ufsNopDelay,ufsNopCache},
            s.ufsNop,s.ufsDma,s.ufsDmaPhys,ids[i],true);
        if(s.ufsDescriptors[i].state!=PolarisUfsDescriptor::Success)break;
    }
    auto after=polaris_ufs_inventory({&s,ufsRead},{&s,ufsSmmuRead},true,true);
    s.ufsQuerySmmuUnchanged=polaris_ufs_nop_same_smmu(s.ufsInventory,after);
    if(!ufsSmmuRead(&s,0x48,&s.ufsQueryFaultAfter[0]) || !ufsSmmuRead(&s,cb+0x58,&s.ufsQueryFaultAfter[1]) ||
       polaris_ufs_nop_fault(s.ufsQueryFaultAfter[0],s.ufsQueryFaultAfter[1]))s.ufsQuerySmmuUnchanged=false;
}
static void queryHex(const uint8_t *data,unsigned bytes,char *out) {
    const char digits[]="0123456789abcdef";
    for(unsigned i=0;i<bytes;i++){out[2*i]=digits[data[i]>>4];out[2*i+1]=digits[data[i]&15u];}out[2*bytes]=0;
}
static void ufsQueryText(TerminalSession &s,bool response) {
    char text[512],hex[65];auto emit=[&](){if(response)respond(s,text);else console("%s",text);};
    unsigned writes=0,doorbells=0;bool ok=s.ufsQueryCount==2 && s.ufsQuerySmmuUnchanged;
    for(unsigned i=0;i<s.ufsQueryCount;i++){const auto &d=s.ufsDescriptors[i];writes+=d.writes;doorbells+=d.doorbells;ok&=d.state==PolarisUfsDescriptor::Success;}
    snprintf(text,sizeof(text),"POLARIS_UFS_QUERY build=%s state=%s count=%u writes=%u doorbells=%u smmu_unchanged=%u sector_commands=0 smmu_writes=0 retained=%u pa=%016llx\n",
        POLARIS_USB_BUILD_ID,!s.ufsQueryEnabled?"disabled":ok?"ok":"failed",s.ufsQueryCount,writes,doorbells,s.ufsQuerySmmuUnchanged,s.ufsDmaDesc!=nullptr,s.ufsDmaPhys);emit();
    for(unsigned i=0;i<s.ufsQueryCount;i++) {
        const auto &d=s.ufsDescriptors[i];queryHex(d.upiu,32,hex);
        snprintf(text,sizeof(text),"POLARIS_UFS_DESC n=%u idn=%02x index=00 selector=00 state=%s ocs=%u response=%02x requested=255 segment=%u osf=%u guards=%u stopped=%u polls=%u run=%08x db=%08x is=%08x upiu=%s\n",
            i,d.idn,polaris_ufs_descriptor_state(d.state),d.ocs,d.response,d.length,d.osfLength,d.guards,d.stopped,d.polls,d.finalRun,d.finalDb,d.isAfter,hex);emit();
        for(unsigned offset=0;offset<d.length && offset<255;offset+=32) {
            unsigned bytes=d.length-offset;if(bytes>32)bytes=32;if(bytes>255-offset)bytes=255-offset;
            queryHex(d.data+offset,bytes,hex);
            snprintf(text,sizeof(text),"POLARIS_UFS_DESC_DATA n=%u offset=%u hex=%s\n",i,offset,hex);emit();
        }
    }
    snprintf(text,sizeof(text),"POLARIS_UFS_QUERY_FAULT global_before=%08x bank_before=%08x global_after=%08x bank_after=%08x\nPOLARIS_UFS_QUERY_END\n",
        s.ufsQueryFaultBefore[0],s.ufsQueryFaultBefore[1],s.ufsQueryFaultAfter[0],s.ufsQueryFaultAfter[1]);emit();
}
static void runUfsScsi(TerminalSession &s,bool enabled) {
    s.ufsScsiEnabled=enabled;
    if(!enabled || s.ufsQueryCount!=2 || !s.ufsQuerySmmuUnchanged ||
       s.ufsDescriptors[0].state!=PolarisUfsDescriptor::Success || s.ufsDescriptors[1].state!=PolarisUfsDescriptor::Success)return;
    unsigned cb=(s.ufsInventory.pages+2u)*4096u;
    if(!ufsSmmuRead(&s,0x48,&s.ufsScsiFaultBefore[0]) || !ufsSmmuRead(&s,cb+0x58,&s.ufsScsiFaultBefore[1]) ||
       polaris_ufs_nop_fault(s.ufsScsiFaultBefore[0],s.ufsScsiFaultBefore[1]))return;
    const PolarisUfsScsiKind kinds[]={PolarisUfsScsiKind::Inquiry,PolarisUfsScsiKind::Capacity10};
    for(unsigned i=0;i<2;i++) {
        s.ufsScsiCount++;
        s.ufsScsi[i]=polaris_ufs_scsi_read({&s,ufsNopRead,ufsNopWrite,ufsNopDelay,ufsNopCache},
            s.ufsNop,s.ufsDescriptors[1],s.ufsDma,s.ufsDmaPhys,kinds[i],true);
        if(s.ufsScsi[i].state!=PolarisUfsScsiResult::Success)break;
    }
    auto after=polaris_ufs_inventory({&s,ufsRead},{&s,ufsSmmuRead},true,true);
    s.ufsScsiSmmuUnchanged=polaris_ufs_nop_same_smmu(s.ufsInventory,after);
    if(!ufsSmmuRead(&s,0x48,&s.ufsScsiFaultAfter[0]) || !ufsSmmuRead(&s,cb+0x58,&s.ufsScsiFaultAfter[1]) ||
       polaris_ufs_nop_fault(s.ufsScsiFaultAfter[0],s.ufsScsiFaultAfter[1]))s.ufsScsiSmmuUnchanged=false;
}
static void ufsScsiText(TerminalSession &s,bool response) {
    char text[512],hex[69];auto emit=[&](){if(response)respond(s,text);else console("%s",text);};
    unsigned writes=0,doorbells=0;bool ok=s.ufsScsiCount==2 && s.ufsScsiSmmuUnchanged;
    for(unsigned i=0;i<s.ufsScsiCount;i++){const auto &r=s.ufsScsi[i];writes+=r.writes;doorbells+=r.doorbells;ok&=r.state==PolarisUfsScsiResult::Success;}
    snprintf(text,sizeof(text),"POLARIS_UFS_SCSI build=%s state=%s count=%u writes=%u doorbells=%u smmu_unchanged=%u prdt_entries=1 sector_commands=0 media_writes=0 smmu_writes=0 retained=%u pa=%016llx\n",
        POLARIS_USB_BUILD_ID,!s.ufsScsiEnabled?"disabled":ok?"ok":"failed",s.ufsScsiCount,writes,doorbells,s.ufsScsiSmmuUnchanged,s.ufsDmaDesc!=nullptr,s.ufsDmaPhys);emit();
    for(unsigned i=0;i<s.ufsScsiCount;i++) {
        const auto &r=s.ufsScsi[i];queryHex(r.upiu,34,hex);
        snprintf(text,sizeof(text),"POLARIS_UFS_SCSI_CMD n=%u kind=%s opcode=%02x lun=00 state=%s ocs=%u response=%02x status=%02x flags=%02x bytes=%u residual=%u segment=%u sense_len=%u guards=%u stopped=%u polls=%u run=%08x db=%08x is=%08x upiu=%s\n",
            i,r.kind==PolarisUfsScsiKind::Inquiry?"inquiry":"capacity10",r.kind==PolarisUfsScsiKind::Inquiry?0x12:0x25,
            polaris_ufs_scsi_state(r.state),r.ocs,r.response,r.status,r.flags,r.bytes,r.residual,r.segment,r.senseLength,r.guards,r.stopped,r.polls,r.finalRun,r.finalDb,r.isAfter,hex);emit();
        queryHex(r.prdt,16,hex);
        snprintf(text,sizeof(text),"POLARIS_UFS_SCSI_DMA n=%u data_pa=%016llx prdt=%s\n",i,r.dataPa,hex);emit();
        for(unsigned offset=0;offset<r.bytes && offset<36;offset+=32) {
            unsigned bytes=r.bytes-offset;if(bytes>32)bytes=32;queryHex(r.data+offset,bytes,hex);
            snprintf(text,sizeof(text),"POLARIS_UFS_SCSI_DATA n=%u offset=%u hex=%s\n",i,offset,hex);emit();
        }
        if(r.senseLength){queryHex(r.sense,r.senseLength,hex);snprintf(text,sizeof(text),"POLARIS_UFS_SCSI_SENSE n=%u hex=%s\n",i,hex);emit();}
    }
    const auto &capacity=s.ufsScsi[1];
    snprintf(text,sizeof(text),"POLARIS_UFS_SCSI_CAPACITY last_lba=%016llx block_bytes=%u logical_blocks=%llu bytes=%llu query_match=%u\n",
        capacity.lastLba,capacity.blockBytes,capacity.state==PolarisUfsScsiResult::Success?capacity.lastLba+1:0,
        capacity.capacityBytes,capacity.state==PolarisUfsScsiResult::Success);emit();
    snprintf(text,sizeof(text),"POLARIS_UFS_SCSI_FAULT global_before=%08x bank_before=%08x global_after=%08x bank_after=%08x\nPOLARIS_UFS_SCSI_END\n",
        s.ufsScsiFaultBefore[0],s.ufsScsiFaultBefore[1],s.ufsScsiFaultAfter[0],s.ufsScsiFaultAfter[1]);emit();
}
static void runUfsRead(TerminalSession &s,bool enabled) {
    s.ufsReadEnabled=enabled;
    if(!enabled || s.ufsScsiCount!=2 || !s.ufsScsiSmmuUnchanged ||
       s.ufsScsi[0].state!=PolarisUfsScsiResult::Success || s.ufsScsi[1].state!=PolarisUfsScsiResult::Success)return;
    unsigned cb=(s.ufsInventory.pages+2u)*4096u;
    if(!ufsSmmuRead(&s,0x48,&s.ufsReadFaultBefore[0]) || !ufsSmmuRead(&s,cb+0x58,&s.ufsReadFaultBefore[1]) ||
       polaris_ufs_nop_fault(s.ufsReadFaultBefore[0],s.ufsReadFaultBefore[1]))return;
    for(unsigned i=0;i<UFS_READ_SECTORS;i++) {
        s.ufsReadCount++;
        polaris_ufs_read_sector(s.ufsSectors[i],{&s,ufsNopRead,ufsNopWrite,ufsNopDelay,ufsNopCache},
            s.ufsNop,s.ufsDescriptors[1],s.ufsScsi[1],s.ufsDma,s.ufsDmaPhys,i);
        auto after=polaris_ufs_inventory({&s,ufsRead},{&s,ufsSmmuRead},true,true);
        s.ufsReadSmmuUnchanged=polaris_ufs_nop_same_smmu(s.ufsInventory,after);
        if(!ufsSmmuRead(&s,0x48,&s.ufsReadFaultAfter[0]) || !ufsSmmuRead(&s,cb+0x58,&s.ufsReadFaultAfter[1]) ||
           polaris_ufs_nop_fault(s.ufsReadFaultAfter[0],s.ufsReadFaultAfter[1]))s.ufsReadSmmuUnchanged=false;
        if(s.ufsSectors[i].command.state!=PolarisUfsScsiResult::Success || !s.ufsReadSmmuUnchanged)break;
    }
}
static void ufsReadText(TerminalSession &s,bool response) {
    char text[512],hex[69];auto emit=[&](){if(response)respond(s,text);else console("%s",text);};
    unsigned writes=0,doorbells=0;bool ok=s.ufsReadCount==UFS_READ_SECTORS && s.ufsReadSmmuUnchanged;
    for(unsigned i=0;i<s.ufsReadCount;i++){const auto &r=s.ufsSectors[i].command;writes+=r.writes;doorbells+=r.doorbells;ok&=r.state==PolarisUfsScsiResult::Success;}
    snprintf(text,sizeof(text),"POLARIS_UFS_READ build=%s state=%s count=%u writes=%u doorbells=%u smmu_unchanged=%u prdt_entries=1 sector_commands=%u media_writes=0 smmu_writes=0 retained=%u pa=%016llx\n",
        POLARIS_USB_BUILD_ID,!s.ufsReadEnabled?"disabled":ok?"ok":"failed",s.ufsReadCount,writes,doorbells,s.ufsReadSmmuUnchanged,doorbells,s.ufsDmaDesc!=nullptr,s.ufsDmaPhys);emit();
    for(unsigned i=0;i<s.ufsReadCount;i++) {
        const auto &sector=s.ufsSectors[i];const auto &r=sector.command;queryHex(r.upiu,34,hex);
        snprintf(text,sizeof(text),"POLARIS_UFS_READ_CMD n=%u lba=%u opcode=28 lun=00 blocks=1 state=%s ocs=%u response=%02x status=%02x flags=%02x bytes=%u residual=%u segment=%u sense_len=%u guards=%u stopped=%u polls=%u run=%08x db=%08x is=%08x upiu=%s\n",
            i,r.lba,polaris_ufs_scsi_state(r.state),r.ocs,r.response,r.status,r.flags,r.bytes,r.residual,r.segment,r.senseLength,r.guards,r.stopped,r.polls,r.finalRun,r.finalDb,r.isAfter,hex);emit();
        queryHex(r.prdt,16,hex);
        snprintf(text,sizeof(text),"POLARIS_UFS_READ_DMA n=%u data_pa=%016llx prdt=%s\n",i,r.dataPa,hex);emit();
        // Full raw sectors are served only on explicit cached USB queries.
        // Keep startup console data below its 64 KiB ring and off the screen.
        if(response && r.state==PolarisUfsScsiResult::Success)for(unsigned offset=0;offset<UFS_READ_BYTES;offset+=32) {
            queryHex(sector.data+offset,32,hex);
            snprintf(text,sizeof(text),"POLARIS_UFS_READ_DATA n=%u offset=%u hex=%s\n",i,offset,hex);emit();
        }
        if(r.senseLength){queryHex(r.sense,r.senseLength,hex);snprintf(text,sizeof(text),"POLARIS_UFS_READ_SENSE n=%u hex=%s\n",i,hex);emit();}
    }
    snprintf(text,sizeof(text),"POLARIS_UFS_READ_FAULT global_before=%08x bank_before=%08x global_after=%08x bank_after=%08x\nPOLARIS_UFS_READ_END\n",
        s.ufsReadFaultBefore[0],s.ufsReadFaultBefore[1],s.ufsReadFaultAfter[0],s.ufsReadFaultAfter[1]);emit();
}
static bool ufsEspRead(void *context,uint32_t block,uint8_t *output) {
    auto &s=*static_cast<TerminalSession *>(context);
    if(!s.ufsEspGpt || !s.ufsEspSmmuUnchanged || block>=UFS_ESP_BLOCKS ||
       s.ufsEspCount>=UFS_FAT_MAX_READS || nowMs()-s.ufsEspStarted>10000)return false;
    auto &r=s.ufsEspCommands[s.ufsEspCount++];
    r=polaris_ufs_scsi_read({&s,ufsNopRead,ufsNopWrite,ufsNopDelay,ufsNopCache},
        s.ufsNop,s.ufsDescriptors[1],s.ufsDma,s.ufsDmaPhys,PolarisUfsScsiKind::EspRead10,true,UFS_ESP_START+block);
    unsigned cb=(s.ufsInventory.pages+2u)*4096u;
    auto after=polaris_ufs_inventory({&s,ufsRead},{&s,ufsSmmuRead},true,true);
    s.ufsEspSmmuUnchanged=polaris_ufs_nop_same_smmu(s.ufsInventory,after);
    if(!ufsSmmuRead(&s,0x48,&s.ufsEspFaultAfter[0]) || !ufsSmmuRead(&s,cb+0x58,&s.ufsEspFaultAfter[1]) ||
       polaris_ufs_nop_fault(s.ufsEspFaultAfter[0],s.ufsEspFaultAfter[1]))s.ufsEspSmmuUnchanged=false;
    if(r.state!=PolarisUfsScsiResult::Success || !s.ufsEspSmmuUnchanged)return false;
    s.ufsEspBlockCrc[s.ufsEspCount-1]=polaris_fat_crc(s.ufsDma+UFS_SCSI_DATA,4096);
    for(unsigned i=0;i<4096;i++)output[i]=s.ufsDma[UFS_SCSI_DATA+i];
    return true;
}
static void runUfsEsp(TerminalSession &s,bool enabled) {
    s.ufsEspEnabled=enabled;if(!enabled)return;
    s.ufsEspResult.state=PolarisFatResult::Preflight;
    if(!s.ufsReadSmmuUnchanged || !polaris_ufs_esp_gpt(s.ufsSectors,s.ufsReadCount,s.ufsScsi[1]))return;
    s.ufsEspGpt=true;unsigned cb=(s.ufsInventory.pages+2u)*4096u;
    if(!ufsSmmuRead(&s,0x48,&s.ufsEspFaultBefore[0]) || !ufsSmmuRead(&s,cb+0x58,&s.ufsEspFaultBefore[1]) ||
       polaris_ufs_nop_fault(s.ufsEspFaultBefore[0],s.ufsEspFaultBefore[1]))return;
    s.ufsEspSmmuUnchanged=true;s.ufsEspStarted=nowMs();
    PolarisFatReader reader({&s,ufsEspRead},s.ufsEspWork,s.ufsEspResult);reader.run();
}
static void ufsEspText(TerminalSession &s,bool response) {
    char text[512],hex[69];auto emit=[&](){if(response)respond(s,text);else console("%s",text);};
    const auto &r=s.ufsEspResult;unsigned writes=0,doorbells=0;
    for(unsigned i=0;i<s.ufsEspCount;i++){writes+=s.ufsEspCommands[i].writes;doorbells+=s.ufsEspCommands[i].doorbells;}
    bool ok=r.state==PolarisFatResult::Success && s.ufsEspSmmuUnchanged && r.reads==s.ufsEspCount;
    snprintf(text,sizeof(text),"POLARIS_UFS_ESP build=%s state=%s error=%u count=%u writes=%u doorbells=%u gpt=%u smmu_unchanged=%u start=%u blocks=%u media_writes=0 smmu_writes=0 retained=%u pa=%016llx\n",
        POLARIS_USB_BUILD_ID,!s.ufsEspEnabled?"disabled":ok?"ok":"failed",unsigned(r.state),s.ufsEspCount,writes,doorbells,s.ufsEspGpt,s.ufsEspSmmuUnchanged,UFS_ESP_START,UFS_ESP_BLOCKS,s.ufsDmaDesc!=nullptr,s.ufsDmaPhys);emit();
    snprintf(text,sizeof(text),"POLARIS_UFS_ESP_FAT sector_bytes=%u spc=%u reserved=%u fats=2 fat_sectors=%u total_sectors=%u data_sector=%u root=%u clusters=%u\n",
        r.sectorBytes,r.sectorsPerCluster,r.reserved,r.fatSectors,r.totalSectors,r.dataSector,r.rootCluster,r.clusters);emit();
    snprintf(text,sizeof(text),"POLARIS_UFS_ESP_FILE path=EFI/UniXNU/READTEST.BIN bytes=%u clusters=%u\n",r.fileBytes,r.clusterCount);emit();
    for(unsigned i=0;i<r.clusterCount && i<32;i++){snprintf(text,sizeof(text),"POLARIS_UFS_ESP_CHAIN n=%u cluster=%u\n",i,r.chain[i]);emit();}
    if(response)for(unsigned i=0;i<s.ufsEspCount;i++) {
        const auto &c=s.ufsEspCommands[i];queryHex(c.upiu,34,hex);
        snprintf(text,sizeof(text),"POLARIS_UFS_ESP_CMD n=%u lba=%u opcode=28 lun=00 blocks=1 state=%s ocs=%u response=%02x status=%02x flags=%02x bytes=%u residual=%u segment=%u sense_len=%u guards=%u stopped=%u polls=%u run=%08x db=%08x is=%08x upiu=%s\n",
            i,c.lba,polaris_ufs_scsi_state(c.state),c.ocs,c.response,c.status,c.flags,c.bytes,c.residual,c.segment,c.senseLength,c.guards,c.stopped,c.polls,c.finalRun,c.finalDb,c.isAfter,hex);emit();
        queryHex(c.prdt,16,hex);snprintf(text,sizeof(text),"POLARIS_UFS_ESP_DMA n=%u data_pa=%016llx prdt=%s crc32=%08x\n",i,c.dataPa,hex,s.ufsEspBlockCrc[i]);emit();
        if(c.senseLength){queryHex(c.sense,c.senseLength,hex);snprintf(text,sizeof(text),"POLARIS_UFS_ESP_SENSE n=%u hex=%s\n",i,hex);emit();}
    }
    if(response && ok)for(unsigned offset=0;offset<r.fileBytes;offset+=32) {
        unsigned count=r.fileBytes-offset;if(count>32)count=32;queryHex(r.data+offset,count,hex);
        snprintf(text,sizeof(text),"POLARIS_UFS_ESP_DATA offset=%u hex=%s\n",offset,hex);emit();
    }
    snprintf(text,sizeof(text),"POLARIS_UFS_ESP_FAULT global_before=%08x bank_before=%08x global_after=%08x bank_after=%08x\nPOLARIS_UFS_ESP_END\n",
        s.ufsEspFaultBefore[0],s.ufsEspFaultBefore[1],s.ufsEspFaultAfter[0],s.ufsEspFaultAfter[1]);emit();
}
static void nodePhase(uint64_t phase){__atomic_store_n(&polaris_ufs_node_progress[0],phase,__ATOMIC_RELEASE);}
static bool ufsNodePhysical(TerminalSession &s,uint32_t block) {
    nodePhase(3);__atomic_fetch_add(&polaris_ufs_node_progress[3],1,__ATOMIC_RELAXED);
    if(s.ufsNodeModel){
        if(block>2)return false;
        for(unsigned i=0;i<4096;i++){uint64_t off=uint64_t(block)*4096+i;s.ufsNodeBlock[i]=uint8_t(off*37+(off>>8)*11+0x51);}
        return true;
    }
    if(block>=UFS_ESP_BLOCKS || s.ufsNodeCount>=128 || !s.ufsNodeSmmuUnchanged)return false;
    unsigned i=s.ufsNodeCount++;auto &r=s.ufsNodeCommands[i];
    r=polaris_ufs_scsi_read({&s,ufsNopRead,ufsNopWrite,ufsNopDelay,ufsNopCache},
        s.ufsNop,s.ufsDescriptors[1],s.ufsDma,s.ufsDmaPhys,PolarisUfsScsiKind::EspRead10,true,UFS_ESP_START+block);
    auto after=polaris_ufs_inventory({&s,ufsRead},{&s,ufsSmmuRead},true,true);
    s.ufsNodeSmmuUnchanged=polaris_ufs_nop_same_smmu(s.ufsInventory,after);
    unsigned cb=(s.ufsInventory.pages+2u)*4096u;
    if(!ufsSmmuRead(&s,0x48,&s.ufsNodeFaultAfter[0]) || !ufsSmmuRead(&s,cb+0x58,&s.ufsNodeFaultAfter[1]) ||
       polaris_ufs_nop_fault(s.ufsNodeFaultAfter[0],s.ufsNodeFaultAfter[1]))s.ufsNodeSmmuUnchanged=false;
    if(r.state!=PolarisUfsScsiResult::Success || !s.ufsNodeSmmuUnchanged)return false;
    s.ufsNodeBlockCrc[i]=polaris_fat_crc(s.ufsDma+UFS_SCSI_DATA,4096);
    for(unsigned n=0;n<4096;n++)s.ufsNodeBlock[n]=s.ufsDma[UFS_SCSI_DATA+n];
    return true;
}
static void serviceUfsNode(TerminalSession &s) {
    if(!s.ufsNodeLock || !s.ufsNodeReady)return;
    IOLockLock(s.ufsNodeLock);
    bool claimed=s.ufsNodeQueue.claim();uint64_t offset=s.ufsNodeQueue.offset;unsigned length=s.ufsNodeQueue.bytes;
    IOLockUnlock(s.ufsNodeLock);if(!claimed)return;
    nodePhase(2);__atomic_fetch_add(&polaris_ufs_node_progress[2],1,__ATOMIC_RELAXED);
    unsigned done=0;int error=0;uint32_t cached=~0u;
    while(done<length) {
        uint32_t block=0;unsigned within=0,count=0;
        if(!polaris_ufs_node_span(s.ufsEspResult,offset+done,length-done,block,within,count)){error=5;break;}
        if(block!=cached){if(!ufsNodePhysical(s,block)){error=5;break;}cached=block;}
        for(unsigned i=0;i<count;i++)s.ufsNodeQueue.data[done+i]=s.ufsNodeBlock[within+i];done+=count;
    }
    nodePhase(4);
    IOLockLock(s.ufsNodeLock);
    if(error){s.ufsNodeFailed=true;s.ufsNodeError=error;s.ufsNodeErrors++;}
    s.ufsNodeCompleted++;s.ufsNodeQueue.finish(error);
    IOLockWakeup(s.ufsNodeLock,&s.ufsNodeQueue.error,true);
    IOLockUnlock(s.ufsNodeLock);
}
static int nodeCopy(uint64_t offset,unsigned bytes,void *output) {
    auto *s=__atomic_load_n(&gNodeSession,__ATOMIC_ACQUIRE);
    if(!s || !s->ufsNodeLock)return 6;
    if(!output || !bytes || bytes>4096 || offset>=8193 || bytes>8193-offset)return 22;
    IOLockLock(s->ufsNodeLock);
    if(!s->ufsNodeReady || s->ufsNodeFailed){IOLockUnlock(s->ufsNodeLock);return 5;}
    if(s->ufsNodeSubmitted>=64){IOLockUnlock(s->ufsNodeLock);return 28;}
    if(!s->ufsNodeQueue.submit(offset,bytes)){s->ufsNodeBusy++;IOLockUnlock(s->ufsNodeLock);return 16;}
    s->ufsNodeSubmitted++;
    IOLockWakeup(s->ufsNodeLock,&s->ufsNodeQueue,true);
    uint64_t deadline=0;clock_interval_to_deadline(5000,kMillisecondScale,&deadline);
    for(;;) {
        int error=0;
        if(s->ufsNodeQueue.collect(static_cast<uint8_t *>(output),error)){IOLockUnlock(s->ufsNodeLock);return error;}
        if(mach_absolute_time()>=deadline){
            unsigned queue=unsigned(s->ufsNodeQueue.state);
            s->ufsNodeQueue.cancel();s->ufsNodeCancelled++;IOLockUnlock(s->ufsNodeLock);
            uint64_t progress[4];for(unsigned i=0;i<4;i++)progress[i]=__atomic_load_n(&polaris_ufs_node_progress[i],__ATOMIC_ACQUIRE);
            console("POLARIS_UFS_NODE_TIMEOUT queue=%u phase=%llu loops=%llu claimed=%llu phys=%llu\n",queue,progress[0],progress[1],progress[2],progress[3]);
            return 60;
        }
        // Sleep atomically with mutex release; completion cannot be lost.
        (void)IOLockSleepDeadline(s->ufsNodeLock,&s->ufsNodeQueue.error,deadline,THREAD_UNINT);
    }
}
static void nodeWorkerPause(TerminalSession &s) {
    if(!s.ufsNodeLock || !s.ufsNodeReady){IOSleep(2);return;}
    IOLockLock(s.ufsNodeLock);
    if(s.ufsNodeQueue.state!=PolarisUfsNodeQueue::Pending){
        uint64_t deadline=0;clock_interval_to_deadline(2,kMillisecondScale,&deadline);
        (void)IOLockSleepDeadline(s.ufsNodeLock,&s.ufsNodeQueue,deadline,THREAD_UNINT);
    }
    IOLockUnlock(s.ufsNodeLock);
}

static void runUfsNode(TerminalSession &s,bool enabled) {
    s.ufsNodeEnabled=enabled;if(!enabled)return;
    if(s.ufsEspResult.state!=PolarisFatResult::Success || !s.ufsEspSmmuUnchanged || !polaris_ufs_node_start){s.ufsNodeError=6;return;}
    unsigned cb=(s.ufsInventory.pages+2u)*4096u;
    if(!ufsSmmuRead(&s,0x48,&s.ufsNodeFaultBefore[0]) || !ufsSmmuRead(&s,cb+0x58,&s.ufsNodeFaultBefore[1]) ||
       polaris_ufs_nop_fault(s.ufsNodeFaultBefore[0],s.ufsNodeFaultBefore[1])){s.ufsNodeError=5;return;}
    s.ufsNodeLock=IOLockAlloc();if(!s.ufsNodeLock){s.ufsNodeError=12;return;}
    s.ufsNodeSmmuUnchanged=true;s.ufsNodeReady=true;
    __atomic_store_n(&gNodeSession,&s,__ATOMIC_RELEASE);
    s.ufsNodeError=polaris_ufs_node_start(0);
    if(s.ufsNodeError){s.ufsNodeReady=false;__atomic_store_n(&gNodeSession,static_cast<TerminalSession *>(nullptr),__ATOMIC_RELEASE);}
}
static void ufsNodeText(TerminalSession &s) {
    char text[640],hex[69];uint64_t submitted=0,completed=0,cancelled=0,busy=0,errors=0;unsigned state=0;
    if(s.ufsNodeLock){IOLockLock(s.ufsNodeLock);submitted=s.ufsNodeSubmitted;completed=s.ufsNodeCompleted;cancelled=s.ufsNodeCancelled;busy=s.ufsNodeBusy;errors=s.ufsNodeErrors;state=unsigned(s.ufsNodeQueue.state);IOLockUnlock(s.ufsNodeLock);}
    bool ok=s.ufsNodeReady && !s.ufsNodeFailed && state==0 && completed && submitted==completed && !cancelled && !errors;
    snprintf(text,sizeof(text),"POLARIS_UFS_NODE build=%s state=%s mode=%s ready=%u failed=%u error=%d queue=%u submitted=%llu completed=%llu cancelled=%llu busy=%llu errors=%llu count=%u writes=%u doorbells=%u smmu_unchanged=%u media_writes=0 smmu_writes=0 pa=%016llx\n",
        POLARIS_USB_BUILD_ID,!s.ufsNodeEnabled?"disabled":ok?"ok":"pending",s.ufsNodeModel?"model":"hardware",s.ufsNodeReady,s.ufsNodeFailed,s.ufsNodeError,state,submitted,completed,cancelled,busy,errors,s.ufsNodeCount,3*s.ufsNodeCount,s.ufsNodeCount,s.ufsNodeSmmuUnchanged,s.ufsDmaPhys);respond(s,text);
    snprintf(text,sizeof(text),"POLARIS_UFS_NODE_VFS opens=%llu reads=%llu bytes=%llu denied=%llu path=/dev/polaris-esp-file size=8193\n",polaris_node_opens,polaris_node_reads,polaris_node_bytes,polaris_node_denied);respond(s,text);
    for(unsigned i=0;i<s.ufsNodeCount;i++) {
        const auto &r=s.ufsNodeCommands[i];queryHex(r.upiu,34,hex);
        snprintf(text,sizeof(text),"POLARIS_UFS_NODE_CMD n=%u lba=%u opcode=28 lun=00 blocks=1 state=%s ocs=%u response=%02x status=%02x flags=%02x bytes=%u residual=%u segment=%u sense_len=%u guards=%u stopped=%u polls=%u run=%08x db=%08x is=%08x upiu=%s\n",i,r.lba,polaris_ufs_scsi_state(r.state),r.ocs,r.response,r.status,r.flags,r.bytes,r.residual,r.segment,r.senseLength,r.guards,r.stopped,r.polls,r.finalRun,r.finalDb,r.isAfter,hex);respond(s,text);
        queryHex(r.prdt,16,hex);snprintf(text,sizeof(text),"POLARIS_UFS_NODE_DMA n=%u data_pa=%016llx prdt=%s crc32=%08x\n",i,r.dataPa,hex,s.ufsNodeBlockCrc[i]);respond(s,text);
    }
    snprintf(text,sizeof(text),"POLARIS_UFS_NODE_FAULT global_before=%08x bank_before=%08x global_after=%08x bank_after=%08x\nPOLARIS_UFS_NODE_END\n",s.ufsNodeFaultBefore[0],s.ufsNodeFaultBefore[1],s.ufsNodeFaultAfter[0],s.ufsNodeFaultAfter[1]);respond(s,text);
}
static void handleCommand(TerminalSession &s,const char *command) {
    polaris_usb_stats[5]=polaris_usb_stats[5]+1;
    char text[512];
    if(!strcmp(command,"ping"))respond(s,"POLARIS_PONG\n");
    else if(!strncmp(command,"echo ",5)) {
        respond(s,"POLARIS_ECHO ");respond(s,command+5);respond(s,"\n");
    } else if(!strcmp(command,"status")) {
        snprintf(text,sizeof(text),"POLARIS_USB_STATUS build=%s stage=%llu core=%08x events=%u resets=%u configured=%u cmd_errors=%llu rx=%llu tx_done=%llu reply_overflow=%llu dma=%llx smmu=%08x/%08x phy_mode=%u last_error=%s/%u/%x/%x/%d\n",
                 POLARIS_USB_BUILD_ID,polaris_usb_stats[0],s.hw.rd(DWC3_GSNPSID),s.hw.usbEvents,s.hw.usbResets,s.hw.configured,
                 polaris_usb_stats[3],polaris_usb_stats[6],polaris_usb_stats[7],polaris_usb_stats[8],s.hw.dmaPhys,
                 s.hw.smmuOrigSmr,s.hw.smmuOrigS2cr,s.hw.phyInitMode,s.hw.errorContext,s.hw.errorCmdDep,s.hw.errorCmdWord,s.hw.errorCmdRaw,s.hw.errorCmdStatus);
        respond(s,text);
    } else if(!strcmp(command,"log")) {
        // All commands and TX run on this worker; only capture needs the spin lock.
        if(s.reply.available()>60000){respond(s,"ERR busy\n");return;}
        auto state=IOSimpleLockLockDisableInterrupt(gLogLock);
        uint64_t head=gLog.head,dropped=gLog.dropped;
        size_t bytes=0;
        IOSimpleLockUnlockEnableInterrupt(gLogLock,state);
        snprintf(text,sizeof(text),"POLARIS_LOG_BEGIN build=%s capacity=65536 scope=post-console-open end=%llu dropped=%llu\n",
                 POLARIS_USB_BUILD_ID,head,dropped);respond(s,text);
        // Snapshot by sequence. A concurrent overwrite is reported, never read as a different log.
        uint64_t cursor=head-(head<65536?head:65536);
        while(cursor<head) {
            state=IOSimpleLockLockDisableInterrupt(gLogLock);
            if(cursor<gLog.tail){IOSimpleLockUnlockEnableInterrupt(gLogLock,state);respond(s,"\nERR log-overwritten\n");break;}
            bytes=(head-cursor)<sizeof(s.logScratch)?static_cast<size_t>(head-cursor):sizeof(s.logScratch);
            for(size_t i=0;i<bytes;i++)s.logScratch[i]=gLog.data[(cursor+i)%65536];
            IOSimpleLockUnlockEnableInterrupt(gLogLock,state);
            if(!s.reply.append(s.logScratch,bytes)){polaris_usb_stats[8]=polaris_usb_stats[8]+1;break;}
            cursor+=bytes;
        }
        snprintf(text,sizeof(text),"\nPOLARIS_LOG_END end=%llu dropped=%llu\n",head,dropped);respond(s,text);
    } else if(!strcmp(command,"ufs")){ufsText(s,text,sizeof(text));respond(s,text);}
    else if(!strcmp(command,"ufs-map"))ufsInventoryText(s,true);
    else if(!strcmp(command,"ufs-nop"))ufsNopText(s,true);
    else if(!strcmp(command,"ufs-query"))ufsQueryText(s,true);
    else if(!strcmp(command,"ufs-scsi"))ufsScsiText(s,true);
    else if(!strcmp(command,"ufs-read"))ufsReadText(s,true);
    else if(!strcmp(command,"ufs-esp"))ufsEspText(s,true);
    else if(!strcmp(command,"ufs-node"))ufsNodeText(s);
    else if(!strcmp(command,"help"))respond(s,"POLARIS_COMMANDS ping echo status log ufs ufs-map ufs-nop ufs-query ufs-scsi ufs-read ufs-esp ufs-node help\n");
    else respond(s,"ERR unknown-command\n");
}
#include "UsbEvents.h"

// SDM845 GCC, from Linux gcc-sdm845.c. Preserve firmware rail and RCG state.
static bool enableClocks(UsbHw &hw) {
    hw.gcc=reinterpret_cast<volatile uint32_t *>(mapRangeUncached(0x00100000,0x100000,hw.gccMap));
    if(!hw.gcc)return false;
    polaris_usb_stats[0]=2;
    uint32_t power=hw.grd(0xf004);
    if(power&1)hw.gwr(0xf004,power&~1u);
    for(unsigned i=0;!(hw.grd(0xf004)&0x80000000u);i++){if(i==5000)return false;IODelay(5);}
    // Master, sleep, mock UTMI, cfg_noc, aggre, PHY aux/com/ref and AHB2PHY.
    static const unsigned branches[]={0xf00c,0xf010,0xf014,0x502c,0x8201c,0xf04c,0xf050,0x8c008,0x6a004};
    for(unsigned off:branches)hw.gwr(off,hw.grd(off)|1u);
    for(unsigned off:branches) {
        for(unsigned i=0;hw.grd(off)&0x80000000u;i++){if(i==2000){console("POLARIS_USB_CLOCK_GATE off=%x value=%08x\n",off,hw.grd(off));return false;}IODelay(2);}
    }
    return true;
}
static uint32_t smmuRead(void *context,unsigned offset) {
    return static_cast<volatile uint32_t *>(context)[offset/4];
}
static void smmuWrite(void *context,unsigned offset,uint32_t value) {
    static_cast<volatile uint32_t *>(context)[offset/4]=value;
    __asm__ volatile("dsb sy":::"memory");
}
static bool prepareSmmu(UsbHw &hw) {
    hw.smmu=reinterpret_cast<volatile uint32_t *>(mapRangeUncached(0x15000000,0x100000,hw.smmuMap));
    if(!hw.smmu)return false;
    PolarisSmmuIo io{const_cast<uint32_t *>(hw.smmu),smmuRead,smmuWrite};
    auto result=polaris_usb_smmu(io,hw.dmaPhys,DMA_TOTAL_BYTES);
    hw.smmuOrigSmr=result.smr;hw.smmuOrigS2cr=result.s2cr;hw.smmuBank=result.bank;
    if(!result.error && result.pages && result.smr && ((result.s2cr>>16)&3u)==0)hw.smmuContextOff=(result.pages+result.bank)*4096u;
    polaris_usb_stats[9]=result.bank;polaris_usb_stats[10]=result.error;
    polaris_usb_stats[11]=result.before;polaris_usb_stats[12]=result.after;
    polaris_usb_stats[13]=result.cbar;polaris_usb_stats[14]=result.cba2r;polaris_usb_stats[15]=result.idr1;
    console("POLARIS_USB_SMMU error=%u bank=%u old=%u shared=%u moved=%u\n",result.error,result.bank,result.originalBank,result.shared,result.remapped);
    console("POLARIS_USB_CONTEXT sctlr=%08x/%08x cbar=%08x cba2r=%08x smr=%08x s2cr=%08x/%08x\n",
        result.before,result.after,result.cbar,result.cba2r,result.smr,result.s2cr,result.smr?hw.smmu[(0xc00+4*result.slot)/4]:0);
    console("POLARIS_USB_IDR %08x/%08x/%08x\n",result.idr0,result.idr1,result.idr2);
    return result.error==0;
}

static bool initQusb2(UsbHw &hw) {
    hw.initPhase="phy-not-ready";
    hw.phyInitMode=1;
    hw.gwr(0x12000,1);IODelay(150);hw.gwr(0x12000,0);IODelay(150);
    // Owned polaris DTB qcom,qusb-phy-init-seq; value, offset pairs.
    static const uint32_t sequence[]={0x23,0x210,0x03,0x04,0x7c,0x18c,0x80,0x2c,0x0a,0x184,0x19,0xb4,0x40,0x194,0x17,0x198,0x21,0x214,0x08,0x220,0x58,0x224,0x07,0x240,0x2b,0x244,0xca,0x248,0x04,0x24c,0x03,0x250,0x00,0x23c,0x22,0x210};
    for(unsigned i=0;i<sizeof(sequence)/sizeof(sequence[0]);i+=2)hw.pwr(sequence[i+1],sequence[i]);
    IODelay(200);
    for(unsigned i=0;i<10000;i++){if(hw.prd(0x1a0)&1u)return true;IODelay(1);}
    return false;
}
static bool startController(TerminalSession &s) {
    UsbHw &hw=s.hw;
    polaris_usb_stats[0]=1;
    if(!enableClocks(hw)){logStage("clock-enable-failed");return false;}
    hw.core=reinterpret_cast<volatile uint32_t *>(mapRangeUncached(0x0a600000,0x100000,hw.coreMap));
    hw.phy=reinterpret_cast<volatile uint32_t *>(mapRangeUncached(0x088e2000,0x1000,hw.phyMap));
    if(!hw.core || !hw.phy)return false;
    uint32_t core=hw.rd(DWC3_GSNPSID),id=core&DWC3_GSNPSID_MASK;
    polaris_usb_stats[1]=core;polaris_usb_stats[0]=3;
    hw.wr(DWC3_DCTL,hw.rd(DWC3_DCTL)&~DWC3_DCTL_RUN_STOP);IOSleep(20);
    if(id!=DWC3_GSNPSID_USB3 && id!=DWC3_GSNPSID_USB31 && id!=DWC3_GSNPSID_USB31_3331)return false;
    hw.dmaDesc=IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task,
        static_cast<IOOptionBits>(kIODirectionInOut)|static_cast<IOOptionBits>(kIOMemoryPhysicallyContiguous),DMA_TOTAL_BYTES,0xfffffff000ULL);
    if(!hw.dmaDesc || hw.dmaDesc->prepare()!=kIOReturnSuccess)return false;
    addr64_t segment=0;hw.dmaPhys=hw.dmaDesc->getPhysicalSegment(0,&segment);hw.dmaVirt=static_cast<uint8_t *>(hw.dmaDesc->getBytesNoCopy());
    unsigned width=hw.rd(0xc140)>>24;
    if(!hw.dmaVirt || segment<DMA_TOTAL_BYTES || width<32 || width>64 || (width<64 && ((hw.dmaPhys+DMA_TOTAL_BYTES-1)>>width)))return false;
    polaris_usb_stats[2]=hw.dmaPhys;polaris_usb_stats[0]=4;
    if(!prepareSmmu(hw))return false;
    memset(hw.dmaVirt,0,DMA_TOTAL_BYTES);usbCacheRange(hw.dmaVirt,DMA_TOTAL_BYTES,1);
    polaris_usb_stats[0]=5;
    if(!(hw.prd(0x1a0)&1u)) {
        logStage("firmware QUSB2 not ready: restore before endpoint initialization");
        if(!initQusb2(hw))return false;
    }
    return true;
}
static bool runController(TerminalSession &s) {
    UsbHw &hw=s.hw;
    hw.commandContext="startup";
    hw.initPhase="reset-timeout";
    hw.lastCmdSt=0;hw.lastCmdDep=hw.lastCmdWord=hw.lastCmdRaw=0;
    hw.wr(DWC3_DCTL,hw.rd(DWC3_DCTL)&~DWC3_DCTL_RUN_STOP);
    // The owned DT limits this controller to HS. Follow the qcom UTMI-as-PIPE
    // sequence directly; SDM845 PIPE uses BRANCH_HALT_SKIP, not a reliable HALT bit.
    hw.wr(QSCRATCH_GENERAL_CFG,hw.rd(QSCRATCH_GENERAL_CFG)|QSCRATCH_PIPE_UTMI_CLK_DIS);IODelay(100);
    hw.wr(QSCRATCH_GENERAL_CFG,hw.rd(QSCRATCH_GENERAL_CFG)|QSCRATCH_PIPE_UTMI_CLK_SEL|QSCRATCH_PIPE3_PHYSTATUS_SW);IODelay(100);
    hw.wr(QSCRATCH_GENERAL_CFG,hw.rd(QSCRATCH_GENERAL_CFG)&~QSCRATCH_PIPE_UTMI_CLK_DIS);
    hw.wr(QSCRATCH_HS_PHY_CTRL,hw.rd(QSCRATCH_HS_PHY_CTRL)|QSCRATCH_UTMI_OTG_VBUS_VALID|QSCRATCH_SW_SESSVLD_SEL);
    hw.wr(QSCRATCH_SS_PHY_CTRL,hw.rd(QSCRATCH_SS_PHY_CTRL)|QSCRATCH_LANE0_PWR_PRESENT);
    hw.wr(DWC3_GUSB2PHYCFG(0),hw.rd(DWC3_GUSB2PHYCFG(0))&~(DWC3_GUSB2PHYCFG_SUSPHY|DWC3_GUSB2PHYCFG_ENBLSLPM));
    hw.wr(DWC3_GCTL,(hw.rd(DWC3_GCTL)&~0x3000u)|DWC3_GCTL_PRTCAPDIR(2));
    hw.wr(DWC3_DCTL,hw.rd(DWC3_DCTL)|DWC3_DCTL_CSFTRST);
    for(unsigned i=0;hw.rd(DWC3_DCTL)&DWC3_DCTL_CSFTRST;i++){if(i==100000)return false;IODelay(5);}
    // Device reset can restore PHY suspend defaults. Keep HS clocks available
    // throughout endpoint initialization, including between commands.
    hw.wr(DWC3_GUSB2PHYCFG(0),hw.rd(DWC3_GUSB2PHYCFG(0))&~(DWC3_GUSB2PHYCFG_SUSPHY|DWC3_GUSB2PHYCFG_ENBLSLPM));
    hw.wr(DWC3_GSBUSCFG0,hw.rd(DWC3_GSBUSCFG0)|0x0eu);
    hw.wr(DWC3_GCTL,hw.rd(DWC3_GCTL)|DWC3_GCTL_DSBLCLKGTNG);
    hw.initPhase="fifo-plan";
    uint32_t fifos[3];
    if(!polaris_usb_fifos(hw.rd(0xc140),hw.rd(0xc144),hw.rd(0xc158),hw.rd(0xc15c),hw.rd(DWC3_GTXFIFOSIZ(0)),fifos))return false;
    for(unsigned i=0;i<3;i++)hw.wr(DWC3_GTXFIFOSIZ(i),fifos[i]);
    hw.wr(DWC3_DCFG,(hw.rd(DWC3_DCFG)&~(DWC3_DCFG_SPEED_MASK|DWC3_DCFG_DEVADDR_MASK))|DWC3_DCFG_HIGHSPEED);
    hw.wr(DWC3_DEVTEN,7);
    hw.wr(DWC3_GEVNTADRLO(0),static_cast<uint32_t>(hw.dmaPhys));
    hw.wr(DWC3_GEVNTADRHI(0),static_cast<uint32_t>(hw.dmaPhys>>32));
    hw.wr(DWC3_GEVNTSIZ(0),4096|(1u<<31)); // Polling: mask IRQ, retain DMA events.
    hw.wr(DWC3_GEVNTCOUNT(0),0);hw.evtOff=0;
    hw.configured=0;hw.ep2Armed=false;hw.ep3Busy=false;hw.ep2RscIdx=hw.ep3RscIdx=0;
    hw.initPhase="ep0-command";
    if(!programEp0(hw))return false;
    IOSleep(30);hw.wr(DWC3_DCTL,hw.rd(DWC3_DCTL)|DWC3_DCTL_RUN_STOP);
    polaris_usb_stats[0]=6;
    hw.initPhase="running";
    console("POLARIS_USB_RUN core=%08x dma=%llx phy=%08x\n",hw.rd(DWC3_GSNPSID),hw.dmaPhys,hw.prd(0x1a0));
    return true;
}
struct Place {};
} // namespace
inline void *operator new(size_t,void *p,Place) noexcept{return p;}
namespace {
static void nodeModelWorker(void *context,wait_result_t) {
    auto &s=*static_cast<TerminalSession *>(context);
    for(;;){
        nodePhase(1);__atomic_fetch_add(&polaris_ufs_node_progress[1],1,__ATOMIC_RELAXED);
        serviceUfsNode(s);nodePhase(7);nodeWorkerPause(s);
    }
}
static int enableNodeModel() {
    unsigned enabled=0;DTEntry root=nullptr;const void *compatible=nullptr;unsigned bytes=0;
    if(!PE_parse_boot_argn("polaris-ufs-node-model",&enabled,sizeof(enabled)) || enabled!=1 ||
       SecureDTLookupEntry(nullptr,"/",&root)!=kSuccess || SecureDTGetProperty(root,"compatible",&compatible,&bytes)!=kSuccess ||
       bytes!=sizeof("linux,dummy-virt") || memcmp(compatible,"linux,dummy-virt",sizeof("linux,dummy-virt")))return 1;
    if(__atomic_load_n(&gNodeSession,__ATOMIC_ACQUIRE))return 16;
    void *memory=IOMalloc(sizeof(TerminalSession));if(!memory)return 12;
    auto *s=new(memory,Place{})TerminalSession();s->ufsNodeLock=IOLockAlloc();
    if(!s->ufsNodeLock){s->~TerminalSession();IOFree(memory,sizeof(TerminalSession));return 12;}
    s->ufsNodeModel=true;s->ufsNodeReady=true;s->ufsNodeEnabled=true;
    auto &file=s->ufsEspResult;file.state=PolarisFatResult::Success;file.fileBytes=8193;
    file.sectorsPerCluster=2;file.clusters=130000;file.clusterCount=9;
    for(unsigned i=0;i<9;i++)file.chain[i]=i+2;
    __atomic_store_n(&gNodeSession,s,__ATOMIC_RELEASE);
    thread_t thread=THREAD_NULL;
    if(kernel_thread_start(nodeModelWorker,s,&thread)!=KERN_SUCCESS){
        __atomic_store_n(&gNodeSession,static_cast<TerminalSession *>(nullptr),__ATOMIC_RELEASE);
        IOLockFree(s->ufsNodeLock);s->~TerminalSession();IOFree(memory,sizeof(TerminalSession));return 12;
    }
    thread_deallocate(thread);return 0;
}
static void worker(void *,wait_result_t) {
    IOSleep(200);
    void *memory=IOMalloc(sizeof(TerminalSession));if(!memory){thread_terminate(current_thread());return;}
    auto *s=new(memory,Place{})TerminalSession();s->hw.initDefaults();
    console("POLARIS_USB_BUILD %s\n",POLARIS_USB_BUILD_ID);
    auto diagnostic=[&]() {
        console("POLARIS_USB_FAILED stage=%llu core=%08llx cmd=%d dma=%llx smmu=%08x/%08x cr0=%08x\n",
            polaris_usb_stats[0],polaris_usb_stats[1],s->hw.lastCmdSt,s->hw.dmaPhys,s->hw.smmuOrigSmr,s->hw.smmuOrigS2cr,s->hw.smmu?s->hw.smmu[0]:0);
        console("POLARIS_USB_STOP reason=%s dep=%u command=%08x raw=%08x dctl=%08x\n",
            s->hw.initPhase,s->hw.lastCmdDep,s->hw.lastCmdWord,s->hw.lastCmdRaw,s->hw.core?s->hw.rd(DWC3_DCTL):0);
        if(s->hw.core)console("POLARIS_USB_HW h0=%08x h1=%08x h6=%08x h7=%08x fifo0=%08x\n",s->hw.rd(0xc140),s->hw.rd(0xc144),s->hw.rd(0xc158),s->hw.rd(0xc15c),s->hw.rd(DWC3_GTXFIFOSIZ(0)));
        if(s->hw.core)console("POLARIS_USB_CLOCK usb2=%08x usb3=%08x mux=%08x pipe=%08x phy=%08x ahb=%08x\n",
            s->hw.rd(DWC3_GUSB2PHYCFG(0)),s->hw.rd(DWC3_GUSB3PIPECTL(0)),s->hw.rd(QSCRATCH_GENERAL_CFG),s->hw.grd(0xf054),s->hw.phy?s->hw.prd(0x1a0):0,s->hw.grd(0x6a004));
        if(s->hw.smmu && s->hw.smmuContextOff) {
            unsigned cb=s->hw.smmuContextOff;
            console("POLARIS_USB_FAULT fsr=%08x far=%08x%08x syn=%08x\n",s->hw.smmu[(cb+0x58)/4],s->hw.smmu[(cb+0x64)/4],s->hw.smmu[(cb+0x60)/4],s->hw.smmu[(cb+0x68)/4]);
        }
    };
    bool ok=startController(*s);
    if(ok) {
        ok=runController(*s);
        if(!ok && !s->hw.phyInitMode && ((!strcmp(s->hw.initPhase,"ep0-command") && s->hw.lastCmdSt==-1) || !strcmp(s->hw.initPhase,"reset-timeout"))) {
            diagnostic();logStage("retry: QUSB2 init followed by full device reset");
            s->hw.wr(DWC3_DCTL,s->hw.rd(DWC3_DCTL)&~DWC3_DCTL_RUN_STOP);IOSleep(40);
            ok=initQusb2(s->hw) && runController(*s);
        }
    }
    if(!ok){diagnostic();thread_terminate(current_thread());return;}
    unsigned ufsEnable=0;
    bool probe=PE_parse_boot_argn("polaris-ufs-probe",&ufsEnable,sizeof(ufsEnable)) && ufsEnable==1;
    s->ufsSnapshot=polaris_ufs_probe({s,ufsRead},probe);
    if(probe){char text[512];ufsText(*s,text,sizeof(text));console("%s",text);}
    unsigned inventoryEnable=0;
    bool inventory=probe && PE_parse_boot_argn("polaris-ufs-inventory",&inventoryEnable,sizeof(inventoryEnable)) && inventoryEnable==1;
    s->ufsInventory=polaris_ufs_inventory({s,ufsRead},{s,ufsSmmuRead},inventory,s->ufsSnapshot.state==PolarisUfsSnapshot::Ready);
    if(inventory)ufsInventoryText(*s,false);
    unsigned nopEnable=0;
    bool nop=inventory && PE_parse_boot_argn("polaris-ufs-nop",&nopEnable,sizeof(nopEnable)) && nopEnable==1;
    runUfsNop(*s,nop);if(nop)ufsNopText(*s,false);
    unsigned queryEnable=0;
    bool query=nop && PE_parse_boot_argn("polaris-ufs-query",&queryEnable,sizeof(queryEnable)) && queryEnable==1;
    runUfsQueries(*s,query);if(query)ufsQueryText(*s,false);
    unsigned scsiEnable=0;
    bool scsi=query && PE_parse_boot_argn("polaris-ufs-scsi",&scsiEnable,sizeof(scsiEnable)) && scsiEnable==1;
    runUfsScsi(*s,scsi);if(scsi)ufsScsiText(*s,false);
    unsigned readEnable=0;
    bool read=scsi && PE_parse_boot_argn("polaris-ufs-read",&readEnable,sizeof(readEnable)) && readEnable==1;
    runUfsRead(*s,read);if(read)ufsReadText(*s,false);
    unsigned espEnable=0;
    bool esp=read && PE_parse_boot_argn("polaris-ufs-esp",&espEnable,sizeof(espEnable)) && espEnable==1;
    runUfsEsp(*s,esp);if(esp)ufsEspText(*s,false);
    unsigned nodeEnable=0;
    bool node=esp && PE_parse_boot_argn("polaris-ufs-node",&nodeEnable,sizeof(nodeEnable)) && nodeEnable==1;
    runUfsNode(*s,node);
    uint64_t started=nowMs(),next=started+5000;bool fallback=s->hw.phyInitMode!=0;
    for(;;) {
        nodePhase(1);__atomic_fetch_add(&polaris_ufs_node_progress[1],1,__ATOMIC_RELAXED);
        serviceUfsNode(*s);
        nodePhase(5);pollUsbEvents(*s);
        nodePhase(6);
        polaris_usb_stats[4]=s->hw.usbEvents;
        if(s->hw.hostReady && s->hw.configured && !s->hw.ep3Busy) {
            if(!s->pending)s->pending=s->reply.peek(s->tx,sizeof(s->tx));
            if(s->pending && startBulkIn(s->hw,s->tx,static_cast<uint32_t>(s->pending))){s->reply.consume(s->pending);s->pending=0;}
        }
        uint64_t now=nowMs();
        if(now>=next) {
            next=now+10000;
            console("POLARIS_USB_STATE ev=%u rst=%u cfg=%u cmd=%d dsts=%08x dctl=%08x phy=%08x smmu=%08x/%08x\n",
                s->hw.usbEvents,s->hw.usbResets,s->hw.configured,s->hw.lastCmdSt,s->hw.rd(DWC3_DSTS),s->hw.rd(DWC3_DCTL),s->hw.prd(0x1a0),s->hw.smmuOrigSmr,s->hw.smmuOrigS2cr);
            if(!fallback && !s->hw.usbEvents) {
                nodePhase(8);fallback=true;s->hw.wr(DWC3_DCTL,s->hw.rd(DWC3_DCTL)&~DWC3_DCTL_RUN_STOP);IOSleep(40);
                if(!initQusb2(s->hw)||!runController(*s)){nodePhase(9);diagnostic();logStage("fallback-failed");thread_terminate(current_thread());return;}
            }
        }
        nodePhase(7);nodeWorkerPause(*s);
    }
}
} // namespace
extern "C" int polaris_ufs_node_model_enable(void){return enableNodeModel();}
extern "C" int polaris_ufs_node_copy(uint64_t offset,unsigned bytes,void *output){return nodeCopy(offset,bytes,output);}
extern "C" void polaris_usb_capture(const char *data,unsigned length) {
    IOSimpleLock *lock=gLogLock;if(!lock)return;
    auto state=IOSimpleLockLockDisableInterrupt(lock);
    for(unsigned i=0;i<length;i++)gLog.capture(static_cast<uint8_t>(data[i]));
    IOSimpleLockUnlockEnableInterrupt(lock,state);
}
extern "C" void polaris_usb_start(void) {
    unsigned enable=0;
    if(!PE_parse_boot_argn("polaris-usb",&enable,sizeof(enable)) || enable!=1)return;
    static unsigned started=0;if(__atomic_exchange_n(&started,1,__ATOMIC_ACQ_REL))return;
    gLogLock=IOSimpleLockAlloc();if(!gLogLock)return;
    thread_t thread=THREAD_NULL;
    if(kernel_thread_start(worker,nullptr,&thread)==KERN_SUCCESS)thread_deallocate(thread);
}
