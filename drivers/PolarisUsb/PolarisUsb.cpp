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
#include <pexpert/pexpert.h>
#include <libkern/libkern.h>
#include <stdarg.h>
#include "Dwc3Regs.h"
#include "UsbWire.h"
#include "UsbBuffers.h"
#include "UsbFifo.h"
#include "UsbSmmu.h"

#ifndef POLARIS_USB_BUILD_ID
#define POLARIS_USB_BUILD_ID "local-unqualified"
#endif
extern "C" {
volatile uint64_t polaris_usb_stats[16];
void polaris_usb_start(void);
void polaris_usb_capture(const char *,unsigned);
int kmputc(int,char);
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
    PolarisBytes<131072> reply;
    PolarisLine input;
    uint8_t tx[DMA_EP3_MAX_CHUNK]{};
    uint8_t logScratch[4096]{};
    size_t pending=0;
};
static void respond(TerminalSession &s,const char *text) {
    if(!s.reply.append(text,strlen(text)))polaris_usb_stats[8]=polaris_usb_stats[8]+1;
}
static void handleCommand(TerminalSession &s,const char *command) {
    polaris_usb_stats[5]=polaris_usb_stats[5]+1;
    char text[512];
    if(!strcmp(command,"ping"))respond(s,"POLARIS_PONG\n");
    else if(!strncmp(command,"echo ",5)) {
        respond(s,"POLARIS_ECHO ");respond(s,command+5);respond(s,"\n");
    } else if(!strcmp(command,"status")) {
        snprintf(text,sizeof(text),"POLARIS_USB_STATUS build=%s stage=%llu core=%08x events=%u resets=%u configured=%u cmd_errors=%llu rx=%llu tx_done=%llu reply_overflow=%llu dma=%llx smmu=%08x/%08x phy_mode=%u\n",
                 POLARIS_USB_BUILD_ID,polaris_usb_stats[0],s.hw.rd(DWC3_GSNPSID),s.hw.usbEvents,s.hw.usbResets,s.hw.configured,
                 polaris_usb_stats[3],polaris_usb_stats[6],polaris_usb_stats[7],polaris_usb_stats[8],s.hw.dmaPhys,
                 s.hw.smmuOrigSmr,s.hw.smmuOrigS2cr,s.hw.phyInitMode);
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
    } else if(!strcmp(command,"help"))respond(s,"POLARIS_COMMANDS ping echo status log help\n");
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
    // Master, sleep, mock UTMI, cfg_noc, aggre, PHY aux/com/ref.
    static const unsigned branches[]={0xf00c,0xf010,0xf014,0x502c,0x8201c,0xf04c,0xf050,0x8c008};
    for(unsigned off:branches)hw.gwr(off,hw.grd(off)|1u);
    for(unsigned off:branches) {
        for(unsigned i=0;hw.grd(off)&0x80000000u;i++){if(i==2000)return false;IODelay(2);}
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
    polaris_usb_stats[9]=result.bank;polaris_usb_stats[10]=result.error;
    polaris_usb_stats[11]=result.before;polaris_usb_stats[12]=result.after;
    polaris_usb_stats[13]=result.cbar;polaris_usb_stats[14]=result.cba2r;polaris_usb_stats[15]=result.idr1;
    console("POLARIS_USB_SMMU error=%u bank=%u old=%u shared=%u moved=%u\n",result.error,result.bank,result.originalBank,result.shared,result.remapped);
    console("POLARIS_USB_CONTEXT sctlr=%08x/%08x cbar=%08x cba2r=%08x smr=%08x s2cr=%08x/%08x\n",
        result.before,result.after,result.cbar,result.cba2r,result.smr,result.s2cr,result.smr?hw.smmu[(0xc00+4*result.slot)/4]:0);
    console("POLARIS_USB_IDR %08x/%08x/%08x\n",result.idr0,result.idr1,result.idr2);
    return result.error==0;
}

static void initQusb2(UsbHw &hw) {
    hw.phyInitMode=1;
    hw.gwr(0x12000,1);IODelay(150);hw.gwr(0x12000,0);IODelay(150);
    // Owned polaris DTB qcom,qusb-phy-init-seq; value, offset pairs.
    static const uint32_t sequence[]={0x23,0x210,0x03,0x04,0x7c,0x18c,0x80,0x2c,0x0a,0x184,0x19,0xb4,0x40,0x194,0x17,0x198,0x21,0x214,0x08,0x220,0x58,0x224,0x07,0x240,0x2b,0x244,0xca,0x248,0x04,0x24c,0x03,0x250,0x00,0x23c,0x22,0x210};
    for(unsigned i=0;i<sizeof(sequence)/sizeof(sequence[0]);i+=2)hw.pwr(sequence[i+1],sequence[i]);
    IODelay(200);
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
    return true;
}
static bool runController(TerminalSession &s) {
    UsbHw &hw=s.hw;
    hw.wr(DWC3_DCTL,hw.rd(DWC3_DCTL)&~DWC3_DCTL_RUN_STOP);
    // Select the UTMI clock when the firmware's SS pipe is not running.
    if(hw.phyInitMode || (hw.grd(0xf054)&0x80000000u)) {
        hw.wr(QSCRATCH_GENERAL_CFG,hw.rd(QSCRATCH_GENERAL_CFG)|QSCRATCH_PIPE_UTMI_CLK_DIS);IODelay(100);
        hw.wr(QSCRATCH_GENERAL_CFG,hw.rd(QSCRATCH_GENERAL_CFG)|QSCRATCH_PIPE_UTMI_CLK_SEL|QSCRATCH_PIPE3_PHYSTATUS_SW);IODelay(100);
        hw.wr(QSCRATCH_GENERAL_CFG,hw.rd(QSCRATCH_GENERAL_CFG)&~QSCRATCH_PIPE_UTMI_CLK_DIS);
    }
    hw.wr(QSCRATCH_HS_PHY_CTRL,hw.rd(QSCRATCH_HS_PHY_CTRL)|QSCRATCH_UTMI_OTG_VBUS_VALID|QSCRATCH_SW_SESSVLD_SEL);
    hw.wr(QSCRATCH_SS_PHY_CTRL,hw.rd(QSCRATCH_SS_PHY_CTRL)|QSCRATCH_LANE0_PWR_PRESENT);
    hw.wr(DWC3_GUSB2PHYCFG(0),hw.rd(DWC3_GUSB2PHYCFG(0))&~(DWC3_GUSB2PHYCFG_SUSPHY|DWC3_GUSB2PHYCFG_ENBLSLPM));
    hw.wr(DWC3_GCTL,(hw.rd(DWC3_GCTL)&~0x3000u)|DWC3_GCTL_PRTCAPDIR(2));
    hw.wr(DWC3_DCTL,hw.rd(DWC3_DCTL)|DWC3_DCTL_CSFTRST);
    for(unsigned i=0;hw.rd(DWC3_DCTL)&DWC3_DCTL_CSFTRST;i++){if(i==100000)return false;IODelay(5);}
    hw.wr(DWC3_GSBUSCFG0,hw.rd(DWC3_GSBUSCFG0)|0x0eu);
    hw.wr(DWC3_GCTL,hw.rd(DWC3_GCTL)|DWC3_GCTL_DSBLCLKGTNG);
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
    programEp0(hw);
    if(hw.lastCmdSt)return false;
    IOSleep(30);hw.wr(DWC3_DCTL,hw.rd(DWC3_DCTL)|DWC3_DCTL_RUN_STOP);
    polaris_usb_stats[0]=6;
    console("POLARIS_USB_RUN core=%08x dma=%llx phy=%08x\n",hw.rd(DWC3_GSNPSID),hw.dmaPhys,hw.prd(0x1a0));
    return true;
}
struct Place {};
} // namespace
inline void *operator new(size_t,void *p,Place) noexcept{return p;}
namespace {
static void worker(void *,wait_result_t) {
    IOSleep(200);
    void *memory=IOMalloc(sizeof(TerminalSession));if(!memory){thread_terminate(current_thread());return;}
    auto *s=new(memory,Place{})TerminalSession();s->hw.initDefaults();
    if(!startController(*s)||!runController(*s)){
        console("POLARIS_USB_FAILED stage=%llu core=%08llx cmd=%d dma=%llx smmu=%08x/%08x cr0=%08x\n",
            polaris_usb_stats[0],polaris_usb_stats[1],s->hw.lastCmdSt,s->hw.dmaPhys,s->hw.smmuOrigSmr,s->hw.smmuOrigS2cr,s->hw.smmu?s->hw.smmu[0]:0);
        if(s->hw.core)console("POLARIS_USB_HW h0=%08x h1=%08x h6=%08x h7=%08x fifo0=%08x\n",s->hw.rd(0xc140),s->hw.rd(0xc144),s->hw.rd(0xc158),s->hw.rd(0xc15c),s->hw.rd(DWC3_GTXFIFOSIZ(0)));
        thread_terminate(current_thread());return;
    }
    uint64_t started=nowMs(),next=started+5000;bool fallback=false;
    for(;;) {
        pollUsbEvents(*s);
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
                fallback=true;s->hw.wr(DWC3_DCTL,s->hw.rd(DWC3_DCTL)&~DWC3_DCTL_RUN_STOP);IOSleep(40);
                initQusb2(s->hw);if(!runController(*s)){logStage("fallback-failed");thread_terminate(current_thread());return;}
            }
        }
        IOSleep(2);
    }
}
} // namespace
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
