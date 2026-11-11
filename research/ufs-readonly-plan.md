# Polaris 原生 UFS 只读识别准备

当前可靠基线是源码 `7e3fa6ff5fe21e5ede1d0e7d44ab3536eb846f4b`：RAM 根、自制用户态、USB 诊断和十轮同镜像启动已按[验收计划](bringup-test-plan.md)验证。F2 仍未接入。以下是下一轮实现范围和静态资源盘点，尚未在 XNU 中访问 UFS 控制器或介质。

## 已知资源

资源来自自有 MIX 2S 的 Lineage DTB，SHA256 为 `57a156f62ad5278599b979d0248b8be5fff9465b19f0e741b860b2677aa9f0af`。完整 DTB 和设备序列号未提交。

| 项目 | 静态证据 |
| --- | --- |
| 控制器节点 | `ufshc@1d84000`，`qcom,ufshc` |
| MMIO | 基址 `0x01d84000`，范围 `0x2500` |
| 中断 | GIC SPI 265，对应 INTID 297 |
| 链路 | 每方向 2 lanes |
| 时钟名称 | core、bus_aggr、iface、unipro、ice、ref，以及 TX lane0、RX lane0/1 sync |
| 电源与复位 | DT 提供 HBA、VCC、VCCQ2 等电源和 core_reset；实际寄存器与继承状态待核对 |
| DMA stream | 该旧版控制器节点没有 `iommus` 属性，SID 尚未建立；USB 的 SID `0x740` 只用于 USB |

寄存器语义参考固定 Linux 修订版的 [include/ufs/ufshci.h](https://github.com/torvalds/linux/blob/067c5c24cef23d913b67eaaf71ae67e7169a6620/include/ufs/ufshci.h)。后续还需核对同版本 Qualcomm UFS、GCC 和 PHY 实现。

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

主机 ASan/UBSan 测试覆盖关闭探测、供电/复位/时钟拦截、控制器地址白名单、映射失败、状态变化、无效寄存器，以及原始 USB 证据的构建身份和计数校验。状态不满足时只记录跳过，不算控制器识别通过。当前候选尚待 CI 和真机验证。

## 后续只读传输

建立 UFS 的 SID、SMMU 路径和 DMA 地址能力，确认队列交接后，再实现 NOP、设备描述符查询、LUN/容量识别和有界块读取。已知测试区域可使用当前 ESP 的固定内容作读回与哈希比对；每项单独记录实际设备结果。当前阶段不实现介质写入、RPMB 操作或持久根文件系统。
