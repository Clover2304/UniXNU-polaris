# Polaris 原生 UFS 只读识别与后续计划

当前源码 `9f1401c1f91153a40a123cc4c1eb55267aca8ead` 已在真机完成控制器寄存器识别、固件队列和候选 SMMU 映射的只读盘点，原有 RAM 根、自制用户态和 USB 自检仍通过。首轮控制器识别源码 `c5dbce256e9d6c1fcd3ae60e27abbb0063254565` 已保留。UFS DMA、介质访问和队列所有权尚未验证。十轮同镜像启动的可靠性证据仍属于 `7e3fa6ff5fe21e5ede1d0e7d44ab3536eb846f4b` 基线；c5dbce2 和 9f1401c 各验证一轮整机启动。

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

控制器可识别为 UFSHCI 2.1，CAP 声明 32 个传输槽位与 64 位寻址能力；后者只是能力位，尚未验证 UFS DMA。12 次 GCC 读取（前后各 6 次）一致，控制器仅读取上述 6 个寄存器，UFS MMIO 写入和介质命令提交均为零。缓存快照的 SHA256 为 `bd5af264d9bfcca8dc50b27665f8e2ff0c2f4dbec0b2ebe8e4c8b378d5b0b3aa`。

原有 IO、子进程、`/tmp` 和 1,000 次进程回收自检全部通过；最终日志包含 tick 1～3，第二次采样较第一次继续增长；USB 命令错误、回复溢出和日志丢失均为零。最终运行日志响应 3,126 字节，SHA256 `1560759cc3ff473e50746f29e3c688283ea716ab4bb5e8b48351ac9adc84ad91`。两个 doorbell 为零只表明采样时没有挂起位，不证明队列所有权、固件完全退出或链路可重新初始化。

## 后续只读传输

只读盘点已完成，下面记录观察到的候选映射和固件队列配置。主线设备树提供 `0x100 / mask 0xf` 线索，尚未作为本机的已验证 SID；不得套用 USB 的 `0x740`。确认队列交接和 DMA 地址能力后，再实现 NOP、设备描述符查询、LUN/容量识别和有界块读取。已知测试区域可使用当前 ESP 的固定内容作读回与哈希比对；每项单独记录实际设备结果。当前阶段不实现介质写入、RPMB 操作或持久根文件系统。

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

#### 下一轮：新队列与有界 NOP 传输

1. 在受控内存窗口新分配对齐的 DMA 缓冲区，检查 CAP/SMMU 声明的地址范围与物理地址；不复用固件的 `c989a000/c989b000` 内存。先用主机模型验证描述符布局、地址边界、缓存维护和超时处理。
2. 仅在前置快照有效、无截断/歧义/变化且 doorbell 为零的条件下，停止 UFS 传输队列并核对读回；读取原配置备查，再安装自有队列。bank 2 为共享配置，后续方案保留该 context 与流映射。
3. 首个实际传输只使用单个 NOP OUT，限时轮询 doorbell/OCS 与 NOP IN 响应，并核对 task tag、缓存可见性和 USB/用户态存活。失败时保存队列及 SMMU 故障诊断，不循环重试或直接进入介质命令。
4. NOP 成功只证明该命令的传输/DMA 路径；不单凭成功反推精确硬件 SID 或长期独占所有权。随后逐步加入设备描述符查询、LUN/容量识别和已知 ESP 区域的有界读取与哈希比对。介质写入、RPMB 和持久根仍不在本轮范围。

第三轮候选已实现上述单次 NOP：`--ufs-nop` 必须同时启用 USB、probe 和 inventory，并固定单核/1 GiB 受控窗口。新 UTRL/UCD/响应缓冲共 16 KiB；传输无 PRDT 数据负载，初始 OCS=15、响应区填充标记、其余区域加保护字节。停队列/启动各限 100 ms，完成限 500 ms；不进行自动重试。完成后请求停队列，保留自有地址和页面，既不还原固件缓冲地址，也不在超时后释放页面。只写 UTRLBA low/high、UTRLRSR 和 slot-0 doorbell，SMMU、任务列表、IE/IS、HCE、PHY/电源/时钟保持不改。当前候选尚待完整 CI 和真机验收。

第三轮首个候选 `5670f6c` 的完整 CI 和基线自检通过，但原始 bank-2 FSR=`0x400` 被错误当成故障，前置检查在任何 UFS 写入/NOP 提交之前退出。固定 Linux 定义中 FSR[10:9] 为 FORMAT，不属于故障掩码 `0xc00001fe`。修正版使用该掩码判断，保留原始值，补充所有 FORMAT 取值与真实 fault 位的测试。首轮未进行 NOP/DMA，原始证据和[摘要](ufs-nop-preflight.json)保留；继续使用修正版验收。
