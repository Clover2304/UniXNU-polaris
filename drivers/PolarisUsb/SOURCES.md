# Polaris USB diagnostic transport

The DWC3 transfer state machine, register definitions, DMA cache helpers and
CDC descriptors are adapted from Denis0x00/UniXNU-j716f at
`bf2d0f32f706adbe414551e452f1426ed7c4bd19`, files
`package/UsbDebugCdc.cpp`, `UsbDebugCdc.h`, `UsbDebugHw.h`, `UsbDebugWire.h`, `SmmuBypass.h`.
Copyright (c) 2026, UniXNU contributors. BSD-2-Clause-Patent; full license in LICENSE.

The SM8250 power/PHY/SMMU programming, framebuffer capture, unrelated peripheral
commands and reboot commands are excluded. Polaris supplies SDM845 register
resources, a small diagnostic command set and a bounded runtime console log.

Hardware references:

- Owned MIX 2S Lineage DTB (SHA256 `57a156f62ad5278599b979d0248b8be5fff9465b19f0e741b860b2677aa9f0af`):
  DWC3 `0x0a600000`, QUSB2 v2 `0x088e2000`, stream `0x740`, high-speed limit.
- Linux `067c5c24cef23d913b67eaaf71ae67e7169a6620`:
  `drivers/clk/qcom/gcc-sdm845.c`, `drivers/phy/qualcomm/phy-qcom-qusb2.c`,
  `drivers/usb/dwc3/core.h`, `drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c`
  (register semantics and firmware BYPASS-to-FAULT quirk; no Linux source copied).

The first hardware candidate stopped at stage 4 with core `5533300a`, HWPARAMS0
`4020400a`, USB stream `80000740`, S2CR `00000004` and sCR0 `002d0406`.
The second hardware candidate refused to modify shared context bank 4
(`error=7`, SCTLR `000000e0`). Its early-return CBAR/CBA2R output was not yet
populated, so the printed zeros do not establish their hardware values.

The next path recognizes an already bypassed shared context by SCTLR.M=0,
CBAR.TYPE=S1_TRANS_S2_BYPASS, and CBA2R.VA64=1, preserving other SCTLR flags.
Otherwise a shared original context is left untouched. Following the Linux
Qualcomm quirk, only the final apps context bank is considered for USB:
reuse an existing bypass, or configure it only if disabled and unreferenced
by the first 128 reliable stream groups. Extended groups are not accessed
because of the documented QHEE emulation limitation. This is a bounded
firmware handoff policy, not proof of ownership of inaccessible secure state.
Only the USB stream's S2CR.CBNDX is redirected; SMRs and other S2CRs stay
unchanged. Readbacks are checked and a failed remap restores the original
mapping and candidate context when possible. Failure diagnostics include
the actual context register values, original bank, sharing and remap status.

This is a polling CDC diagnostic channel, enabled by `polaris-usb=1`.
The log is a 64 KiB in-memory ring captured after the console opens. It does
not preserve pre-console output or survive reset, and it does not implement
Apple USB pairing, a POSIX serial device, USB host mode or suspend/resume.
