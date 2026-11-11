# Polaris USB diagnostic transport

## Opt-in UFS register snapshot

`UfsProbe.h` is an original bounded read-only probe, carried by the existing
USB worker so it can return one cached snapshot without a second transport.
It has a read callback and no hardware-write or command-submission interface.
`polaris-ufs-probe=1` is independent of USB enablement and defaults off. The
`ufs` command returns the cached boot snapshot; it does not repeat MMIO.

Resources and semantics are cross-checked against the owned Lineage DTB above
and Linux `067c5c24cef23d913b67eaaf71ae67e7169a6620`:

- [sdm845.dtsi](https://github.com/torvalds/linux/blob/067c5c24cef23d913b67eaaf71ae67e7169a6620/arch/arm64/boot/dts/qcom/sdm845.dtsi):
  internal UFS at `0x01d84000`, span `0x2500`, UFS PHY clocks/domain/reset.
- [gcc-sdm845.c](https://github.com/torvalds/linux/blob/067c5c24cef23d913b67eaaf71ae67e7169a6620/drivers/clk/qcom/gcc-sdm845.c):
  GCC base `0x00100000`; PHY BCR/GDSCR/CFG/AXI/AHB offsets
  `0x77000/04/08/0c/10`, aggregate PHY AXI `0x82024`.
  The distinct UFS CARD domain at `0x75000` is not used.
- [gdsc.c](https://github.com/torvalds/linux/blob/067c5c24cef23d913b67eaaf71ae67e7169a6620/drivers/clk/qcom/gdsc.c):
  `POLL_CFG_GDSCR` power-up-complete at GDSCR+4 bit 16; SW_COLLAPSE bit 0.
- [ufshci.h](https://github.com/torvalds/linux/blob/067c5c24cef23d913b67eaaf71ae67e7169a6620/include/ufs/ufshci.h):
  CAP/VER/HCS/HCE and the two doorbells. These six registers alone are read.

The probe conservatively requires all three AXI/AHB branches enabled and
not halted. It skips controller MMIO otherwise, rather than modifying power,
reset, clocks, interrupts, queues or SMMU. A second GCC snapshot detects a
changed handoff. This assumes firmware leaves a stable accessible domain;
it is not an exception-recovery mechanism. A valid register snapshot does
not establish DMA, stream ownership, link readiness, LUN identity or media I/O.
Mainline describes stream `0x100` with mask `0xf`; this is a reference for
later investigation, not a hardware-verified SMMU assignment for this port.

`UfsInventory.h` adds a separately gated `polaris-ufs-inventory=1` cached
inventory (`ufs-map`). It reads IS/IE, UTRL/UTMRL base addresses, doorbells,
run/stop, HCS and HCE twice without following the queue pointers. Power and
clock gates are checked before and after. It also reads the apps SMMU at
`0x15000000`, already mapped for USB, through a read-only callback. Register
semantics are checked against the same Linux revision's
[arm-smmu.h](https://github.com/torvalds/linux/blob/067c5c24cef23d913b67eaaf71ae67e7169a6620/drivers/iommu/arm/arm-smmu/arm-smmu.h).
The supported geometry is legacy stream matching, 4 KiB pages and no more
than 128 banks. At most the first 128 stream groups are scanned; additional
groups are explicitly marked unobserved. Extended-ID mode is rejected.

Only SMRs intersecting the mainline candidate window `0x100..0x10f` are
reported with their S2CR and, for translated entries, SCTLR/CBAR/CBA2R.
Reference counts are scoped to the scanned valid entries. A second read of
the same registers checks visible stability. No clock, reset, interrupt,
fault, queue or SMMU register is written; no queue memory, translation tables
or media are read. Even a stable shared bypass-shaped context and zero
doorbells do not prove the real UFS stream ID, DMA, exclusive ownership or
firmware quiescence. Those remain separate transfer/handoff milestones.

## Owned-queue NOP

`UfsNop.h` and `polaris-ufs-nop=1` add exactly one NOP OUT on newly allocated
16 KiB contiguous pages in the controlled upper-bank window. `ufs-nop` returns
the cached result. This requires the separately enabled probe/inventory and
the observed stable bank-2 shared bypass; SMMU registers stay unchanged.
The only UFS writes are UTRLRSR, UTRLBA low/high and slot-0 UTRL doorbell.
Interrupt enables/status, task-list registers, HCE, reset, clocks and PHY are
preserved. After checking idle doorbells and stopping the inherited transfer
queue, the code installs the owned list, runs one NOP, checks OCS/NOP IN/tag,
guards and context stability, then requests queue stop. It retains the new
pages and queue address until reboot on success or failure. It neither restores
firmware buffer pointers nor frees possibly DMA-visible memory after a timeout.

The original implementation uses register/packet semantics from the same pinned
Linux revision's [ufshci.h](https://github.com/torvalds/linux/blob/067c5c24cef23d913b67eaaf71ae67e7169a6620/include/ufs/ufshci.h),
[ufs.h](https://github.com/torvalds/linux/blob/067c5c24cef23d913b67eaaf71ae67e7169a6620/include/ufs/ufs.h)
and [ufshcd.c](https://github.com/torvalds/linux/blob/067c5c24cef23d913b67eaaf71ae67e7169a6620/drivers/ufs/core/ufshcd.c):
32-byte UTRD, storage command type 1, interrupt-command bit 24, no data/crypto,
invalid initial OCS 15, doubleword response offsets/length, NOP OUT/IN 0x00/0x20.
There is no PRDT payload. Command/response occupy separate 512-byte areas.
The existing ARM64 cache helper cleans+invalidates the full owned allocation
before DMA and invalidates it after the doorbell clears. A host model maintains
separate CPU/device memory and injects readback, completion, tag, OCS, guard,
cache and timeout failures. Successful NOP DMA does not qualify media payloads,
the exact active SID or exclusive firmware quiescence.

The first NOP candidate stopped before any UFS writes because bank-2 FSR read
`0x400`. The pinned `arm-smmu.h` defines FSR[10:9] as FORMAT; these bits are
not part of `ARM_SMMU_CB_FSR_FAULT` (`0xc00001fe`). Both kernel and evidence
grader now test that fault mask while preserving raw FSR values. FORMAT-only
values 0/0x200/0x400/0x600 are covered alongside every real context fault bit.

## Read-only descriptor queries

`UfsQuery.h` reuses the NOP-owned stopped queue, UTRD layout and explicit cache
callbacks. `polaris-ufs-query=1` additionally enables one READ_DESCRIPTOR for
DEVICE (IDN 0) and one for UNIT index 0 (IDN 2), selector 0. The request is
QUERY REQUEST 0x16, STANDARD READ REQUEST 0x01, opcode 0x01, maximum length
255. Descriptor data arrives after the 32-byte QUERY RESPONSE UPIU (0x36),
inside the existing 512-byte response area; there is no PRDT or sector I/O.
The frozen Linux revision's `ufshcd.c` query composition/copy functions and
[scsi_bsg_ufs.h](https://github.com/torvalds/linux/blob/067c5c24cef23d913b67eaaf71ae67e7169a6620/include/uapi/scsi/scsi_bsg_ufs.h)
provide the header/OSF byte layout. `ufs.h` supplies descriptor field offsets.

Each command checks ownership/base/status before touching memory, accepts only
these two read IDs, performs at most one doorbell submission, and requests stop
after completion or failure. It checks OCS, response type/result/tag, opcode,
IDN/index/selector, segment/OSF lengths, descriptor bLength/type, unit geometry
and guards. OSF length must cover actual descriptor bytes and fit the request.
No string descriptor, device flag/attribute/configuration write, RPMB, queue
pointer traversal or automatic retry is supported. `ufs-query` returns a cached
boot result. Public summaries omit raw descriptor bytes and retain only hashes
and necessary capability fields; UNIT geometry is a hint until SCSI validation.

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
  `drivers/usb/dwc3/core.h`, `drivers/usb/dwc3/dwc3-qcom.c`,
  `drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c`
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

The next hardware report confirmed CBA2R `00000001`: bank 4 is already a
usable shared bypass and is retained without writes. IDR0/1/2 were
`4c017eac/5000002d/00005555` (64 global pages, 45 banks, extended stream
groups limited to the first 128). The controller then reached endpoint
initialization and timed out with `cmd=-1`; HWPARAMS0/1/6/7 were
`4020400a/0162493b/07ea0020/03080a65`, and FIFO0 read back `00000040`.
The FIFO planner accepts those exact parameters in a host regression.

For the HS-only owned DT profile, select the qcom UTMI-as-PIPE clock path
explicitly, using the sequence documented in Linux `dwc3-qcom.c` at the
same revision above. SDM845's PIPE branch uses BRANCH_HALT_SKIP; its old
bit-31-only running test is not reliable. Enable GCC AHB2PHY `0x6a004`
before PHY register accesses, and clear USB2 PHY suspend settings again
after device reset. Endpoint initialization stops at its first command
failure and preserves the command/raw value. A startup command timeout or
reset timeout permits one QUSB2 reinitialization and full device reset,
reusing the same DMA arena; CORE_READY is polled for at most 10 ms.
Failures print clock and read-only SMMU fault context too. Those fault
registers may include earlier firmware activity and are not cleared.
Hardware acceptance for source `638a344d865375c209f13f6109484eee7fd7d5fe`
completed on 2026-11-11: the first EP0 SETEPCONFIG timed out, then QUSB2
reinitialization and full device reset reached RUN and CDC enumeration.
The host verified nonce round-trip, exact build identity and an 11,100-byte
runtime log response with zero dropped bytes and the stress-test checkpoints.
Direct USB logging read IDR0 `4c017e4c` (76 groups), correcting the earlier
user transcription `4c017eac`; bank 4 remained an unchanged shared bypass.
Ten further nonce/status samples over about ten seconds retained three
historical command errors and two bus resets with no reply overflow.
This qualifies basic polling diagnostic transport on one physical boot.
Zero-error startup, repeated boots/reconnects, USB IRQs, isolation, throughput
and long-term or suspend/resume reliability remain unqualified. Evidence
scope and hashes are recorded in `research/bringup-test-plan.md`.

Follow-up on that same running kernel used two USB/IP detach/reattach cycles.
Both restored nonce/build/log verification, but reset counts 2/4/6 tracked
command errors 3/5/7. This is evidence of reset-related error growth, not
proof of the failing command. Linux `drivers/usb/dwc3/gadget.c` and `ep0.c`
at the revision above retain an already active EP0 SETUP transfer on reset
and use SETSTALL without CLEARSTALL for control-endpoint stall/restart.
The next candidate follows that behavior, clears incomplete session input
and unsent replies at reset/disconnect, and records the most recent command
error with its event context. It also checks QUSB2 CORE_READY before issuing
the first endpoint command; the same bounded PHY restore is used if not ready.
These follow-up changes passed hardware acceptance on source
`7e3fa6ff5fe21e5ede1d0e7d44ab3536eb846f4b`: startup reported no endpoint
command errors after restoring the unready PHY before endpoint setup.
Ten USB/IP reconnect cycles with partial command injection retained zero
command errors and verified nonce/build/log responses. An unsupported control
descriptor request stalled, followed by a successful normal descriptor read
and nonce exchange with no new reset/error. Another 256 echo exchanges over
about 53 seconds covered 1–250 characters and retained zero command errors
and reply overflow. That initial acceptance was followed by ten same-image
phone boots, each verifying native USB identity, self-check markers, changing
heartbeat and zero command errors. Eight followed warm-restart instructions,
one was shutdown/power-on per the user, and the first entry method remains
unclassified. Per-boot hashes are in `research/usb-boot-series.json`.
Long-term qualification remains pending. Earlier bridge reattach delay and
the two failing-baseline reconnects are retained in the test plan.

This is a polling CDC diagnostic channel, enabled by `polaris-usb=1`.
The log is a 64 KiB in-memory ring captured after the console opens. It does
not preserve pre-console output or survive reset, and it does not implement
Apple USB pairing, a POSIX serial device, USB host mode or suspend/resume.
