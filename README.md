# UniXNU on Xiaomi Mi MIX 2S (polaris)

项目验收、当前证据和后续实施顺序见 [验收与基准测试计划](research/bringup-test-plan.md)。

Device port for SDM845 / Snapdragon 845 / Adreno 630 on UniXNU's Darwin 23
architecture (XNU 10063, source-kernel platform milestone). The retired
ABL-shim chain ([ios16_on_polaris_mix2s](https://github.com/luuu20309/ios16_on_polaris_mix2s),
XNU 8796 / iOS 16) is the measured-hardware reference for this tree.

**A resident own PID 1 is verified on real hardware** (temporary `fastboot boot`,
2026-11-11). Renegade firmware → UniXNU ESP on userdata → DarwinBoot → the
single-core source XNU now mounts a mockfs RAM root, admits an explicitly named
ad-hoc-signed static init, and passes getpid/sysctl, bounded 4 KiB RAM-file I/O
and standard-descriptor checks. The user reported `POLARIS_IO_OK` and a changing
`POLARIS_IO_LIVE` counter; this milestone has no captured hardware register dump
or photograph. The C entry aligns the raw process stack before its first stack
access, fixing the real-phone SIGBUS that the QEMU run did not expose.

The framebuffer is inherited from firmware. There is no Apple userland,
interactive input, persistent XNU storage, native touch/display/GPU driver or
daily-usable system. The named-root and second-process stage is also verified by the user-reported
phone screen: `POLARIS_CHILD_OK pid=2 ppid=1`,
`POLARIS_PROC_OK child=2 exit=42 reaped=1 cow=OK`, and a changing
`POLARIS_PROC_LIVE` counter. Init forks and execs the independently signed
`/bin/probe`, the child reads `/etc/message` and updates shared RAM state,
then init reaps exit status 42 and checks private-memory copy-on-write.
[Source build and rejection checks](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42943892037)
passed before the temporary physical boot. The tree has ten boot-defined entries
(five directories, five files). The bounded `/tmp` stage passes
[CI and QEMU](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42950190208):
runtime regular-file creation, readdir, seek, append, truncate, unlink with
open descriptors, and capacity recovery. There are 32 RAM slots of 4 KiB,
with at most 16 linked names. The user also confirmed `POLARIS_TMP_CHILD_OK`,
`POLARIS_TMP_OK` and a continuously changing tick on the phone after temporary boot.
Runtime directories, rename,
concurrent filesystem qualification and persistent storage remain unverified.

The process stress stage also passes [CI and QEMU](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42953408390):
200 serial children plus 100 batches of eight children, exact fork/exec/wait
results, inherited descriptors, exit-time temporary-file cleanup and four
sampled COW pages. Quiescent process/task counts return to baseline with an
empty zombie list. Whole-kernel memory leak freedom is not qualified.
The user confirmed `POLARIS_STRESS_OK`, `POLARIS_STRESS_CLEAN` and a continuously
changing tick on the phone after temporary boot. Internal kernel resource
counts are QEMU evidence; the physical acceptance uses the program's self-checks.

## English

### What "transfer from ABL" means here

The old chain booted XNU 8796 through Android's ABL: a boot image with a
custom shim that inherited ABL's framebuffer and UFS state, a forged iPhone X
(D22AP) identity in the ADT, and 93 patches against the SWEET machine
configuration. That chain reached the iOS 16 lock screen with touch — evidence
that belongs to that tree only.

This repository moves the port onto UniXNU's build driver and boot chain:

| Aspect | Old ABL-shim chain | This repository |
| --- | --- | --- |
| Kernel | XNU 8796, MACHINE_CONFIG SWEET, darwin-xnu-build | XNU 10063, BCM2837 4 KiB pages, `unixnu` build driver |
| Build | macOS CI wrapping blacktop/darwin-xnu-build | `scripts/build-platform-kernel.py` on UniXNU `37987aea` |
| Boot | boot.img → custom shim → kernel | edk2-msm (Renegade) firmware → UniXNU → DarwinBoot → payload kernel.macho |
| ADT | iPhone X identity, ABL framebuffer/UFS nodes | platform ADT, `darwinboot-memory-range`, no Apple identity |
| Root | iOS 16 userland on UFS/HFS+ | named mockfs RAM root, own init and child; bounded RAM writes |
| CPU | cpus=4 proven (A55 0-3); A75 4-7 never observed | same topology; prefix check migrated; scheduler proven on hardware |

### Implemented and verified

- **Host**: polaris platform ADT generator, binary-ABI tests, fail-closed CPU
  prefix check (migrated from the old chain), clang test suites. CI green.
- **EFI/QEMU**: UniXNU EFI apps and the DarwinBoot override build and pass ten
  QEMU cases (menu, hand-off, ARM64 entry with Darwin 23 boot_args, allocation
  window, refusals). CI green.
- **Source kernel**: builds on the macOS runner; the QEMU native check proves
  CPU registration and advancing timer IRQs on every build. CI green.
- **Real hardware** (photographed evidence, user-approved temporary boots):
  Renegade edk2-msm polaris firmware boots; UniXNU menu runs from the ESP
  flashed to userdata; DarwinBoot validates and hands off
  (`va=0xfffffff007004000 pa=0x100004000`, `video base=0x9d400000 1080×2160`);
  the XNU kernel executes through arm_init, the scheduler, the STARTUP chain,
  the corecrypto FIPS POST, bsd_init and IOKit, and stops at the designed
  root-mount boundary. bsd_init cannot run without working timer interrupts,
  so the real-phone GIC/PPI-19 timer path is established by execution.
- **Diagnostic tooling built during bring-up**: entry-probe stage bars and
  on-screen panic for the phone (caves relocated into verified zero windows),
  on-demand kernel disassembly in CI, payload ESP assembly with automatic
  FIPS-HMAC re-sealing.

### Two UniXNU-wide bugs found and fixed here

1. **The BCM2837 fixed panic-log address (0x3c0fc000) bus-errors on SDM845**:
   every non-Raspberry-Pi hardware port silently dies in `PE_create_console`
   while QEMU tolerates the read. Fix: `CONFIG_XNU_DT_PANIC_REGION=y` (the
   device-tree probe from UniXNU's own patch).
2. **Post-link kernel patching invalidates the FIPS POST integrity HMAC**:
   any tool that rewrites `__text` after the base build's seal must re-run
   `seal-source-crypto-kernel.py`; `make-payload-esp.py` does this now.

### Boot-image recipe (Route A)

`fastboot flash userdata esp.img` (a 128 MiB FAT32 ESP with the boot manager,
DarwinBoot, the kernel and the polaris ADT) plus `fastboot boot
boot-polaris.img` (Renegade firmware, built from
[the edk2-msm fork](https://github.com/luuu20309/edk2-msm)). All writes were
user-approved; boot/modem/persist partitions untouched. Details in
[NOTES.md](NOTES.md); firmware background in
[research/uefi-firmware-plan.md](research/uefi-firmware-plan.md).

### Repository layout

Standard UniXNU device repository: `board.toml`, `configs/polaris_defconfig`,
`drivers/ArmCpu` (timer PPI from the ADT + the heartbeat probe),
`drivers/DarwinBoot` (upper-bank allocation window), `scripts/` (ADT, ESP
assembly, platform kernel build, probes), `tests/`, CI workflows for host/EFI,
kernel+QEMU and ESP images. Pinned upstream revisions in
[upstream.json](upstream.json).

### Next stages

A root for the kernel (ramdisk/mockfs first, UFS later), then the prelink and
Apple-identity decision, then touch/display as Darwin 23 drivers. The full
history of both chains is in [docs/HISTORY.md](docs/HISTORY.md).

## 简体中文

### 现状

**已在真机运行自制 PID 1 和独立子程序**（2026-11-11，`fastboot boot` 临时引导）。
RAM 根挂载、固定文件读写、fork/exec/exit/wait、退出状态和单个 COW 自检已由用户回报画面通过，心跳持续变化。
当前为单核、受控的 1 GiB 内存窗口；标准输入仍为自测管道，显示继承固件 framebuffer。

新增 `/tmp` 动态普通文件已通过 CI 和 QEMU，包括创建、目录遍历、追加、截断、删除后保留打开的描述符和容量回收。
用户已确认真机出现 `TMP_CHILD_OK`、`TMP_OK`，且 tick 持续变化；本轮自检通过。
完整清单和证据见[验收与基准测试计划](research/bringup-test-plan.md)。
1,000 次进程创建/执行/回收（200 串行、800 个分批子进程）已通过 CI 和 QEMU，覆盖 0～63 号父进程描述符、退出时临时文件回收和四个 COW 页抽样。
用户已确认真机出现 `STRESS_OK`、`STRESS_CLEAN`，且 tick 持续变化，本轮程序自检通过。
内核内部资源计数仅在 QEMU 取证；完整内存泄漏排查仍未完成。
Apple 用户态、交互输入、原生 UFS、触摸和 GPU 尚未接入。

### 本次移植期间发现并修复的两个通用 bug

1. UniXNU 的 BCM2837 机器配置把树莓派 SoC 的固定 panic-log 地址
   (0x3c0fc000)编进内核,`PE_create_console` 无条件访问;QEMU 宽容而
   **任何非树莓派真机都会静默死**。修复:启用设备树 panic-region 探测。
2. 内核链接后修改 `__text`(探针等)会使 FIPS POST 完整性 HMAC 失配;
   ESP 组装流程现在在修改后自动重新封印。

### 构建与复现

```sh
# 本地检查(仅 Python)
python3 -m unittest discover -s tests -v

# CI:内核 + QEMU native check + ESP 镜像(esp-image / disasm 按需)
# 真机:esp.img 刷 userdata + fastboot boot 固件,详见 NOTES.md
```

### 来源

板级测量与历史:[ios16_on_polaris_mix2s](https://github.com/luuu20309/ios16_on_polaris_mix2s) ·
完整迭代史:[docs/HISTORY.md](docs/HISTORY.md) · 移植模板:UniXNU-sweet ·
基线:[UniXNU](https://github.com/UniXNU/UniXNU) `37987aea`,BSD-2-Clause-Patent。
不包含 Apple 二进制、Qualcomm 固件、设备转储或序列号。
