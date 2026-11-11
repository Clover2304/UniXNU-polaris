// SPDX-License-Identifier: BSD-2-Clause-Patent
// Adapted from UniXNU-j716f; see SOURCES.md and LICENSE.
static void discardUsbSession(TerminalSession &s) {
    s.input.count=0;s.input.overflow=false;
    s.reply.consume(s.reply.available());s.pending=0;
    s.hw.configured=0;s.hw.hostReady=false;s.hw.lineState=0;s.hw.needReplay=true;
}

static void handleDepEvent(TerminalSession &s, uint32_t ev) {
    UsbHw &hw = s.hw;
    const unsigned dep    = (ev >> 1) & 0x1Fu;
    const unsigned kind   = (ev >> 6) & 0x0Fu;
    const unsigned status = (ev >> 12) & 0x0Fu;

    if (dep <= 1u) {
        if (kind == DWC3_DEPEVT_XFERNOTREADY) {
            if (status == DEPEVT_STATUS_CONTROL_STATUS && (hw.ep0Stage == 2 || hw.ep0Stage == 1)) {
                ep0StartStatus(hw, dep <= 1u ? dep : hw.ep0StatusDep);
            }
            return;
        }
        if (kind != DWC3_DEPEVT_XFERCOMPLETE) return;
        if (hw.ep0Stage == 0) {
            usbCacheRange(hw.dmaVirt + DMA_EP0_TRB_OFF, 64u, 2);
            usbCacheRange(hw.dmaVirt + DMA_EP0_BUF_OFF, 64u, 2);
            handleEp0Setup(hw);
        } else if (hw.ep0Stage == 1) {
            usbCacheRange(hw.dmaVirt + DMA_EP0_TRB_OFF, 64u, 2);
            bool setupPending = false;
            for (unsigned i = 0; i < hw.ep0Trbs; ++i) {
                if (DWC3_TRB_SIZE_TRBSTS(ep0TrbPtr(hw, i)[2]) == DWC3_TRBSTS_SETUP_PENDING) {
                    setupPending = true;
                }
            }
            if (!hw.ep0In && hw.ep0Armed == 7u) {
                usbCacheRange(hw.dmaVirt + DMA_EP0_BUF_OFF, 64u, 2);
                memcpy(hw.lineCoding, hw.dmaVirt + DMA_EP0_BUF_OFF, 7);
            }
            if (setupPending) {
                ep0ArmSetup(hw);
                return;
            }
            hw.ep0Stage = 2;
        } else if (hw.ep0Stage == 3) {
            ep0ArmSetup(hw);
        }
        return;
    }

    if (dep == 2u && kind == DWC3_DEPEVT_XFERCOMPLETE) {
        hw.ep2Armed = false;
        hw.ep2RscIdx = 0u;
        usbCacheRange(hw.dmaVirt + DMA_EP2_TRB_OFF, 64u, 2);
        usbCacheRange(hw.dmaVirt + DMA_EP2_BUF_OFF, hw.bulkMps, 2);
        volatile uint32_t *t = reinterpret_cast<volatile uint32_t *>(hw.dmaVirt + DMA_EP2_TRB_OFF);
        const uint32_t rem = t[2] & DWC3_TRB_SIZE_MASK;
        const uint32_t got = (rem <= hw.bulkMps) ? (hw.bulkMps - rem) : 0u;
        const uint8_t *rx = hw.dmaVirt + DMA_EP2_BUF_OFF;
        hw.hostReady = true;
        for (uint32_t i=0;i<got;i++) {
            char line[256];
            const auto result=s.input.feed(static_cast<char>(rx[i]),line);
            if(result==PolarisLine::Ready)handleCommand(s,line);
            else if(result==PolarisLine::TooLong)s.reply.append("ERR line-too-long\n",18);
        }
        polaris_usb_stats[6]=polaris_usb_stats[6]+got;
        armBulkOut(hw);
        return;
    }

    if (dep == 3u && kind == DWC3_DEPEVT_XFERCOMPLETE) {
        polaris_usb_stats[7]=polaris_usb_stats[7]+1;
        hw.ep3Busy   = false;
        hw.ep3RscIdx = 0u;
        return;
    }
}

static void pollUsbEvents(TerminalSession &s) {
    UsbHw &hw = s.hw;
    volatile uint32_t *ring = reinterpret_cast<volatile uint32_t *>(hw.dmaVirt + DMA_EVT_OFF);
    for (unsigned guard = 0; guard < 64; ++guard) {
        const uint32_t count = hw.rd(DWC3_GEVNTCOUNT(0)) & 0xFFFCu;
        if (!count) break;
        if (count > 4096u) {
            hw.evtOff = (hw.evtOff + count) % 4096u;
            hw.wr(DWC3_GEVNTCOUNT(0), count);
            continue;
        }
        usbCacheRange(hw.dmaVirt + DMA_EVT_OFF, 4096u, 2);
        uint32_t left = count;
        while (left >= 4u) {
            const uint32_t ev = ring[hw.evtOff / 4u];
            ++hw.usbEvents;
            if (ev & 1u) {
                const unsigned type = (ev >> 8) & 0x0Fu;
                if (type == DWC3_DEV_EVENT_RESET) {
                    hw.commandContext="usb-reset";
                    ++hw.usbResets;
                    if (hw.usbResets == 1u) {
                        logStage("usb: first host USB_RESET received");
                    }
                    endDepTransfer(hw, 2, hw.ep2RscIdx, hw.ep2Armed);
                    endDepTransfer(hw, 3, hw.ep3RscIdx, hw.ep3Busy);
                    discardUsbSession(s);
                    hw.wr(DWC3_DCFG, hw.rd(DWC3_DCFG) & ~DWC3_DCFG_DEVADDR_MASK);
                    ep0Reset(hw);
                } else if (type == DWC3_DEV_EVENT_CONNECT_DONE) {
                    const uint32_t spd = hw.rd(DWC3_DSTS) & DWC3_DSTS_CONNECTSPD_MASK;
                    hw.bulkMps = (spd == DWC3_DCFG_HIGHSPEED) ? 512u : 64u;
                } else if (type == DWC3_DEV_EVENT_DISCONNECT) {
                    hw.commandContext="disconnect";
                    endDepTransfer(hw, 2, hw.ep2RscIdx, hw.ep2Armed);
                    endDepTransfer(hw, 3, hw.ep3RscIdx, hw.ep3Busy);
                    discardUsbSession(s);
                }
            } else {
                hw.commandContext="endpoint-event";
                handleDepEvent(s, ev);
            }
            hw.commandContext="runtime";
            hw.evtOff = (hw.evtOff + 4u) % 4096u;
            left -= 4u;
        }
        hw.wr(DWC3_GEVNTCOUNT(0), count);
    }
}

