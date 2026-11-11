// SPDX-License-Identifier: BSD-2-Clause-Patent
// Adapted from UniXNU-j716f; see SOURCES.md and LICENSE.
#pragma once
// --- QSCRATCH wrapper registers inside USB0 core_base ----------------------
#define QSCRATCH_REG_OFFSET           0x000F8800u
#define QSCRATCH_GENERAL_CFG          (QSCRATCH_REG_OFFSET + 0x08u)
#define QSCRATCH_HS_PHY_CTRL          (QSCRATCH_REG_OFFSET + 0x10u)
#define QSCRATCH_CGCTL                (QSCRATCH_REG_OFFSET + 0x28u)
#define QSCRATCH_SS_PHY_CTRL          (QSCRATCH_REG_OFFSET + 0x30u)
#define QSCRATCH_PIPE_UTMI_CLK_SEL    (1u << 0)
#define QSCRATCH_PIPE3_PHYSTATUS_SW   (1u << 3)
#define QSCRATCH_PIPE_UTMI_CLK_DIS    (1u << 8)
#define QSCRATCH_UTMI_OTG_VBUS_VALID  (1u << 20)
#define QSCRATCH_SW_SESSVLD_SEL       (1u << 28)
#define QSCRATCH_LANE0_PWR_PRESENT    (1u << 24)

// --- Synopsys DWC3 registers (offset from 0x0A600000) ----------------------
#define DWC3_GSBUSCFG0                0xC100u
#define DWC3_GSBUSCFG1                0xC104u
#define DWC3_GUCTL1                   0xC10Cu
#define DWC3_GCTL                     0xC110u
#define DWC3_GEVTEN                   0xC114u
#define DWC3_GSTS                     0xC118u
#define DWC3_GUCTL                    0xC11Cu
#define DWC3_GSNPSID                  0xC120u
#define DWC3_OSTS_WRAP                0xD010u
#define DWC3_GUSB2PHYCFG(n)           (0xC200u + ((n) * 0x04u))
#define DWC3_GUSB3PIPECTL(n)          (0xC2C0u + ((n) * 0x04u))
#define DWC3_GTXFIFOSIZ(n)            (0xC300u + ((n) * 0x04u))
#define DWC3_GEVNTADRLO(n)            (0xC400u + ((n) * 0x10u))
#define DWC3_GEVNTADRHI(n)            (0xC404u + ((n) * 0x10u))
#define DWC3_GEVNTSIZ(n)              (0xC408u + ((n) * 0x10u))
#define DWC3_GEVNTCOUNT(n)            (0xC40Cu + ((n) * 0x10u))

#define DWC3_DCFG                     0xC700u
#define DWC3_DCTL                     0xC704u
#define DWC3_DEVTEN                   0xC708u
#define DWC3_DSTS                     0xC70Cu
#define DWC3_DALEPENA                 0xC720u
#define DWC3_DEPCMDPAR2(n)            (0xC800u + ((n) * 0x10u))
#define DWC3_DEPCMDPAR1(n)            (0xC804u + ((n) * 0x10u))
#define DWC3_DEPCMDPAR0(n)            (0xC808u + ((n) * 0x10u))
#define DWC3_DEPCMD(n)                (0xC80Cu + ((n) * 0x10u))

#define DWC3_GCTL_DSBLCLKGTNG         (1u << 0)
#define DWC3_GCTL_CORESOFTRESET       (1u << 11)
#define DWC3_GCTL_PRTCAPDIR(n)        ((n) << 12)
#define DWC3_GCTL_PRTCAP_DEVICE       2u

#define DWC3_GUSB2PHYCFG_SUSPHY       (1u << 6)
#define DWC3_GUSB2PHYCFG_ENBLSLPM     (1u << 8)
#define DWC3_GUSB2PHYCFG_PHYSOFTRST   (1u << 31)
#define DWC3_GUSB3PIPECTL_SUSPHY      (1u << 17)

#define DWC3_DCFG_HIGHSPEED           0u
#define DWC3_DCFG_SPEED_MASK          0x7u
#define DWC3_DCFG_DEVADDR(n)          (((n) & 0x7Fu) << 3)
#define DWC3_DCFG_DEVADDR_MASK        (0x7Fu << 3)

#define DWC3_DCTL_HIRD_THRES(n)       (((n) & 0x1Fu) << 24)
#define DWC3_DCTL_CSFTRST             (1u << 30)
#define DWC3_DCTL_RUN_STOP            (1u << 31)

#define DWC3_DSTS_CONNECTSPD_MASK     0x7u
#define DWC3_DSTS_DEVCTRLHLT          (1u << 22)

#define DWC3_GSNPSID_MASK             0xFFFF0000u
#define DWC3_GSNPSID_USB3             0x55330000u
#define DWC3_GSNPSID_USB31            0x55340000u
#define DWC3_GSNPSID_USB31_3331       0x33310000u

#define DWC3_DEPCMD_SETEPCONFIG       0x01u
#define DWC3_DEPCMD_SETTRANSFRESOURCE 0x02u
#define DWC3_DEPCMD_SETSTALL          0x04u
#define DWC3_DEPCMD_CLEARSTALL        0x05u
#define DWC3_DEPCMD_STARTTRANSFER     0x06u
#define DWC3_DEPCMD_ENDTRANSFER       0x08u
#define DWC3_DEPCMD_DEPSTARTCFG       0x09u
#define DWC3_DEPCMD_CMDIOC            (1u << 8)
#define DWC3_DEPCMD_CMDACT            (1u << 10)
#define DWC3_DEPCMD_HIPRI_FORCERM     (1u << 11)
#define DWC3_DEPCMD_STATUS(x)         (((x) >> 12) & 0x0Fu)
#define DWC3_DEPCMD_PARAM(x)          (((x) & 0x7Fu) << 16)
#define DWC3_DEPCMD_RSC_IDX(x)        (((x) >> 16) & 0x7Fu)

#define DWC3_DALEPENA_EP(n)           (1u << (n))

#define DWC3_DEPCFG_EP_TYPE(n)          (((n) & 0x3u) << 1)
#define DWC3_DEPCFG_MAX_PACKET_SIZE(n)  (((n) & 0x7FFu) << 3)
#define DWC3_DEPCFG_FIFO_NUMBER(n)      (((n) & 0x1Fu) << 17)
#define DWC3_DEPCFG_XFER_COMPLETE_EN    (1u << 8)
#define DWC3_DEPCFG_XFER_IN_PROGRESS_EN (1u << 9)
#define DWC3_DEPCFG_XFER_NOT_READY_EN   (1u << 10)
#define DWC3_DEPCFG_EP_NUMBER(n)        (((n) & 0x1Fu) << 25)
#define DWC3_DEPXFERCFG_NUM_XFER_RES(n) ((n) & 0xFFFFu)

#define DWC3_TRB_CTRL_HWO             (1u << 0)
#define DWC3_TRB_CTRL_LST             (1u << 1)
#define DWC3_TRB_CTRL_CHN             (1u << 2)
#define DWC3_TRB_CTRL_CSP             (1u << 3)
#define DWC3_TRB_CTRL_TRBCTL(n)       (((n) & 0x3Fu) << 4)
#define DWC3_TRB_CTRL_ISP_IMI         (1u << 10)
#define DWC3_TRB_CTRL_IOC             (1u << 11)
#define DWC3_TRB_SIZE_MASK            0x00FFFFFFu
#define DWC3_TRB_SIZE_TRBSTS(x)       (((x) >> 28) & 0x0Fu)
#define DWC3_TRBSTS_SETUP_PENDING     2u

#define DWC3_TRBCTL_NORMAL            DWC3_TRB_CTRL_TRBCTL(1)
#define DWC3_TRBCTL_CONTROL_SETUP     DWC3_TRB_CTRL_TRBCTL(2)
#define DWC3_TRBCTL_CONTROL_STATUS2   DWC3_TRB_CTRL_TRBCTL(3)
#define DWC3_TRBCTL_CONTROL_STATUS3   DWC3_TRB_CTRL_TRBCTL(4)
#define DWC3_TRBCTL_CONTROL_DATA      DWC3_TRB_CTRL_TRBCTL(5)

#define DWC3_DEPEVT_XFERCOMPLETE      1u
#define DWC3_DEPEVT_XFERNOTREADY      3u
#define DEPEVT_STATUS_CONTROL_DATA    1u
#define DEPEVT_STATUS_CONTROL_STATUS  2u

#define DWC3_DEV_EVENT_DISCONNECT     0u
#define DWC3_DEV_EVENT_RESET          1u
#define DWC3_DEV_EVENT_CONNECT_DONE   2u
#define DWC3_DEV_EVENT_LINK_STATUS    3u



#define DWC3_DEPCFG_XFER_IN_PROGRESS_EN (1u << 9)
