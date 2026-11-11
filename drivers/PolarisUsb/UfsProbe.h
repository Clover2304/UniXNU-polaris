// SPDX-License-Identifier: BSD-2-Clause-Patent
// Bounded UFS register snapshot; no write, DMA, UIC or media-command interface.
// SDM845 resources and register semantics: SOURCES.md.
#pragma once
#include <stdint.h>

struct PolarisUfsIo {
    void *context;
    bool (*read)(void *, bool controller, unsigned offset, uint32_t *value);
};
struct PolarisUfsSnapshot {
    enum State { Disabled, GateReadFailed, PowerOff, Reset, ClocksOff,
                 ControllerReadFailed, GateChanged, Invalid, Ready } state=Disabled;
    uint32_t gate[6]{}; // BCR, GDSCR, CFG_GDSCR, AXI, AHB, aggregate AXI
    uint32_t regs[6]{}; // CAP, VER, HCS, HCE, UTRLDBR, UTMRLDBR
    unsigned gccReads=0,controllerReads=0;
};
static inline const char *polaris_ufs_state(PolarisUfsSnapshot::State state) {
    switch(state) {
        case PolarisUfsSnapshot::Disabled:return "disabled";
        case PolarisUfsSnapshot::GateReadFailed:return "gate-read-failed";
        case PolarisUfsSnapshot::PowerOff:return "power-not-ready";
        case PolarisUfsSnapshot::Reset:return "reset-asserted";
        case PolarisUfsSnapshot::ClocksOff:return "clocks-not-ready";
        case PolarisUfsSnapshot::ControllerReadFailed:return "controller-read-failed";
        case PolarisUfsSnapshot::GateChanged:return "gate-changed";
        case PolarisUfsSnapshot::Invalid:return "invalid-registers";
        case PolarisUfsSnapshot::Ready:return "snapshot";
    }
    return "unknown";
}
static inline PolarisUfsSnapshot::State polaris_ufs_gate(const uint32_t *g) {
    // UFS_PHY_GDSC uses POLL_CFG_GDSCR: POWER_UP_COMPLETE is bit 16 at +4.
    // GDSCR bit 31 alone is insufficient. Never uncollapse or enable a clock.
    if(g[1]==0xffffffffu || g[2]==0xffffffffu || (g[1]&1u) || !(g[2]&(1u<<16)))
        return PolarisUfsSnapshot::PowerOff;
    if(g[0]&1u)return PolarisUfsSnapshot::Reset;
    for(unsigned i=3;i<6;i++)
        if(!(g[i]&1u) || (g[i]&0x80000000u))return PolarisUfsSnapshot::ClocksOff;
    return PolarisUfsSnapshot::Ready;
}
static inline bool polaris_ufs_version(uint32_t v) {
    switch(v) {
        case 0x100:case 0x101:case 0x110:case 0x200:case 0x210:
        case 0x300:case 0x310:case 0x400:return true;
        default:return false;
    }
}
static inline PolarisUfsSnapshot polaris_ufs_probe(const PolarisUfsIo &io,bool enabled) {
    PolarisUfsSnapshot s;
    if(!enabled)return s;
    constexpr unsigned gates[]={0x77000,0x77004,0x77008,0x7700c,0x77010,0x82024};
    constexpr unsigned registers[]={0,8,0x30,0x34,0x58,0x78};
    for(unsigned i=0;i<6;i++) {
        if(!io.read(io.context,false,gates[i],&s.gate[i])){s.state=PolarisUfsSnapshot::GateReadFailed;return s;}
        s.gccReads++;
    }
    s.state=polaris_ufs_gate(s.gate);
    if(s.state!=PolarisUfsSnapshot::Ready)return s;
    for(unsigned i=0;i<6;i++) {
        if(!io.read(io.context,true,registers[i],&s.regs[i])){s.state=PolarisUfsSnapshot::ControllerReadFailed;return s;}
        s.controllerReads++;
    }
    // Firmware must leave the domain stable for this bounded snapshot.
    // No exception recovery is implied: the readiness checks precede MMIO.
    for(unsigned i=0;i<6;i++) {
        uint32_t value=0;
        if(!io.read(io.context,false,gates[i],&value)){s.state=PolarisUfsSnapshot::GateReadFailed;return s;}
        s.gccReads++;
        if(value!=s.gate[i]){s.state=PolarisUfsSnapshot::GateChanged;return s;}
    }
    if(s.regs[0]==0 || s.regs[0]==0xffffffffu || !polaris_ufs_version(s.regs[1]) ||
       s.regs[2]==0xffffffffu || (s.regs[3]&~3u))s.state=PolarisUfsSnapshot::Invalid;
    return s;
}
