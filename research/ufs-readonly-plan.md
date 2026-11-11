# Polaris 原生 UFS 只读识别与后续计划

当前源码 `88cf31dc88bcdc966824c3ecfbfc7eec4ed32081` 已在真机完成自有队列 NOP、DEVICE/UNIT 0 READ_DESCRIPTOR，以及独立 PRDT 数据页上的 INQUIRY 和 READ CAPACITY(10)。4 KiB 块、59,064,188,928 字节容量与描述符一致，原有 RAM 根、进程/文件系统压力自检、变化心跳与 USB 均通过。实际扇区读取、精确硬件 SID 和长期独占所有权仍未验证。

首轮控制器识别 `c5dbce2`、只读队列/SMMU 盘点 `9f1401c` 和十轮启动的 USB 基线 `7e3fa6f` 均保留。当前 NOP 成功镜像只验证一轮启动、一次命令；此前 `5670f6c` 被 FSR 判断误拦，未提交命令，记录单独保留。

## 已知资源

资源来自自有 MIX 2S 的 Lineage DTB，SHA256 为 `57a156f62ad5278599b979d0248b8be5fff9465b19f0e741b860b2677aa9f0af`。完整 DTB 和设备序列号未提交。

| 项目 | 静态证据 |
| --- | --- |
| 控制器节点 | `ufshc@1d84000`，`qcom,ufshc` |
| MMIO | 基址 `0x01d84000`，范围 `0x2500` |
| 中断 | GIC SPI 265，对应 INTID 297 |
| 链路 | 每方向 2 lanes |
| 时钟名称 | core、bus_aggr、iface、unipro、ice、ref，以及 TX lane0、RX lane0/1 sync |
| 电源与复位 | DT 提供 HBA、VCC、VCCQ2 等电源和 core_reset；控制器总线电源/时钟继承状态已核对，完整 PHY/介质供电仍待验证 |
| DMA stream | 该旧版控制器节点没有 `iommus` 属性，SID 尚未建立；USB 的 SID `0x740` 只用于 USB |

寄存器语义参考固定 Linux 修订版的 [include/ufs/ufshci.h](https://github.com/torvalds/linux/blob/067c5c24cef23d913b67eaaf71ae67e7169a6620/include/ufs/ufshci.h)。GCC、GDSC 与主线设备树已交叉核对；后续传输还需核对 Qualcomm UFS 和 PHY 初始化实现。

## 第一轮：控制器寄存器识别

沿用已通过的 RAM 根和 USB 诊断通道，增加独立启用的 UFS 探测。先核对固件留下的电源、时钟状态；确认可访问后采集以下寄存器，输出构建身份及原始值，并通过 USB 取回日志。

| 寄存器 | 偏移 | 首轮用途 |
| --- | --- | --- |
| CAP | `0x00` | 命令槽位、64 位 DMA 寻址等能力 |
| VER | `0x08` | UFSHCI 版本 |
| HCS | `0x30` | 控制器/链路状态 |
| HCE | `0x34` | 控制器启用状态 |
| UTRL doorbell | `0x58` | 固件遗留传输队列活动 |
| UTMRL doorbell | `0x78` | 固件遗留任务管理活动 |

第一轮仅做状态读取；若供电、时钟或继承状态不明，则报告具体状态并保留现有启动。验收要求：控制器寄存器值合理、USB 日志可取回，原有 PID 1、文件系统和进程压力自检仍通过。达到这一点只记为控制器识别，F2 的介质读取仍待验证。

首版实现位于 `drivers/PolarisUsb/UfsProbe.h`，由现有 USB worker 执行一次，`ufs` 命令只取回缓存快照。`make-payload-esp.py --usb-console --ufs-probe` 才会加入 `polaris-ufs-probe=1`；普通 USB 和 QEMU 验收镜像保持关闭。CI 会额外生成 `polaris-ufs-esp`。

GCC 检查使用内部 UFS PHY 域：BCR `0x77000`、GDSCR `0x77004`、CFG_GDSCR `0x77008`、AXI/AHB `0x7700c/10`、aggregate AXI `0x82024`。必须无 collapse、无 reset、CFG power-up-complete 有效，并且三个总线分支已开启且未 halt，才映射和读取控制器。快照后再次检查 GCC 是否变化。该检查依赖固件保留的稳定可访问状态，不能恢复总线访问异常。寄存器及来源详见 [SOURCES.md](../drivers/PolarisUsb/SOURCES.md)。

主机 ASan/UBSan 测试覆盖关闭探测、供电/复位/时钟拦截、控制器地址白名单、映射失败、状态变化、无效寄存器，以及原始 USB 证据的构建身份和计数校验。状态不满足时只记录跳过，不算控制器识别通过。首轮 c5dbce2 已通过 51 项主机 unittest、定时器/加载窗口检查、完整 CI 和一轮真机验收。

## 真机首轮结果（2026-11-11 08:51 UTC）

[完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42988178206)通过；打包镜像在关闭 USB/UFS 参数的本地 QEMU 中显示 STRESS_OK/CLEAN，tick 5→8。新 ESP 写入 userdata 后临时启动固件，主机通过原生 CDC 校验精确源码身份、随机 nonce、完整自检日志和两次采样间继续增长的 tick。证据摘要见 [ufs-register-snapshot.json](ufs-register-snapshot.json)，本地原始响应在 `artifacts/ufs-register-phone-c5dbce2/`。

| 项目 | 原始值 |
| --- | --- |
| GCC BCR / GDSCR / CFG | `00000000 / f8222000 / 00070400` |
| GCC AXI / AHB / aggregate AXI | `00004221 / 00000001 / 00000001` |
| CAP / VER | `1587031f / 00000210` |
| HCS / HCE | `0000010f / 00000001` |
| UTRL / UTMRL doorbell | `00000000 / 00000000` |

控制器可识别为 UFSHCI 2.1，CAP 声明 32 个传输槽位与 64 位寻址能力；后者当时只是能力位，尚未验证 UFS DMA。12 次 GCC 读取（前后各 6 次）一致，控制器仅读取上述 6 个寄存器，UFS MMIO 写入和介质命令提交均为零。缓存快照的 SHA256 为 `bd5af264d9bfcca8dc50b27665f8e2ff0c2f4dbec0b2ebe8e4c8b378d5b0b3aa`。

原有 IO、子进程、`/tmp` 和 1,000 次进程回收自检全部通过；最终日志包含 tick 1～3，第二次采样较第一次继续增长；USB 命令错误、回复溢出和日志丢失均为零。最终运行日志响应 3,126 字节，SHA256 `1560759cc3ff473e50746f29e3c688283ea716ab4bb5e8b48351ac9adc84ad91`。两个 doorbell 为零只表明采样时没有挂起位，不证明队列所有权、固件完全退出或链路可重新初始化。

## 后续只读传输

只读盘点已完成，下面记录观察到的候选映射和固件队列配置。主线设备树提供 `0x100 / mask 0xf` 线索，尚未作为本机的已验证 SID；不得套用 USB 的 `0x740`。自有队列和 NOP DMA 已按第三轮记录通过；后续先实现设备描述符查询，再分别验证带 PRDT 的 LUN/容量识别和有界块读取。已知测试区域可使用当前 ESP 的固定内容作读回与哈希比对；每项单独记录实际设备结果。当前阶段不实现介质写入、RPMB 操作或持久根文件系统。

### 第二轮：只读队列与 SMMU 盘点（真机通过）

`--usb-console --ufs-probe --ufs-inventory` 独立启用第二层探测；普通 USB、第一轮 UFS 以及 QEMU 验收镜像不加入该开关。`ufs-map` 取回启动时缓存。队列配置采样两次，包括 IS/IE、两套队列基址/doorbell/run-stop、HCS/HCE；不跟随地址读取固件内存。

SMMU 使用已验证的 4 KiB 几何布局，先读取 CR0/IDR0/1/2，再扫描最多 128 组 SMR 和已启用组的 S2CR。只记录与候选 `0x100～0x10f` 有交集的项，以及相关 SCTLR/CBAR/CBA2R 和扫描范围内的共享引用数。第二遍核对相同访问集是否变化；不清除 fault/中断，不改 context 或流映射。截断、歧义匹配和未稳定状态均单独报告，不推断可进行 DMA。该实现已通过 [完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42992224617)、54 项主机 unittest、定时器/加载窗口检查，以及一轮真机启动。

#### 第二轮真机结果（2026-11-11 09:21 UTC）

主机通过精确源码身份、随机 nonce、完整基线自检与变化心跳校验，取回 `ufs` 和 `ufs-map` 缓存。打包镜像的本地 QEMU 画面显示 STRESS_OK/CLEAN、tick 6→9；外设诊断在 QEMU 中关闭。真机原始响应位于 `artifacts/ufs-inventory-phone-9f1401c/`，公开摘要见 [ufs-queue-smmu-snapshot.json](ufs-queue-smmu-snapshot.json)。

| 项目 | 原始值与范围 |
| --- | --- |
| 传输队列 UTRLBA | `0x00000000c989a000` |
| UTRL doorbell / run-stop | `00000000 / 00000001` |
| 任务队列 UTMRLBA | `0x00000000c989b000` |
| UTMRL doorbell / run-stop | `00000000 / 00000000` |
| IS / IE / HCS / HCE | `00000400 / 00000000 / 0000010f / 00000001` |
| SMMU CR0 | `002d0406` |
| SMMU IDR0 / 1 / 2 | `4c017e4c / 5000002d / 00005555` |
| 扫描范围 | 76/76 组，无截断；8 组有效 |
| 候选匹配 | slot 5，SMR `800f0100`，覆盖 `0x100～0x10f`，无重叠匹配 |
| S2CR / bank | `00000002` / bank 2 |
| SCTLR / CBAR / CBA2R | `000000e0 / 00010000 / 00000001` |
| 共享引用 | 另有 1 组有效条目引用 bank 2；不推断其对应设备 |

启动时的两次硬件采样一致，GCC 读取 12 次、队列/状态读取 24 次、SMMU 读取 182 次。两次主机查询取得相同缓存，不代表间隔数秒的两次新硬件探测。S2CR.TYPE=TRANSLATE、SCTLR.M=0、CBAR.TYPE=S1_TRANS_S2_BYPASS、CBA2R.VA64=1，呈现旁路配置；该候选范围位于 bank 2，与已验证 USB 的 bank 4 不同。未改写任何 SMMU 或 UFS 寄存器，未读取队列指向的内存，未提交介质命令。

原有 IO、进程、`/tmp` 和 1,000 次进程回收自检通过，第二次主机采样中 tick 继续增长至 4；USB 命令错误、回复溢出和日志丢失均为零。最终日志响应 4,172 字节，SHA256 `a4d5d2cf5464ad71d9b3614ac30133b1dce4ee5f84a371fd5eff1c0a7c282cc5`；盘点响应 SHA256 `856c25e2fc9825a4392597d3eca32c7e97e2891f0f744d155d703f8bfae55ef5`。

#### 第三轮实现范围：新队列与有界 NOP 传输

1. 在受控内存窗口新分配对齐的 DMA 缓冲区，检查 CAP/SMMU 声明的地址范围与物理地址；不复用固件的 `c989a000/c989b000` 内存。先用主机模型验证描述符布局、地址边界、缓存维护和超时处理。
2. 仅在前置快照有效、无截断/歧义/变化且 doorbell 为零的条件下，停止 UFS 传输队列并核对读回；读取原配置备查，再安装自有队列。bank 2 为共享配置，后续方案保留该 context 与流映射。
3. 首个实际传输只使用单个 NOP OUT，限时轮询 doorbell/OCS 与 NOP IN 响应，并核对 task tag、缓存可见性和 USB/用户态存活。失败时保存队列及 SMMU 故障诊断，不循环重试或直接进入介质命令。
4. NOP 成功只证明该命令的传输/DMA 路径；不单凭成功反推精确硬件 SID 或长期独占所有权。随后逐步加入设备描述符查询、LUN/容量识别和已知 ESP 区域的有界读取与哈希比对。介质写入、RPMB 和持久根仍不在本轮范围。

第三轮候选已实现上述单次 NOP：`--ufs-nop` 必须同时启用 USB、probe 和 inventory，并固定单核/1 GiB 受控窗口。新 UTRL/UCD/响应缓冲共 16 KiB；传输无 PRDT 数据负载，初始 OCS=15、响应区填充标记、其余区域加保护字节。停队列/启动各限 100 ms，完成限 500 ms；不进行自动重试。完成后请求停队列，保留自有地址和页面，既不还原固件缓冲地址，也不在超时后释放页面。只写 UTRLBA low/high、UTRLRSR 和 slot-0 doorbell，SMMU、任务列表、IE/IS、HCE、PHY/电源/时钟保持不改。FSR 修正版已通过下方完整 CI 与真机 NOP 验收。

第三轮首个候选 `5670f6c` 的完整 CI 和基线自检通过，但原始 bank-2 FSR=`0x400` 被错误当成故障，前置检查在任何 UFS 写入/NOP 提交之前退出。固定 Linux 定义中 FSR[10:9] 为 FORMAT，不属于故障掩码 `0xc00001fe`。修正版使用该掩码判断，保留原始值，补充所有 FORMAT 取值与真实 fault 位的测试。首轮未进行 NOP/DMA，原始证据和[摘要](ufs-nop-preflight.json)保留；继续使用修正版验收。

### 第三轮成功结果（2026-11-11 10:50 UTC）

源码 `1d38b03539ec26b146ceda717c6cf831ab572cec` 的 [完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43004883497)通过，包含 57 项主机 unittest、定时器/加载窗口检查、完整基线与拒绝用例。最终打包内核在关闭硬件诊断参数的本地 QEMU 中出现 STRESS_OK/CLEAN，tick 6→9；原生 UFS 结果来自真机。公开记录见 [ufs-nop-result.json](ufs-nop-result.json)，原始响应位于 `artifacts/ufs-nop-phone-1d38b03/`。

| 项目 | 真机结果 |
| --- | --- |
| 新 UTRL/UCD/响应分配 | 16 KiB，基址 `0x00000001023bc000`，位于受控窗口且高于 4 GiB |
| USB DMA 分配 | `0x00000001023ac000`，64 KiB，与 UFS 分配不重叠 |
| 原固件 UTRLBA | `0x00000000c989a000`，未复用其内存 |
| 提交次数 | 一次 slot-0 doorbell；无自动重试 |
| OCS | 初始 15 → 硬件完成 0 |
| NOP IN | `20000000 / 00000000 / 00000000`，transaction 0x20、task tag 0 匹配 |
| 缓存与保护 | 提交前 clean+invalidate，完成后 invalidate，描述符/请求/保护区校验通过 |
| UFS 写入 | 6 次，仅 UTRLRSR、UTRLBA low/high 和 doorbell |
| 最终队列 | UTRLBA=`0x1023bc000`，run-stop=0、doorbell=0，页面保留至重启 |
| 最终 IS / IE / HCS / HCE | `00000401 / 00000000 / 0000010f / 00000001` |
| SMMU | 未写入；候选 bank-2 配置和引用情况采样前后一致 |
| 原始故障状态 | global FSR 前后 0；context FSR 前后 `0x400`，FORMAT=2、故障掩码内位均为 0 |

doorbell 在第 2 次轮询中归零，这只是功能完成记录，不作为延迟基准。FAR/SYN 原始值已保存；在 FSR 故障位未置位时，不将它们解释为本次传输异常。未改写 SMMU、PHY、时钟、HCE、IE/IS 或任务队列，没有 PRDT 数据负载和介质命令。两个 USB 查询取得相同缓存，只提交过一次实际 NOP。

原有 IO、子进程、`/tmp` 和 1,000 次进程回收自检通过，第二次主机采样中的 tick 继续增长至 4。USB 命令错误、回复溢出和日志丢失均为零；最终日志响应 4,414 字节，SHA256 `0f3dca2597455e674c44decb215b2a79bace9069057666463a8eec3cc339db2e`。NOP 响应 SHA256 `766a13c5c393d42dcc8100e34f30322c7b2c5c896c0e19a16a0b4332ebcb203c`。镜像保留于 `artifacts/ufs-nop-format-ready/`。

### NOP 后的实施顺序

先在已验证的自有队列中加入限时 QUERY READ_DESCRIPTOR，验证响应类型、tag、长度、返回码与边界，取得设备/单元描述符；原始唯一标识保留在本地，公开摘要仅记录必要能力。随后分别实现 SCSI LUN/容量查询和带 PRDT 的有界读取，用已知 ESP 内容做读回哈希比对。每类命令单独记录资格；继续使用 RAM 根。介质写入、RPMB、持久根、精确 SID 的动态归属及长期独占仍不包含在 NOP 成功结论中。

## 第四轮：只读 QUERY READ_DESCRIPTOR（真机通过）

新增独立 `--ufs-query` 开关，必须同时启用已有 USB/probe/inventory/NOP 路径。NOP 成功且自有队列停止后，最多执行两条 READ_DESCRIPTOR：DEVICE（IDN 0）和 UNIT index 0（IDN 2），selector 0、请求长度 255；不查询字符串或写设备配置。描述符随 QUERY RESPONSE 返回到已有 512 字节响应区，不使用 PRDT，也不读扇区。每条命令只提交一次，核对返回码、tag、opcode/IDN/index/selector、分段/OSF 长度、bLength/type、保护区及停止状态，失败即停止，缓冲区保留至重启。USB `ufs-query` 返回启动时缓存；公开证据只保留必要能力、长度和哈希。UNIT 中的块数/块大小只是描述符线索，仍待 SCSI 容量与数据读取验证。已通过 60 项主机 unittest、ARM64 语法检查、完整 CI、本地打包镜像 QEMU 画面，以及一轮真机验收。


### 第四轮真机结果（2026-11-11 11:56 UTC）

[CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43011741949)首轮未分配到 macOS runner；GitHub 注释说明容量不足，内核编译步骤未执行。重试同一提交后全部任务通过。128 MiB ESP 写入 userdata 后临时启动固定固件；本地 QEMU 画面为 STRESS_OK/CLEAN、tick 6→9。原生 CDC 验证精确源码身份、nonce、缓存查询与原有 IO/进程/tmp/1,000 次进程回收自检，后续采样中 tick 继续增长至 3。公开结果见 [ufs-query-result.json](ufs-query-result.json)，原始响应仅在本地 `artifacts/ufs-query-phone-9feeaca/`。

| 项目 | 结果 |
| --- | --- |
| DEVICE | 64 字节，spec `0x0210`，manufacturer ID `0x01ad`，6 个 LU、4 个 WLU |
| UNIT 0 | 35 字节，enabled=1，逻辑块 4,096 字节，块数 14,419,968 |
| 容量线索 | 59,064,188,928 字节；来自 UNIT 描述符，尚非 SCSI 容量或块读取验证 |
| 两次 QUERY | 各提交一次、OCS=0、response=0、tag/类型/长度/保护区校验通过，各在第 2 次轮询完成 |
| 自有缓冲 | 复用 NOP 的 `0x1023bc000` / 16 KiB；每次完成后 run-stop=0、doorbell=0 |
| 本轮写操作 | NOP 阶段 6 次 UFS MMIO 写，QUERY 阶段合计 6 次；SMMU 写入和介质命令均为 0 |
| 故障状态 | global FSR 前后 0；bank FSR 前后 `0x400`，故障掩码内位均为 0；候选映射未变化 |
| USB / 日志 | 命令错误、回复溢出和丢失均为 0；最终日志 5,100 字节 |

两次后续 USB 采样读取同一启动缓存，不会重发设备查询。该结果证明 QUERY 响应中的元数据路径，不能替代 PRDT 数据负载、SCSI READ CAPACITY 或实际扇区读回。下一轮分别引入带 PRDT 的只读 SCSI 识别/容量命令和有界块读取，再用已写入的 ESP 固定内容做哈希比对；继续使用 RAM 根，保留本轮成功镜像。


## 第五轮候选：SCSI 元数据与 PRDT 数据负载

增加独立 `--ufs-scsi`，须同时启用并通过 probe/inventory/NOP/QUERY。
复用已停止的 16 KiB 自有队列分配，在 +0x1400 放置唯一 PRDT 项，
指向 +0x2000 的独立数据页。两条命令固定为标准 INQUIRY（36 字节）与
READ CAPACITY(10)（8 字节），LUN/tag 0，无 VPD、PMI 或任意 CDB 接口。
返回类型、状态、residual/溢出、sense、PRDT 内容及所有非返回区均检查；
容量必须与 UNIT 0 描述符一致。非 GOOD、超时或状态变化即停止，不重试，
请求停止队列，页面保留到重启。故障诊断和缓存结果由 USB `ufs-scsi` 取回。

本地主机 70 项 unittest、定时器和加载窗口检查通过，其中新的独立
CPU/device DMA 模型核对字节级 UTRD/CDB/PRDT，模拟数据页未 invalidate、
数据未返回、坏 PRDT/保护区、CHECK CONDITION、错误 residual、超时及清理失败。
首版与修正版的实际验收分别记录在下方；完整 CI 与本地/真机验收不混记。
这一轮仍不读取扇区，不写介质。后续使用已知 ESP 数据做有界扇区读回。

真机部署沿用 [默认 edk2 与 USB 文件更新](uefi-usb-update.md)，保留当前成功
`9feeaca` 镜像，通过 Mass Storage 更新 EFI 文件后普通重启，不再依赖每轮 fastboot。


### 第五轮首个真机候选：INQUIRY 填充误拦

源码 `a05b083` 的完整 Intel Mac/Xcode 26.2 CI 与本地打包画面检查通过，
通过 USB 更新文件后普通重启。实际 INQUIRY 已返回 36 字节，OCS=0、
SCSI GOOD、residual=0，PRDT/保护区与停止状态通过，SMMU 未变化且无新增故障位。
设备返回 `SKhynix` / `H28U74301AMR` / `D003`，vendor/product 使用尾随 NUL。
首版把字段限定为纯可打印 ASCII，因此误判 payload-error，按设计停止，
没有发出 READ CAPACITY。原有自检、心跳、NOP/QUERY 与 USB 仍通过。
[记录](ufs-scsi-inquiry-rejected.json)保留此失败，不把容量或完整 PRDT 阶段记为通过。

修正版逐字段允许 ASCII 前缀及仅由 NUL/空格构成的尾部；拒绝 NUL 后再次出现
非空格文本及其他控制/非 ASCII 字节。数据区初始化改为非 ASCII 哨兵，确保
仅部分更新文本字段尾部也被拒绝。实测字节已加入主机和实际 C++ 传输模型回归。
随后已重编译并完成以下新一轮真机验收；扇区读取仍待验证。


### 第五轮修正版：PRDT 与 SCSI 容量真机通过

源码 `88cf31d` 经 USB EFI 文件更新后普通重启成功，三次独立 USB 采样返回
相同的启动缓存，未重发 SCSI 命令。INQUIRY 的 36 字节与 READ CAPACITY 的
8 字节均在高于 4 GiB 的独立数据页经单项 PRDT DMA 返回；OCS=0、SCSI GOOD、
residual=0、保护区完整，队列停止且 doorbell=0。返回设备 `SKhynix` /
`H28U74301AMR` / `D003`；容量为 14,419,968 个 4 KiB 块，合计
59,064,188,928 字节，与 UNIT 0 描述符一致。

两条 SCSI 命令共 6 次 MMIO 写入、2 次 doorbell，无 SMMU 写入、扇区命令或
介质写命令。SMMU global FSR 保持 0、bank FSR 保持已有 FORMAT=0x400；
未出现新增故障位。PID 1、RAM 文件、子进程、1,000 次进程自检与变化心跳均通过，
USB 错误、回复溢出和日志丢失均为零。[脱敏证据](ufs-scsi-result.json)保留精确哈希。

构建来源：Mac CI 完成内核和实际签名门检查；EFI 复用源码未变更的 `a05b083`
成功 CI 二进制。本地 70 项 unittest、定时器/窗口 ASan/UBSan、15 项 QEMU 运行与
拒绝控制、最终打包画面均通过（QEMU 6.2 使用 CPU=max，画面心跳 4→7）。
同轮 GitHub Linux 任务因 Ubuntu 软件源 DNS 故障失败，未记为完整 CI 通过。
本地组装及旧 EFI 来源明确保存在结果中；该故障不作为驱动运行结果解释。

### 下一轮：首个有界原生扇区读取

先固定 READ(10) 的 LUN 0、单次 4 KiB、LBA 0/1 及 GPT 所需的有界表区间，
不提供任意 CDB 或写接口。与 Mass Storage 阶段保存的只读参考逐字节/哈希比对，
检查 GPT 签名、头与表 CRC、LBA/容量及分区边界。依次验证请求、DMA 数据、保护区、
停止状态和故障位；失败即停止，无自动重试。先在主机 CPU/device DMA 模型和
打包镜像验收，再通过同样的 USB 文件更新及普通重启进行真机测试。
本轮继续使用 RAM 根；完成扇区读回后，再进入只读块设备/文件系统与 Apple 输入加载。


### 第六轮候选已实现：固定六扇区 READ(10)

新增独立 `--ufs-read`，必须同时通过已有 SCSI 容量和 PRDT 元数据阶段。
只允许 LUN 0、单次 4 KiB、LBA 0～5，最多 6 条 READ(10)，每条只提交一次；
无任意 LBA/CDB 或写接口。复用已验证的独立数据页和单项 PRDT，逐条检查
返回码、residual/sense、保护区、队列停止、SMMU 配置与故障位，失败即停止。
原始扇区仅由 USB `ufs-read` 返回启动缓存，不写入屏幕/启动日志。
主机逐字节比较独立预启动参考，再核验保护 MBR、主 GPT 头/表 CRC、容量、
分区边界及重叠；备 GPT 未包含在本轮读取范围。

本地 CPU/device 分离模型验证真实 C++ 传输和完整 4 KiB 数据页缓存维护，覆盖
错误 CDB/PRDT、越界请求、残留、CHECK CONDITION、未失效/未 DMA/部分 DMA、
超时和停止失败；主机拒绝截断/乱序/错误构建身份、参考不一致和坏 GPT。
当前源码 `996fb95` 的 [完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43051897934)、77 项本地主机测试及 15 项运行验收报告通过。最终打包镜像在本地 QEMU（CPU=max，关闭 USB/UFS 硬件参数）显示完整自检，心跳 5→8；真机介质读取仍待用户重启后验收。
用户已要求本轮更新文件并回读后停止，等待用户稍后自行普通重启；不得提前启动
或把“文件写入成功”记作扇区读取通过。

候选复查另修正了异常 sense 长度的保留顺序：长度超过 18 字节时不写入公开诊断长度，避免失败输出读取超出 sense 数组。`0xffff` 长度已加入 C++ 模型回归；首个候选 `66a459b` 不用于本轮设备更新，实际待部署源码为 `996fb95a382530b8d2389e2da2df7588cfe64489`。
