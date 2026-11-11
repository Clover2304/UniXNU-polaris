// SPDX-License-Identifier: BSD-2-Clause-Patent
// Adapted from UniXNU-j716f; see SOURCES.md and LICENSE.
static int epCmd(UsbHw &hw, unsigned dep, uint32_t cmd, uint32_t p0, uint32_t p1, uint32_t p2, uint32_t *rawOut = nullptr) {
    const uint32_t cfg = hw.rd(DWC3_GUSB2PHYCFG(0));
    const uint32_t drop = cfg & (DWC3_GUSB2PHYCFG_SUSPHY | DWC3_GUSB2PHYCFG_ENBLSLPM);
    if (drop) hw.wr(DWC3_GUSB2PHYCFG(0), cfg & ~drop);
    hw.wr(DWC3_DEPCMDPAR0(dep), p0);
    hw.wr(DWC3_DEPCMDPAR1(dep), p1);
    hw.wr(DWC3_DEPCMDPAR2(dep), p2);
    hw.lastCmdDep=dep;hw.lastCmdWord=cmd;
    hw.wr(DWC3_DEPCMD(dep), cmd | DWC3_DEPCMD_CMDACT);
    int st = -1;
    for (unsigned i = 0; i < 20000; ++i) {
        const uint32_t v = hw.rd(DWC3_DEPCMD(dep));
        hw.lastCmdRaw=v;
        if (!(v & DWC3_DEPCMD_CMDACT)) {
            if (rawOut) *rawOut = v;
            st = static_cast<int>(DWC3_DEPCMD_STATUS(v));
            break;
        }
        IODelay(1);
    }
    if (drop) hw.wr(DWC3_GUSB2PHYCFG(0), cfg);
    hw.lastCmdSt = st;
    if(st) polaris_usb_stats[3]=polaris_usb_stats[3]+1;
    return st;
}

static void endDepTransfer(UsbHw &hw, unsigned dep, uint32_t &rscIdx, bool &busyFlag) {
    if (rscIdx != 0u) {
        (void)epCmd(hw, dep,
                    DWC3_DEPCMD_ENDTRANSFER | DWC3_DEPCMD_HIPRI_FORCERM |
                    DWC3_DEPCMD_CMDIOC | DWC3_DEPCMD_PARAM(rscIdx),
                    0, 0, 0);
        IODelay(100);
        rscIdx = 0u;
    }
    busyFlag = false;
}

static volatile uint32_t *ep0TrbPtr(UsbHw &hw, unsigned slot = 0) {
    return reinterpret_cast<volatile uint32_t *>(hw.dmaVirt + DMA_EP0_TRB_OFF + slot * 16u);
}

static void ep0SetTrb(UsbHw &hw, unsigned slot, uint64_t pa, uint32_t sz, uint32_t type, bool last) {
    volatile uint32_t *t = ep0TrbPtr(hw, slot);
    t[0] = static_cast<uint32_t>(pa);
    t[1] = static_cast<uint32_t>(pa >> 32);
    t[2] = sz;
    t[3] = type | DWC3_TRB_CTRL_ISP_IMI | (last ? (DWC3_TRB_CTRL_IOC | DWC3_TRB_CTRL_LST) : DWC3_TRB_CTRL_CHN);
}

static void ep0CommitHead(UsbHw &hw) {
    __asm__ volatile("dsb sy" ::: "memory");
    volatile uint32_t *t = ep0TrbPtr(hw, 0);
    t[3] = t[3] | DWC3_TRB_CTRL_HWO;
    usbCacheRange(hw.dmaVirt + DMA_EP0_TRB_OFF, 64u, 1);
}

static void ep0Start(UsbHw &hw, unsigned dep) {
    const uint64_t pa = hw.dmaPhys + DMA_EP0_TRB_OFF;
    (void)epCmd(hw, dep, DWC3_DEPCMD_STARTTRANSFER, static_cast<uint32_t>(pa >> 32), static_cast<uint32_t>(pa), 0);
}

static void ep0ArmSetup(UsbHw &hw) {
    hw.ep0Stage = 0;
    hw.ep0Armed = 0;
    hw.ep0Trbs  = 1;
    usbCacheRange(hw.dmaVirt + DMA_EP0_BUF_OFF, 64u, 1);
    ep0SetTrb(hw, 0, hw.dmaPhys + DMA_EP0_BUF_OFF, 8u, DWC3_TRBCTL_CONTROL_SETUP, true);
    ep0CommitHead(hw);
    ep0Start(hw, 0);
}

static void ep0Restart(UsbHw &hw) {
    (void)epCmd(hw, 0, DWC3_DEPCMD_SETSTALL, 0, 0, 0);
    (void)epCmd(hw, 0, DWC3_DEPCMD_CLEARSTALL, 0, 0, 0);
    ep0ArmSetup(hw);
}

static void ep0StartData(UsbHw &hw, unsigned dep, uint32_t len) {
    hw.ep0Stage = 1;
    hw.ep0Armed = len;
    const uint64_t bufPa = hw.dmaPhys + DMA_EP0_BUF_OFF;
    if (dep == 1u) {
        usbCacheRange(hw.dmaVirt + DMA_EP0_BUF_OFF, len ? len : 64u, 0);
        if (hw.ep0Zlp) {
            hw.ep0Zlp  = false;
            hw.ep0Trbs = 2;
            ep0SetTrb(hw, 1, bufPa, 0u, DWC3_TRBCTL_CONTROL_DATA, true);
            ep0TrbPtr(hw, 1)[3] = ep0TrbPtr(hw, 1)[3] | DWC3_TRB_CTRL_HWO;
            __asm__ volatile("dsb sy" ::: "memory");
            ep0SetTrb(hw, 0, bufPa, len, DWC3_TRBCTL_CONTROL_DATA, false);
        } else {
            hw.ep0Trbs = 1;
            ep0SetTrb(hw, 0, bufPa, len, DWC3_TRBCTL_CONTROL_DATA, true);
        }
    } else {
        usbCacheRange(hw.dmaVirt + DMA_EP0_BUF_OFF, 64u, 1);
        hw.ep0Trbs = 1;
        // Synopsys DWC3 Databook 8.2.2: EP0 Control-Data OUT TRB size must be aligned to MaxPacketSize (64B)
        ep0SetTrb(hw, 0, bufPa, 64u, DWC3_TRBCTL_CONTROL_DATA, true);
    }
    ep0CommitHead(hw);
    ep0Start(hw, dep);
}

static void ep0StartStatus(UsbHw &hw, unsigned dep) {
    hw.ep0Stage = 3;
    hw.ep0Trbs  = 1;
    ep0SetTrb(hw, 0, hw.dmaPhys + DMA_EP0_BUF_OFF, 0u,
              hw.ep0Three ? DWC3_TRBCTL_CONTROL_STATUS3 : DWC3_TRBCTL_CONTROL_STATUS2, true);
    ep0CommitHead(hw);
    ep0Start(hw, dep);
}

static void armBulkOut(UsbHw &hw) {
    if (!hw.configured || hw.ep2Armed) return;
    usbCacheRange(hw.dmaVirt + DMA_EP2_BUF_OFF, hw.bulkMps, 1);
    volatile uint32_t *t = reinterpret_cast<volatile uint32_t *>(hw.dmaVirt + DMA_EP2_TRB_OFF);
    const uint64_t bufPa = hw.dmaPhys + DMA_EP2_BUF_OFF;
    const uint64_t trbPa = hw.dmaPhys + DMA_EP2_TRB_OFF;
    t[0] = static_cast<uint32_t>(bufPa);
    t[1] = static_cast<uint32_t>(bufPa >> 32);
    t[2] = hw.bulkMps;
    t[3] = DWC3_TRBCTL_NORMAL | DWC3_TRB_CTRL_CSP | DWC3_TRB_CTRL_ISP_IMI |
           DWC3_TRB_CTRL_IOC | DWC3_TRB_CTRL_LST;
    __asm__ volatile("dsb sy" ::: "memory");
    t[3] = t[3] | DWC3_TRB_CTRL_HWO;
    usbCacheRange(hw.dmaVirt + DMA_EP2_TRB_OFF, 64u, 1);
    uint32_t raw = 0;
    if (epCmd(hw, 2, DWC3_DEPCMD_STARTTRANSFER, static_cast<uint32_t>(trbPa >> 32),
              static_cast<uint32_t>(trbPa), 0, &raw) == 0) {
        hw.ep2RscIdx = DWC3_DEPCMD_RSC_IDX(raw);
        hw.ep2Armed  = true;
    }
}

static bool startBulkIn(UsbHw &hw, const uint8_t *data, uint32_t len) {
    if (!hw.configured || hw.ep3Busy || len == 0 || len > DMA_EP3_MAX_CHUNK) return false;
    memcpy(hw.dmaVirt + DMA_EP3_BUF_OFF, data, len);
    usbCacheRange(hw.dmaVirt + DMA_EP3_BUF_OFF, len, 0);
    volatile uint32_t *t0 = reinterpret_cast<volatile uint32_t *>(hw.dmaVirt + DMA_EP3_TRB_OFF);
    const uint64_t bufPa = hw.dmaPhys + DMA_EP3_BUF_OFF;
    const uint64_t trbPa = hw.dmaPhys + DMA_EP3_TRB_OFF;
    const bool needZlp = (len % hw.bulkMps) == 0u;
    if (needZlp) {
        volatile uint32_t *t1 = t0 + 4;
        t1[0] = static_cast<uint32_t>(bufPa);
        t1[1] = static_cast<uint32_t>(bufPa >> 32);
        t1[2] = 0u;
        t1[3] = DWC3_TRBCTL_NORMAL | DWC3_TRB_CTRL_ISP_IMI | DWC3_TRB_CTRL_IOC |
                DWC3_TRB_CTRL_LST | DWC3_TRB_CTRL_HWO;
        __asm__ volatile("dsb sy" ::: "memory");
        t0[0] = static_cast<uint32_t>(bufPa);
        t0[1] = static_cast<uint32_t>(bufPa >> 32);
        t0[2] = len;
        t0[3] = DWC3_TRBCTL_NORMAL | DWC3_TRB_CTRL_ISP_IMI | DWC3_TRB_CTRL_CHN;
    } else {
        t0[0] = static_cast<uint32_t>(bufPa);
        t0[1] = static_cast<uint32_t>(bufPa >> 32);
        t0[2] = len;
        t0[3] = DWC3_TRBCTL_NORMAL | DWC3_TRB_CTRL_ISP_IMI | DWC3_TRB_CTRL_IOC | DWC3_TRB_CTRL_LST;
    }
    __asm__ volatile("dsb sy" ::: "memory");
    t0[3] = t0[3] | DWC3_TRB_CTRL_HWO;
    usbCacheRange(hw.dmaVirt + DMA_EP3_TRB_OFF, 64u, 1);
    uint32_t raw = 0;
    if (epCmd(hw, 3, DWC3_DEPCMD_STARTTRANSFER, static_cast<uint32_t>(trbPa >> 32),
              static_cast<uint32_t>(trbPa), 0, &raw) == 0) {
        hw.ep3RscIdx  = DWC3_DEPCMD_RSC_IDX(raw);
        hw.ep3Busy    = true;
        hw.ep3ArmTick = nowMs();
        return true;
    }
    return false;
}

static bool programEp0(UsbHw &hw) {
    if(epCmd(hw, 0, DWC3_DEPCMD_DEPSTARTCFG, 0, 0, 0))return false;
    const uint32_t cfg = DWC3_DEPCFG_EP_TYPE(0) | DWC3_DEPCFG_MAX_PACKET_SIZE(64) | DWC3_DEPCFG_FIFO_NUMBER(0);
    const uint32_t ev  = DWC3_DEPCFG_XFER_COMPLETE_EN | DWC3_DEPCFG_XFER_NOT_READY_EN;
    if(epCmd(hw, 0, DWC3_DEPCMD_SETEPCONFIG, cfg, ev | DWC3_DEPCFG_EP_NUMBER(0), 0))return false;
    if(epCmd(hw, 0, DWC3_DEPCMD_SETTRANSFRESOURCE, DWC3_DEPXFERCFG_NUM_XFER_RES(1), 0, 0))return false;
    if(epCmd(hw, 1, DWC3_DEPCMD_SETEPCONFIG, cfg, ev | DWC3_DEPCFG_EP_NUMBER(1), 0))return false;
    if(epCmd(hw, 1, DWC3_DEPCMD_SETTRANSFRESOURCE, DWC3_DEPXFERCFG_NUM_XFER_RES(1), 0, 0))return false;
    hw.wr(DWC3_DALEPENA, DWC3_DALEPENA_EP(0) | DWC3_DALEPENA_EP(1));
    ep0ArmSetup(hw);
    return hw.lastCmdSt==0;
}

static void enableDataEndpoints(UsbHw &hw) {
    endDepTransfer(hw, 2, hw.ep2RscIdx, hw.ep2Armed);
    endDepTransfer(hw, 3, hw.ep3RscIdx, hw.ep3Busy);
    (void)epCmd(hw, 0, DWC3_DEPCMD_DEPSTARTCFG | DWC3_DEPCMD_PARAM(2), 0, 0, 0);
    const uint32_t ev = DWC3_DEPCFG_XFER_COMPLETE_EN | DWC3_DEPCFG_XFER_IN_PROGRESS_EN;
    // dep2 = EP1 OUT (0x01), FIFO 0
    (void)epCmd(hw, 2, DWC3_DEPCMD_SETEPCONFIG,
                DWC3_DEPCFG_EP_TYPE(2) | DWC3_DEPCFG_MAX_PACKET_SIZE(hw.bulkMps) | DWC3_DEPCFG_FIFO_NUMBER(0),
                ev | DWC3_DEPCFG_EP_NUMBER(2), 0);
    (void)epCmd(hw, 2, DWC3_DEPCMD_SETTRANSFRESOURCE, DWC3_DEPXFERCFG_NUM_XFER_RES(1), 0, 0);
    // dep3 = EP1 IN (0x81), dedicated TX FIFO 1
    (void)epCmd(hw, 3, DWC3_DEPCMD_SETEPCONFIG,
                DWC3_DEPCFG_EP_TYPE(2) | DWC3_DEPCFG_MAX_PACKET_SIZE(hw.bulkMps) | DWC3_DEPCFG_FIFO_NUMBER(1),
                ev | DWC3_DEPCFG_EP_NUMBER(3), 0);
    (void)epCmd(hw, 3, DWC3_DEPCMD_SETTRANSFRESOURCE, DWC3_DEPXFERCFG_NUM_XFER_RES(1), 0, 0);
    // dep5 = EP2 IN (0x82), dedicated TX FIFO 2, bInterval=9 -> bInterval_m1=8
    (void)epCmd(hw, 5, DWC3_DEPCMD_SETEPCONFIG,
                DWC3_DEPCFG_EP_TYPE(3) | DWC3_DEPCFG_MAX_PACKET_SIZE(16) | DWC3_DEPCFG_FIFO_NUMBER(2),
                ev | (8u << 16) | DWC3_DEPCFG_EP_NUMBER(5), 0);
    (void)epCmd(hw, 5, DWC3_DEPCMD_SETTRANSFRESOURCE, DWC3_DEPXFERCFG_NUM_XFER_RES(1), 0, 0);
    hw.wr(DWC3_DALEPENA, hw.rd(DWC3_DALEPENA) |
          DWC3_DALEPENA_EP(2) | DWC3_DALEPENA_EP(3) | DWC3_DALEPENA_EP(5));
    armBulkOut(hw);
}

static unsigned buildStringDesc(unsigned idx, uint8_t *out) {
    if (idx == 0) {
        out[0] = 4; out[1] = 3; out[2] = 0x09; out[3] = 0x04;
        return 4;
    }
    if (idx >= 4) return 0;
    const char *s = kUsbStrings[idx];
    unsigned n = 0;
    while (s[n] && n < 60) ++n;
    out[0] = static_cast<uint8_t>(2 + 2 * n);
    out[1] = 3;
    for (unsigned i = 0; i < n; ++i) {
        out[2 + 2 * i] = static_cast<uint8_t>(s[i]);
        out[3 + 2 * i] = 0;
    }
    return 2 + 2 * n;
}

static void handleEp0Setup(UsbHw &hw) {
    const uint8_t *setup = hw.dmaVirt + DMA_EP0_BUF_OFF;
    const uint8_t rt   = setup[0];
    const uint8_t req  = setup[1];
    const uint16_t val = static_cast<uint16_t>(setup[2] | (setup[3] << 8));
    const uint16_t len = static_cast<uint16_t>(setup[6] | (setup[7] << 8));

    hw.ep0Three     = (len != 0);
    hw.ep0In        = hw.ep0Three && ((rt & 0x80u) != 0u);
    hw.ep0Zlp       = false;
    hw.ep0StatusDep = hw.ep0In ? 0u : 1u;

    uint8_t *out = hw.dmaVirt + DMA_EP0_BUF_OFF;
    unsigned resp = 0;
    bool stall = false;

    if ((rt & 0x60u) == 0u) {
        switch (req) {
        case 0x00:  // GET_STATUS
            out[0] = 0; out[1] = 0; resp = 2; break;
        case 0x01:  // CLEAR_FEATURE
        case 0x03:  // SET_FEATURE
        case 0x0B:  // SET_INTERFACE
            break;
        case 0x05:  // SET_ADDRESS
            hw.wr(DWC3_DCFG, (hw.rd(DWC3_DCFG) & ~DWC3_DCFG_DEVADDR_MASK) | DWC3_DCFG_DEVADDR(val & 0x7Fu));
            break;
        case 0x06: {  // GET_DESCRIPTOR
            const unsigned dtype = val >> 8;
            if (dtype == 1u) {
                memcpy(out, kUsbCdcDeviceDesc, sizeof(kUsbCdcDeviceDesc));
                resp = sizeof(kUsbCdcDeviceDesc);
            } else if (dtype == 2u) {
                resp = usb_cdc_build_config_desc(out, hw.bulkMps);
            } else if (dtype == 3u) {
                resp = buildStringDesc(val & 0xFFu, out);
                if (!resp) stall = true;
            } else if (dtype == 6u) {  // DEVICE_QUALIFIER
                static const uint8_t kQual[10] = {10, 6, 0x00, 0x02, 0x02, 0x02, 0x00, 64, 1, 0};
                memcpy(out, kQual, 10);
                resp = 10;
            } else {
                stall = true;
            }
            break;
        }
        case 0x08:  // GET_CONFIGURATION
            out[0] = hw.configured; resp = 1; break;
        case 0x09:  // SET_CONFIGURATION
            if(val>1 || len) {stall=true;break;}
            hw.configured = static_cast<uint8_t>(val & 0xFFu);
            if (hw.configured) {
                ++hw.usbCfgCount;
                if (hw.usbCfgCount == 1u) {
                    logStage("usb: host SET_CONFIGURATION=1 complete");
                }
                enableDataEndpoints(hw);
            }
            break;
        default:
            stall = true; break;
        }
    } else if ((rt & 0x60u) == 0x20u) {
        if (req == 0x21u) {  // GET_LINE_CODING
            memcpy(out, hw.lineCoding, 7);
            resp = 7;
        } else if (req == 0x20u) {  // SET_LINE_CODING (7B OUT stage)
            if(len!=7 || (rt&0x80)) {ep0Restart(hw);return;}
            resp = len;
        } else if (req == 0x22u) {  // SET_CONTROL_LINE_STATE
            const bool prevDtr = (hw.lineState & 1u) != 0u;
            hw.lineState = val;
            if (val & 1u) {
                hw.hostReady = true;
                if (!prevDtr) hw.needReplay = true;
            } else {
                hw.hostReady = false;
            }
        } else if (req == 0x23u) {  // SEND_BREAK
        } else {
            stall = true;
        }
    } else {
        stall = true;
    }

    if (stall) {
        ep0Restart(hw);
        return;
    }
    if (!hw.ep0Three) {
        hw.ep0Stage = 2;
        return;
    }
    if (hw.ep0In) {
        if (resp > len) resp = len;
        hw.ep0Zlp = (resp != 0u) && (resp < len) && ((resp % 64u) == 0u);
        ep0StartData(hw, 1u, resp);
    } else {
        ep0StartData(hw, 0u, len > 64u ? 64u : len);
    }
}

