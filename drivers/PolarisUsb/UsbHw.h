// SPDX-License-Identifier: BSD-2-Clause-Patent
// Adapted from UniXNU-j716f; see SOURCES.md and LICENSE.
#pragma once
// 64 KiB physically contiguous Normal-Cacheable DMA layout (explicit ARM64 cache maintenance):
//   0x0000..0x0FFF  Event ring (4 KiB, DWC3 write-only after init)
//   0x1000..0x11FF  EP0 TRBs (up to 32 TRBs, 64B-aligned)
//   0x1200..0x1FFF  EP0 control buffer (64B-aligned)
//   0x2000..0x203F  EP1 OUT (dep2) TRBs (64B-aligned)
//   0x2100..0x28FF  EP1 OUT RX buffer (2 KiB, 64B-aligned)
//   0x3000..0x31FF  EP1 IN  (dep3) TRBs (64B-aligned)
//   0x4000..0xFFFF  EP1 IN  TX buffer (48 KiB, 4096B-aligned)
#define DMA_TOTAL_BYTES   0x10000u
#define DMA_EVT_OFF       0x0000u
#define DMA_EP0_TRB_OFF   0x1000u
#define DMA_EP0_BUF_OFF   0x1200u
#define DMA_EP2_TRB_OFF   0x2000u
#define DMA_EP2_BUF_OFF   0x2100u
#define DMA_EP3_TRB_OFF   0x3000u
#define DMA_EP3_BUF_OFF   0x4000u
#define DMA_EP3_MAX_CHUNK 0x4000u  // 16 KiB per Bulk IN burst

// op: 0 = clean (dc cvac), 1 = clean+inval (dc civac), 2 = inval (dc ivac)
static inline void usbCacheRange(const void *p, size_t len, int op) {
    if (!len) return;
    uint64_t ctr = 0;
    __asm__ volatile("mrs %0, ctr_el0" : "=r"(ctr));
    const size_t line = 4u << ((ctr >> 16) & 15u);
    uintptr_t a = reinterpret_cast<uintptr_t>(p) & ~(line - 1u);
    const uintptr_t end = reinterpret_cast<uintptr_t>(p) + len;
    __asm__ volatile("dsb sy" ::: "memory");
    for (; a < end; a += line) {
        if (op == 0) __asm__ volatile("dc cvac, %0" :: "r"(a) : "memory");
        else if (op == 1) __asm__ volatile("dc civac, %0" :: "r"(a) : "memory");
        else __asm__ volatile("dc ivac, %0" :: "r"(a) : "memory");
    }
    __asm__ volatile("dsb sy" ::: "memory");
}

struct UsbHw {
    OSSharedPtr<IOMemoryMap> coreMap, gccMap, phyMap, eudMap, smmuMap;
    IOBufferMemoryDescriptor *dmaDesc = nullptr;
    volatile uint32_t *core    = nullptr;
    volatile uint32_t *gcc     = nullptr;
    volatile uint32_t *phy     = nullptr;
    volatile uint32_t *eud     = nullptr;
    volatile uint32_t *smmu    = nullptr;
    uint8_t *dmaVirt           = nullptr;
    uint64_t dmaPhys           = 0;

    uint32_t evtOff         = 0;
    uint16_t bulkMps        = 512;
    int ep0Stage            = 0;
    bool ep0Three           = false;
    bool ep0In              = false;
    bool ep0Zlp             = false;
    unsigned ep0StatusDep   = 1;
    unsigned ep0Armed       = 0;
    unsigned ep0Trbs        = 0;

    uint8_t configured      = 0;
    uint16_t lineState      = 0;
    bool hostReady          = false;
    bool needReplay         = true;
    bool ep2Armed           = false;
    bool ep3Busy            = false;
    uint32_t ep2RscIdx      = 0;
    uint32_t ep3RscIdx      = 0;
    uint64_t ep3ArmTick     = 0;
    uint32_t usbEvents      = 0;
    uint32_t usbResets      = 0;
    uint32_t usbCfgCount    = 0;
    int lastCmdSt           = 0;
    uint32_t lastCmdDep     = 0;
    uint32_t lastCmdWord    = 0;
    uint32_t lastCmdRaw     = 0;
    const char *initPhase   = "preflight";
    uint32_t smmuBank       = 0;
    uint32_t smmuContextOff = 0;
    uint32_t smmuOrigSmr    = 0;
    uint32_t smmuOrigS2cr   = 0;
    uint32_t phyInitMode    = 0;
    uint8_t lineCoding[7]   = {0x00, 0xC2, 0x01, 0x00, 0x00, 0x00, 0x08};

    void initDefaults() {
        bulkMps        = 512u;
        ep0StatusDep   = 1u;
        needReplay     = true;
        lineCoding[0]  = 0x00;
        lineCoding[1]  = 0xC2;
        lineCoding[2]  = 0x01;
        lineCoding[3]  = 0x00;
        lineCoding[4]  = 0x00;
        lineCoding[5]  = 0x00;
        lineCoding[6]  = 0x08;
    }

    uint32_t rd(uint32_t off) const { return core[off / 4u]; }
    void wr(uint32_t off, uint32_t v) const {
        core[off / 4u] = v;
        __asm__ volatile("dsb sy" ::: "memory");
    }
    uint32_t grd(uint32_t off) const { return gcc[off / 4u]; }
    void gwr(uint32_t off, uint32_t v) const {
        gcc[off / 4u] = v;
        __asm__ volatile("dsb sy" ::: "memory");
    }
    uint32_t prd(uint32_t off) const { return phy[off / 4u]; }
    void pwr(uint32_t off, uint32_t v) const {
        phy[off / 4u] = v;
        __asm__ volatile("dsb sy" ::: "memory");
    }
    void pmask(uint32_t off, uint32_t mask, uint32_t val) const {
        pwr(off, (prd(off) & ~mask) | (val & mask));
    }
};

static uintptr_t mapRangeUncached(uint64_t phys, uint64_t len, OSSharedPtr<IOMemoryMap> &keep) {
    IODeviceMemory *mem = IODeviceMemory::withRange(phys, len);
    if (!mem) return 0;
    keep = OSSharedPtr<IOMemoryMap>(mem->map(kIOMapAnywhere | kIOMapInhibitCache), OSNoRetain);
    mem->release();
    return keep ? static_cast<uintptr_t>(keep->getVirtualAddress()) : 0;
}

