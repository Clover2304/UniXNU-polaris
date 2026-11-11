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
The corrected path retains the USB stream's translated mapping and disables
stage 1 in its existing context bank 4 after checking for other users. Other
streams and context banks are preserved; readback failures abort the setup.

This is a polling CDC diagnostic channel, enabled by `polaris-usb=1`.
The log is a 64 KiB in-memory ring captured after the console opens. It does
not preserve pre-console output or survive reset, and it does not implement
Apple USB pairing, a POSIX serial device, USB host mode or suspend/resume.
