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
POSIX terminal input, writable persistent XNU storage, native touch/display/GPU driver or
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

**Native XNU USB diagnostics now work on the phone** (2026-11-11,
[source/CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42973646239),
source `7e3fa6ff5fe21e5ede1d0e7d44ab3536eb846f4b`). The polling CDC channel
enumerates as `1209:0001`. The host verified ping, a random nonce round-trip,
the exact build identity and runtime logs containing the process and filesystem
self-check markers. On one phone boot, startup and ten USB/IP reconnect cycles
kept the command-error count at zero. Each cycle included an unfinished old
command to verify session cleanup. An unsupported descriptor request stalled
correctly and the next control request recovered without a reset; 256 further
echo exchanges over about 53 seconds covered 1–250 character payloads with no
new errors or reply overflow. QUSB2 is restored before endpoint initialization
when firmware leaves it unready. Ten same-image phone boots now pass the
native identity, self-check, heartbeat and zero-command-error checks: eight
instructed warm restarts, one user-reported shutdown/power-on, and the initial
boot whose entry method was not separately classified. Per-boot log hashes
are in [the boot series record](research/usb-boot-series.json).
The supported commands are `ping`, `echo`, `status`, `log`, `help`.
The 64 KiB log ring starts when the console opens; early boot and persistent
crash logs, a POSIX user terminal, USB IRQs and suspend/resume remain unqualified.

**Native UFS controller identification now passes on the phone** (2026-11-11,
[source/CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42988178206),
source `c5dbce256e9d6c1fcd3ae60e27abbb0063254565`). An opt-in probe checks
inherited PHY power/reset/bus clocks and reads six controller registers:
UFSHCI 2.1, HCE=1, both doorbells zero. The `ufs` command returns the cached
snapshot. Native USB exact-build/nonce checks and the original RAM-root,
process/tmp/stress checks pass, with an advancing heartbeat and zero USB
command errors, reply overflow or lost log bytes. This is one phone boot;
the ten-boot qualification above remains specific to the older USB baseline.
[Evidence and next steps](research/ufs-readonly-plan.md) distinguish register
identification from UFS DMA, link handoff and media I/O, which remain unverified.

**Read-only UFS queue/SMMU inventory also passes** on source `9f1401c1`
([CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42992224617)).
The `ufs-map` cached boot snapshot finds one SMR covering the mainline candidate
SID window `0x100..0x10f`: slot 5, shared bank 2, with bypass-shaped context
registers. USB uses bank 4. Both hardware samples are stable; firmware's transfer
queue remains running, its task queue stopped, and both doorbells are zero.
The scan covers all 76 exposed legacy groups (8 valid). This one phone boot also
passes the original runtime and USB checks without errors. No SMMU/UFS register
writes, firmware queue-memory reads or media commands were issued by the probe.
[Evidence](research/ufs-queue-smmu-snapshot.json) keeps actual SID identity,
queue ownership and DMA unqualified. This provided the preflight for the owned-queue NOP test below.

**One owned-queue UFS NOP now succeeds on hardware** on source `1d38b035`
([CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43004883497),
57 host tests). A new 16 KiB allocation at `0x1023bc000` supplies the transfer
list, request and response. One NOP OUT returns OCS=0 and matching NOP IN;
cache/guard checks pass and the transfer queue is stopped afterward with its
pages retained. The shared candidate bank-2 configuration is preserved. The
first candidate mistook FSR FORMAT metadata for a fault and submitted nothing;
the corrected mask preserves raw values and rejects actual fault bits.
Runtime/USB self-checks and heartbeat pass without command errors or lost logs.
[Evidence](research/ufs-nop-result.json) qualifies this one no-payload DMA path
above 4 GiB, while media reads, PRDT payloads and exclusive firmware ownership
remain unqualified. This baseline is preserved alongside the descriptor-query result below.


**Read-only DEVICE and UNIT-0 descriptor queries now pass on source `9feeaca`**
([CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43011741949),
60 host tests). Each command completes once using the stopped NOP-owned buffer;
response lengths, identifiers, guards and cleanup pass with no new SMMU fault.
The device reports UFS 2.1; unit 0 reports 4 KiB blocks and a 59,064,188,928-byte
capacity hint. Runtime self-checks and heartbeat pass without USB errors or lost
logs. [Evidence](research/ufs-query-result.json) covers QUERY metadata only;
The later PRDT/capacity and fixed-sector results below extend this baseline.

**Single-entry PRDT INQUIRY and READ CAPACITY now pass on source `88cf31d`.**
The separate data page is above 4 GiB; both commands complete once with intact
DMAs/guards and a stopped queue. Unit 0 has 14,419,968 4 KiB blocks,
59,064,188,928 bytes, matching its descriptor. Baseline self-checks and heartbeat
pass with zero USB errors or dropped logs. [Evidence](research/ufs-scsi-result.json)
qualifies metadata DMA; no sector reads or media writes have been submitted.
The Mac CI kernel and signature gate passed, with 70 local host tests and 15
local runtime checks. EFI was unchanged and reused from the prior successful
CI; current Linux CI failed at apt mirror DNS, so full CI is not claimed.

**Native sector reads and primary GPT validation now pass on source `996fb95`.**
Six single-block READ(10) commands read LUN 0 / LBA 0..5 (24 KiB total), each
matching an independent preboot reference byte for byte. Protective MBR,
primary GPT header/table CRC, capacity and partition bounds pass, together with
baseline self-checks and heartbeat, zero USB errors and zero log loss.
[Evidence](research/ufs-read-result.json) covers one boot and this fixed range;
backup GPT, general block access, filesystem mount and media writes remain
unqualified. Complete source CI, 77 local tests, 15 runtime checks and final
packaged QEMU screen checks also pass.

**Native FAT32 known-file reads now pass on source `2791907`.**
After GPT validation, a bounded reader finds `EFI/UniXNU/READTEST.BIN` within
the fixed 128 MiB ESP and reads all 8193 bytes across nine clusters. The 30
block CRCs, cluster chain and complete file bytes/SHA256 match the updated
image and independent mtools lookup. Baseline checks and heartbeat pass with
zero USB errors or log loss. [Evidence](research/ufs-fat-result.json) qualifies
one fixed path on one boot; a VFS mount, general block device, arbitrary files
and media writes remain unqualified. Complete CI, 82 host tests, 15 runtime
reports and the final packaged QEMU screen check passed.

**Native userspace reads of the fixed UFS file now pass on `abc75cf`.**
`/dev/polaris-esp-file` exposes the verified 8193-byte file through normal
`open/read/pread/close`. Nine requests complete through the event-driven worker,
with ten fresh READ(10) commands matching the actual image's block order and
CRCs. Cross-boundary reads, EOF, negative offsets, repeated reads, write refusal
and reopen checks pass; the original self-checks and heartbeat continue with
zero USB errors or log loss. [Evidence](research/ufs-node-result.json) covers
one boot and a fixed read-only character-device view. The RAM root remains;
general block access, a filesystem mount and Apple userspace remain unqualified.
Complete CI includes 86 host checks and 16 runtime reports; QEMU now exercises
the actual asynchronous queue as well.

Apple-input reuse is also being checked against the exact iPad7,1 / 17.7.11
cache. [Input and static prelink results](research/apple-userland-reuse.md)
confirm the pinned cache and absolute-pointer plan; component ABI, loading and
Apple userspace execution remain unverified.


**Default edk2 boot and USB EFI-file updates are now verified** (2026-11-11).
With the user's explicit choice, the tested firmware was installed to `boot`
and its complete partition read back; the original 64 MiB boot is backed up and
recovery is unchanged. A power-only restart reached the updated UniXNU menu
and the same `9feeaca` XNU, with native USB, self-checks, heartbeat and UFS
queries passing. SimpleInit's Mass Storage launches a bundled Linux helper
(`05f9:1234`) for host-side file replacement; it is separate from native XNU
storage. [Workflow, tools and rollback](research/uefi-usb-update.md) replace
routine fastboot/whole-ESP flashing with USB file updates and normal restart.

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

The ten-boot and USB reconnect checks have passed on the captured baseline.
[Read-only UFS controller identification](research/ufs-readonly-plan.md) now passes
on one phone boot. Read-only inventory and one owned-queue NOP DMA also pass.
DEVICE/UNIT-0 descriptors and single-entry PRDT INQUIRY/READ CAPACITY also pass.
Fixed LBA 0..5 native readback, primary GPT checks and fixed FAT32 ESP-file reads pass.
The fixed devfs file view also passes native userspace read tests. Next is a
general read-only block/filesystem interface. Apple userland,
prelinked components and native display/touch remain later stages. The full
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
原生 XNU 轮询 USB CDC 已在真机枚举为 `1209:0001`，主机完成 ping、随机串往返、构建身份核对和运行日志取回，包含原有进程与文件系统自检成功标记。
USB 基线支持 `ping/echo/status/log/help`，UFS 测试版另有 `ufs`、`ufs-map` 、`ufs-nop` 、`ufs-query` 、`ufs-scsi` 、`ufs-read` 和 `ufs-esp` 缓存诊断命令。USB 基线曾在一轮整机启动和十次 USB/IP 重连中命令错误计数保持零，每轮覆盖半条旧命令的清除；不支持的控制请求被 STALL 后，下一条正常控制请求及数据往返恢复，未新增总线复位。
随后约 53 秒完成 256 次、1～250 字符的往返，无新增错误或回复溢出。固件 PHY 未就绪时在端点初始化前恢复 QUSB2。
同镜像十轮整机启动已通过身份、自检、持续心跳及零 USB 命令错误校验：8 轮按热启动指令执行、1 轮按用户确认关机后上电、首轮进入方式未单独分类。[逐轮日志哈希](research/usb-boot-series.json)已保存；该十轮记录仍属于 `7e3fa6f` 基线。新 `c5dbce2` 已完成一轮[原生 UFS 控制器只读识别](research/ufs-readonly-plan.md)：UFSHCI 2.1，原有自检与持续心跳通过，UFS 介质访问仍待实现。
新增 `9f1401c1` 已完成一轮队列/SMMU 只读盘点：候选 SID `0x100～0x10f` 位于共享 bank 2，与 USB bank 4 不同，寄存器采样稳定；固件传输队列仍运行、两个 doorbell 均为零。原有自检和持续心跳通过，USB 错误及日志丢失均为零。[证据](research/ufs-queue-smmu-snapshot.json)已保存，作为下一轮 NOP 的前置证据。
`1d38b035` 已在一轮真机启动中完成自有队列单次 NOP：16 KiB 缓冲位于 `0x1023bc000`，高于 4 GiB；OCS=0、NOP IN/tag 和保护区校验通过，最终传输队列停止、页面保留。SMMU 配置未写，原有自检/持续心跳和 USB 正常。[结果](research/ufs-nop-result.json)只证明本次描述符/响应 DMA；PRDT、介质读取仍待验证。
日志限于控制台打开后的 64 KiB RAM 环，早期启动和持久化崩溃日志未完成。
`9feeaca` 已完成一轮只读设备/UNIT 0 描述符查询：[结果](research/ufs-query-result.json)记录 UFS 2.1、4 KiB 块及约 59.06 GB 容量线索，队列停止与原有自检、心跳和 USB 均通过；PRDT、SCSI 容量与实际扇区读取仍待验证。目标 Apple 输入与已有预链接工具的静态复用核对见[盘点](research/apple-userland-reuse.md)。

`88cf31d` 已在普通重启中通过单项 PRDT INQUIRY/READ CAPACITY：4 KiB 块、59,064,188,928 字节容量与描述符一致，高于 4 GiB 的独立数据页 DMA、保护区、队列停止和原有自检/心跳通过，USB 错误与日志丢失为零。[结果](research/ufs-scsi-result.json)仅覆盖元数据；下一轮为有界扇区/GPT 读回。Mac CI 内核通过，70 项本地主机测试、15 项本地运行验收通过；Linux CI 因软件源 DNS 故障未通过，EFI 复用来源已明确记录。

`996fb95` 已通过一轮原生 UFS 扇区读取：LUN 0/LBA 0～5 共 24 KiB，逐字节匹配预启动参考，保护 MBR、主 GPT 头/表 CRC、容量与分区边界通过。原有自检、心跳和 USB 正常，未写介质。[结果](research/ufs-read-result.json)仅覆盖固定范围；完整 CI、77 项本地主机测试、15 项运行验收与最终打包画面通过。下一步为已知 ESP 文件读回及只读块设备/文件系统。

`2791907` 已通过固定 FAT32 文件的原生读取：`EFI/UniXNU/READTEST.BIN` 共 8193 字节、9 簇，30 个读取块的 CRC32、簇链及完整内容/SHA256 与实际更新镜像和独立 mtools 结果一致。原有自检、心跳和 USB 正常，未写介质。[结果](research/ufs-fat-result.json)仅覆盖固定路径的有限解析器；下一步接只读块设备/VFS。完整 CI、82 项主机测试、15 项运行报告和最终打包画面通过。


`abc75cf` 已通过固定 UFS 文件的 devfs 访问：9 个请求完成，实际 10 条 READ(10) 的顺序和块 CRC 与实际 ESP 一致；顺序/偏移读、跨边界、EOF、重复读、写入拒绝和重开检查通过，累计返回 20502 字节。原有自检和持续心跳通过，USB 错误、溢出和日志丢失为 0。[结果](research/ufs-node-result.json)仅覆盖固定只读字符设备视图，仍使用 RAM 根。事件唤醒修正解决了本轮首个请求未认领的失败；具体旧自旋/定时器条件未单独测定。

Apple 用户态、用户态终端输入、通用 UFS 文件系统挂载、介质写入、触摸和 GPU 尚未接入。

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
