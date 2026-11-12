# MIX 2S / UniXNU 验收与基准测试计划

更新时间：2026-11-12。本文覆盖从启动到目标 iPadOS 用户态的项目级验收；功能正确性、稳定性和性能分别记录。

## 当前基线与证据

- 已到达：单核 XNU、具名 RAM 根目录、有容量上限的 `/tmp` 动态普通文件、1,000 次进程创建/执行/回收自检（含每批 8 个子进程），以及原生轮询 USB CDC 诊断命令与运行时日志取回，以及 UFS 控制器/队列/SMMU 只读盘点、自有队列 NOP、DEVICE/UNIT 0 描述符查询，以及独立 PRDT 数据页上的 SCSI INQUIRY/READ CAPACITY、LUN 0 前六个 4 KiB 扇区原生读取与主 GPT 校验、ESP 中已知 FAT32 文件读取，以及通过只读 devfs 节点的实际 EL0 文件访问；另已验证默认 edk2 启动和固件辅助的 USB 文件更新。
- 配置：6 GB 机型配置；当前仅启用 1 CPU，内核限制在受控的 1 GiB 内存窗口。完整 RAM 和 SMP 未验证。
- 十轮启动和重连已验证的 USB 源码基线：`7e3fa6ff5fe21e5ede1d0e7d44ab3536eb846f4b`。此前 USB 基线 `638a344d865375c209f13f6109484eee7fd7d5fe`、压力测试基线 `c4081260dd52a6ccdbd3b4f99128b1f4e9c4025e` 及其他成功镜像均保留。
- 保留的 UFS NOP 源码 `1d38b03539ec26b146ceda717c6cf831ab572cec` 已通过 [完整 CI 与模拟器运行](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43004883497)、57 项主机 unittest、定时器/加载窗口检查和一轮真机启动。首轮 c5dbce2、只读盘点 9f1401c 和前置检查误拦 5670f6c 的镜像/记录均保留。QEMU 不模拟 SDM845 USB/UFS；相应外设取证来自真机。
- 真机证据：用户先确认 USB_RUN 和 Windows CDC 枚举，随后主机通过原生 USB 完成 ping、随机 nonce 往返、精确构建身份核对，并取回包含 TMP_CHILD_OK、TMP_OK、CHILD_OK、PROC_OK、STRESS_OK/CLEAN 和变化心跳的运行日志。主机保存了原始 USB 响应及部分外设寄存器诊断；内部进程/task/内存页计数仍仅在 QEMU 取证。
- 保留的 USB 工作区基线：`/root/projects/xnu-polaris/artifacts/usb-reset-ready/`，ESP SHA256：`0fa69a26986f7529516810a70ed63290e9a39b16f5f8e3b507cc000bfec71e47`。USB 证据在 `artifacts/usb-native-check-7e3fa6f/`、`artifacts/usb-reconnect-7e3fa6f-10/`、`artifacts/usb-control-stall-7e3fa6f/`、`artifacts/usb-continuous-7e3fa6f/`。整机启动计数单独保存在 `artifacts/usb-boot-series-7e3fa6f/index.json`。
- 保留的 NOP 镜像在 `artifacts/ufs-nop-format-ready/`，ESP SHA256 `495a3a4b2b9cbf162654c44750952089114fbd45076075eb1e52119e2d219493`；[NOP 证据](ufs-nop-result.json)、[队列/SMMU 证据](ufs-queue-smmu-snapshot.json)和[后续计划](ufs-readonly-plan.md)已保存。
- 最近一版完成原全套自制程序真机自检的内核源码为 `abc75cfb6a7f3a209af4865f575a9b70387c916e`，已通过 [完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43141529622)、86 项主机检查和 16 份运行验收，QEMU 也执行真实异步请求队列。真机一轮启动通过 `/dev/polaris-esp-file` 的 8193 字节顺序读、跨界/偏移、EOF、重复读、写入拒绝和重开；9 次请求完成，10 条 READ(10) 的顺序与 CRC 匹配实际镜像，三次独立 USB 捕获与变化心跳通过。原有自检、USB 健康、SMMU 故障位和零日志丢失通过，见 [文件设备证据](ufs-node-result.json)。此前 [FAT 文件](ufs-fat-result.json)、[六扇区](ufs-read-result.json) 与 [SCSI 元数据](ufs-scsi-result.json) 基线保留。默认 edk2 与 USB 更新见 [说明](uefi-usb-update.md)。
- 最近完整真机自检基线的根目录为启动时确定的 5 个目录和 5 个文件，以及 `/tmp` 的动态普通文件能力；保留固定 4 KiB RAM 状态文件。标准输入是自测管道。原版 dyld 早期执行已有 QEMU 取证，真机按用户画面回报到达缺 libSystem 的依赖查找退出；Apple launchd 主函数、交互输入、通用 UFS 块设备/文件系统或完整可写文件系统尚未通过。当前原生介质读取限定 GPT 六扇区和已核对的 128 MiB ESP 窗口，文件查找限定一个固定路径。
- 最新源码候选 `57a7d7f` 已通过完整 CI（88 项主机检查、16 份原有运行报告），并在 QEMU 通过原版小缓存的签名登记、两个 16K 签名块认证、8 次 4K 缺页校验和映射清理；关闭授权和篡改拒绝对照通过，最终打包内核重复通过正向/篡改测试。真机已普通启动，原生 CDC 核对精确构建身份、随机 nonce、CACHE_OK 和两次独立快照间变化的心跳，错误/日志丢失均为零。本轮小缓存程序自检真机通过；内部 2/8 哈希计数仍仅 QEMU 直接观察，见 [缓存运行证据](apple-cache-runtime.json)、[真机结果](apple-cache-phone.json)和 [部署记录](apple-cache-deploy.json)。
- 目标 Apple 镜像先固定为 arm64 iPadOS 17.7.11（21H461，iPad7,1）；自制程序通过不等于该系统已兼容。

状态：**真机通过** = 指定功能由真机程序自检、用户回报画面或实际 USB 主机往返与日志取证证实，逐项注明来源；**模拟/主机通过** = 仅相应环境取证；**部分** = 仅列明范围；**待验证** = 无充分证据；**未实现/未接入** = 当前不具备。次数和时长均为建议目标，不是已完成结果。

## A. 构建与启动

| 编号 | 验收内容 | 状态 |
| --- | --- | --- |
| A1 | 固定源码、工具链与配置，保存哈希并可重复构建 | 最新候选 57a7d7f 完整 CI、88 项主机检查及 16 份原有运行验收报告通过；最近完整真机基线 abc75cf 为 86 项主机检查；此前 CI 故障单独保留 |
| A2 | 固件、UniXNU、DarwinBoot 到 XNU 交接 | 临时 fastboot 路径与新默认 edk2 普通重启路径均真机通过 |
| A3 | ADT、加载地址、内存窗口与越界拒绝 | 正常路径真机通过；异常输入主机/模拟通过 |
| A4 | 内核初始化、当前配置 corecrypto POST、BSD/IOKit 初始化 | 真机通过 |
| A5 | RAM 根挂载并启动 PID 1 | 真机通过 |
| A6 | 返回 fastboot，保留成功镜像与恢复材料 | fastboot 真机通过；完整系统还原未实测 |
| A7 | 10 次重复启动，覆盖冷启动和热启动 | 同一镜像 10/10 轮通过；按热启动指令完成 8 轮，用户确认冷启动 1 轮；USB 重连单独计数 |
| A8 | 默认 edk2 与 USB 更新 EFI 文件后启动 | 一轮普通重启真机通过；boot 完整备份和安装回读通过，USB 文件更新回读及原生自检通过；恢复备份工具已准备，实际恢复未测 |

## B. CPU、内存与异常

| 编号 | 验收内容 | 状态 |
| --- | --- | --- |
| B1 | 单核调度和父子进程切换 | 真机通过 |
| B2 | 定时等待、唤醒并继续执行 | 真机通过；IRQ 计数模拟取证 |
| B3 | 用户代码、数据与栈映射 | 基本路径真机通过 |
| B4 | C 入口栈 16 字节对齐 | 真机通过；已修复 SIGBUS |
| B5 | fork 后父子私有内存隔离 | 单个 COW 变量及四个独立 4 KiB 页各抽样字节的进程自检真机通过 |
| B6 | 非法地址、只读页写入和执行权限错误处理 | 待验证 |
| B7 | mmap/mprotect/munmap、缺页与大范围 COW | 部分真机自检与 USB 日志通过：原版小缓存 32 KiB MAP_PRIVATE / RX 映射、8 个 4K 页读取和 munmap；内部缺页校验计数仅 QEMU 取证，mprotect、一般映射边界与大范围 COW 待验证 |
| B8 | FPU/NEON/SIMD 上下文保存恢复 | 待验证；当前程序禁用 SIMD |
| B9 | IPI、多核启动、跨核调度和 TLB 同步 | 待验证；尚无 IPI 接收证据 |
| B10 | 全部计划 RAM、内存压力、分配失败与回收 | 待验证 |

## C. 进程、系统调用与 IPC

| 编号 | 验收内容 | 状态 |
| --- | --- | --- |
| C1 | getpid/getppid/sysctl 等实际使用的系统调用 | 当前子集真机通过 |
| C2 | stdout/stderr 与管道 stdin | 真机通过；stdin 为自测管道 |
| C3 | fork/exec/exit/wait、PID/PPID、退出状态 | 真机通过：子进程 2、父进程 1、退出码 42 |
| C4 | 描述符继承和共享 RAM 文件访问 | 真机通过 |
| C5 | 多子进程并发、1,000 次创建回收，无僵尸和泄漏 | 200 串行 + 800 个分批子进程真机自检通过；内部链表仅模拟取证；完整泄漏排查未完成 |
| C6 | 跨进程管道、信号、阻塞 I/O、EINTR 和就绪通知 | 管道屏障跨进程收发真机通过；其余待验证 |
| C7 | Mach 消息、端口权限与生命周期、跨进程内存传递 | 待验证 |
| C8 | 多线程、TLS、互斥锁、条件变量和信号量 | 待验证 |

## D. 文件系统

| 编号 | 验收内容 | 状态 |
| --- | --- | --- |
| D1 | 具名目录及父子关系 | 真机通过：5 目录、5 文件 |
| D2 | open/read/write/close、重开、pread/pwrite | 固定 4 KiB RAM 文件和固定只读 UFS 文件视图真机通过 |
| D3 | EOF、边界与越界写入错误 | 真机通过 |
| D4 | 精确路径、相对路径、chdir 和 ENOENT | 真机通过 |
| D5 | 负偏移、超大长度、分配与用户拷贝失败 | 内核例程主机通过；完整真机故障注入待做 |
| D6 | 动态文件/目录创建和目录遍历 | 普通文件创建和目录遍历真机通过；动态目录未实现 |
| D7 | lseek、追加、截断、删除、重命名和元数据更新 | lseek、追加、截断和删除真机通过；重命名及完整元数据待做 |
| D8 | 并发 I/O、空间耗尽、删除后回收的一致性 | 容量耗尽、打开后删除及回收真机通过；8 子进程各自临时文件和退出清理自检通过；同文件并发一致性待验证 |
| D9 | 持久化、fsync、重启保留与断电恢复 | 未接入 |

## E. 签名与诊断

| 编号 | 验收内容 | 状态 |
| --- | --- | --- |
| E1 | 指定签名的自制 init 与子程序获准执行 | 真机通过 |
| E2 | 错误 CDHash、损坏代码、无签名程序被拒绝 | 主机/模拟通过 |
| E3 | 子进程不能复用 PID 1 签名缓存绕过身份检查 | 模拟通过 |
| E4 | panic 文字及故障现场显示 | 基础显示真机通过；USER 寄存器显示模拟通过 |
| E5 | 自动保存并取回完整日志、异常原因和构建身份 | 部分真机通过：USB 取回 64 KiB 环中的运行日志及精确构建身份；早期启动和持久化崩溃日志未完成 |

自制程序的精确签名授权不等于 Apple 完整签名与安全服务链已验证。

## F. 硬件驱动

| 编号 | 验收内容 | 状态 |
| --- | --- | --- |
| F1 | DMA、缓存一致性、SMMU/IOMMU、设备中断 | USB 收发、UFS NOP/QUERY 和单项 PRDT 的 INQUIRY/容量元数据、六个 4 KiB 扇区和已知 FAT32 文件的 DMA、缓存维护真机通过，复用原有旁路；隔离、长期独占和原生设备中断待验证 |
| F2 | XNU 原生 UFS 识别、读写和持久化 | 部分：UFSHCI 2.1 识别、候选 SMMU 盘点、自有队列交接、单次 NOP、DEVICE/UNIT 0 描述符查询、单项 PRDT INQUIRY/READ CAPACITY、固定 LUN 0/LBA 0～5 原生读取、主 GPT 校验、固定 ESP 路径 FAT32 文件读取及 devfs 只读文件系统调用/事件交接（含 >4 GiB 数据页 DMA）真机通过；通用块设备/VFS 挂载、任意文件访问、介质写入和持久化未验证；固件读 ESP 不计入 |
| F3 | XNU USB 枚举、收发和电脑控制台 | 轮询 CDC 枚举、诊断/日志、十次 USB/IP 重连及控制请求 STALL 恢复真机通过；物理插拔、POSIX 终端、吞吐和挂起恢复待验证 |
| F4 | 原生显示控制器、面板初始化与刷新 | 未接入；当前继承固件 framebuffer |
| F5 | Adreno 命令提交、显存管理与图形合成 | 未接入 |
| F6 | 触摸、按键和输入事件 | 未接入 |
| F7 | Wi-Fi、蓝牙、音频、传感器、相机等 | 未接入，分别验收 |
| F8 | 电池、充电、温控、调频、休眠与唤醒 | 未接入 |
| F9 | 若需要电话功能：SIM、基带与蜂窝服务 | 未接入 |

## G. 目标 Apple 用户态

| 编号 | 验收内容 | 状态 |
| --- | --- | --- |
| G1 | 目标 Apple 二进制、签名和代码页兼容 | 部分：静态检查通过；f916ebb 在 QEMU 实际执行原版 dyld 的 32 个字节匹配 PC，关闭模式/损坏页拒绝通过；真机按画面回报到 DYLD/1 缺 libSystem；完整 Apple 信任链、entitlement、真机精确 PC/构建取证未验证 |
| G2 | 必需预链接组件、AMFI/CoreTrust/Sandbox 与存储组件匹配 | 部分静态核对：原 18 组件计划保留；只读存储的声明依赖闭包另缩小为 12 个可执行组件、7 个内核接口并完成计划分析，尚未生成/加载；源 corecrypto 配置与原组件 crypto_bootstrap 的兼容、ABI 和实际挂载未验证 |
| G3 | dyld、共享缓存、libSystem、TLS 和动态链接 | 部分模拟通过：原版 dyld mach_init / prepare；53 个缓存静态检查；其中一个原版 48 KiB subcache 的真实签名登记、16K 完整性认证和 8 次 4K 页校验，含关闭授权/篡改拒绝对照。完整缓存映射、原缓存代码执行、libSystem/TLS 和动态链接仍未通过；小缓存探针的程序自检、精确构建 CDC 与变化心跳真机通过，内部哈希计数和拒绝对照仍仅 QEMU 取证 |
| G4 | Apple launchd 和基本 Mach 服务 | 未接入 |
| G5 | 必需守护进程和系统服务持续运行 | 未接入 |
| G6 | IOSurface、图形服务、SpringBoard 及输入 | 未接入 |
| G7 | 应用启动、权限、退出和崩溃恢复 | 未验证 |

已有机型的签名、entitlement、Sandbox 和用户态启动方案进入优先复用清单，见 [Apple 用户态复用盘点](apple-userland-reuse.md)。G 组的“未验证/未接入”记录本机资格，不意味着相关通用实现需要从零开发；先检查固定 UniXNU 已有模块，再按 XNU 版本移植 davinci 等项目中已解决同类问题的代码。最小 Apple 程序与 launchd 验证可与 UFS 读取支线并行推进，不等待全部外设或完整 Apple 安全服务恢复。

## H. 稳定性与性能基线

| 编号 | 验收内容 | 状态 |
| --- | --- | --- |
| H1 | 连续 24 小时运行并记录心跳、内存与错误 | 仅短时运行通过 |
| H2 | 1,000 次进程创建/执行/回收，资源占用可回归 | 进程、描述符及临时节点自检真机通过；内部计数回归仅模拟取证；完整内存回收未验证 |
| H3 | 文件操作完成后，10,000 次创建/读写/删除循环 | 本轮 64 轮真机通过；10,000 轮未测 |
| H4 | 内存、空间和描述符耗尽后正确报错并恢复 | 有界 `/tmp` 名称/节点耗尽和恢复真机通过；全局内存和描述符耗尽未测 |
| H5 | 故障注入后日志可取回、设备可恢复启动 | 部分具备诊断与 fastboot 恢复能力 |
| H6 | 启动、系统调用、进程创建的中位数与 P95 延迟 | 未建立性能基线 |
| H7 | 内存、UFS、网络、图形吞吐及功耗温度 | 未建立性能基线 |

## 本轮实施与验收：D6、D7 的首批项目

范围限制为 RAM 中的 `/tmp`。已有启动目录、程序和签名策略保持不可修改；原有成功镜像保留。此轮不代表 UFS、完整 POSIX 文件系统或完整 iPadOS 已完成。

1. 实现有容量上限的普通文件创建、读写、目录遍历和删除，处理名称冲突、ENOSPC、ENOENT 等错误。
2. 在不泄漏 vnode 或节点的前提下处理关闭与删除；保留已打开文件描述符的正确生命周期。
3. 补充主机端例程测试、QEMU 真正用户态系统调用及旧版本回归。
4. 自制 init 在 `/tmp` 创建并写入文件；独立子程序读到内容；父进程列目录并删除，随后继续常驻。
5. 根据实现情况分别增加目录创建/删除、截断、追加与 lseek 的验收；未覆盖的 D7 项保持未完成。
6. CI 通过后核对最终镜像、FAT 读回和实际 QEMU 画面，再进行已授权的真机临时启动，由用户确认新成功标记与持续心跳。

每次验收后更新具体条目、源码提交、CI 链接、镜像哈希与证据来源。模拟通过不得升级成真机通过；单个自检不得升级成整个子系统已完整通过。

### D6/D7 实施记录

- 实现提交：`ce50a4154caeaef5e4524b880cccfdb55c80f703`；[完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42950190208) 已通过：主机、EFI、macOS 内核编译、原有用户态及拒绝回归、新增 `/tmp` 运行测试。
- 可选参数 `polaris-tmp=1`；32 个挂载期分配的节点，每个文件最多 4 KiB，同一目录最多 16 个名称。删除但仍打开的文件占用节点，最后一个引用释放后可复用。数据仅在 RAM 中。
- 新增主机检查后为 39 项 unittest；定时器和加载窗口原生检查也通过。内核 I/O 例程使用 ASan/UBSan 检查补零、截断、追加、边界、分配失败和用户拷贝失败。
- 新程序：`python3 scripts/build-polaris-process.py --tmp --out out/polaris-tmp`；QEMU 沿用 `check-polaris-process.py --process-dir out/polaris-tmp --out out/tmp-check`。普通进程基线和签名拒绝回归仍单独运行。
- QEMU 运行取证：114 次创建、114 次删除、82 次节点复用、39 次 RAM 读取和 100 次写入；32 个已删除但打开的文件各自数据正确，关闭后可重新创建。另完成 64 轮创建/写入/关闭/删除；这些是短时验收，不能代替 10,000 轮或并发压力。
- CI 原始内核 SHA256：`d9e7ba0932d42e3513c01634e1259999901c6e199d99586e04f11442280cdc58`；打包内核：`5557d3ac0c3f9532fa1a46a83fdc49689b00830c1ce9dbb8262f5343dd7279a4`；128 MiB ESP：`8bd741e37966bf9f829ef59f026298ebe39e4ca88af528ed5677180a66aeeb49`。
- 工作区 `/root/projects/xnu-polaris/artifacts/tmp-ready/` 保存读回验证后的镜像。最终打包内核在独立 QEMU 显示检查中出现两个 TMP 成功标记，心跳由 3 增至 6；显示检查没有 GDB 断点或来宾内存写入。
- 真机已于 2026-11-11 03:16 UTC 写入测试 ESP 并临时启动。用户随后确认 TMP_CHILD_OK、TMP_OK 及持续变化的 tick；此轮自检按用户回报画面验收通过。114 次操作和具体计数来自程序自检路径及 QEMU 取证，未从真机直接读取计数器。
- 动态建目录、目录删除、重命名、完整元数据、并发一致性、长期压力、UFS 持久化继续保留为后续分项。

## 本轮实施与验收：C5、H2

本轮继续 C5/H2：先完成已有多进程和 `/tmp` 自检，再新增 1,000 个子进程，其中 200 个串行、800 个分为 100 批，每批 8 个。
管道屏障保证一批子进程均已创建后才开始 exec；每个子进程检查继承描述符、独立临时文件及退出时关闭描述符，父进程逐个验证 PID、退出状态和报告。
每批以 ECHILD 检查没有剩余子进程，逐项检查父进程 0～63 号文件描述符，并抽样检查四个独立 4 KiB COW 页各自的字节；最后检查 32 个临时文件节点均可使用。
QEMU 在短暂让出 CPU 后按固定里程碑读取真实进程、task、回收队列及内存页计数，并在屏障处验证 8 个子进程同时存在。
内存页计数作为观测记录，不以单次计数相等宣称全部内存泄漏已排除。

- 源码：`c4081260dd52a6ccdbd3b4f99128b1f4e9c4025e`；[完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42953408390) 已通过。41 项主机 unittest、两个原生检查及已有启动、文件系统、签名拒绝回归保留。本轮未修改内核补丁。
- 复现：`python3 scripts/build-polaris-process.py --stress --out out/polaris-stress`；运行 `check-polaris-process.py --kernel ... --platform-esp ... --process-dir out/polaris-stress --out out/stress-check`。
- 新增 1,000 次 fork/exec/wait，不计启动阶段原有的一个 probe。共有 300 批等待、1,000 份带 token/PID/PPID/slot 的报告、301 次父进程描述符扫描；每批还检查二次 wait 返回 ECHILD、已回收 PID 的 kill(0) 返回 ESRCH。子进程故意保留临时文件和只读文件描述符，由 exit 清理。
- QEMU 静稳取样点为完成 0、200、208、400、600、800、1,000 个子进程后；先通过 select 让出 CPU 20 ms，给异步回收执行机会。每次 `_nprocs=1`、`_tasks_count=2`、`_threads_count=40`、僵尸链表为空；结束时两个待释放队列计数与基线相同。首个 8 子进程屏障处实测 `_nprocs=9`、`_tasks_count=10`。
- QEMU 全程空闲页 247,658→247,586，wired 页 14,423→14,498；首个 8 子进程批次完成后至最终取样，空闲页变化 −24，wired 页变化 +19。缓存、延迟释放和其他内核活动均可能影响页计数，当前不据此判定全部内存泄漏已排除，也不将调试运行耗时当作性能基线。
- `/tmp` 全程创建和删除计数均为 1,146（原有自测 114 + 子进程 1,000 + 最后的 32 个节点检查）；结束时 32 个节点均可再次分配。
- 原始内核 SHA256：`cf1ebb044a82c6db0183c2f0872fa52f06d27f114106013312185789e614b2fd`；打包内核：`db0644cf2da1d9c4e17225a2e42343c7aa24655ac6c434755c4767e91c801d59`；128 MiB ESP：`bbeb0a04c639b7f2330c12f9e62710ceb9c8d94a3fc53883e10b4a4a69f7cb30`。镜像及报告在 `/root/projects/xnu-polaris/artifacts/stress-ready/`。
- 最终打包镜像在独立 QEMU 显示检查中出现 STRESS_OK 和 STRESS_CLEAN，tick 由 4 增至 7。显示检查未使用 GDB 断点或来宾内存写入。
- 真机于 2026-11-11 03:47 UTC 完成测试 ESP 写入并接受临时启动；用户随后确认 `POLARIS_STRESS_OK total=1000 reaped=1000 maxlive=8`、`POLARIS_STRESS_CLEAN nochild=OK fd=OK tmp=OK cow=OK` 和持续心跳。本轮程序自检按用户回报画面验收通过；内核内部计数尚无真机转储，全局内存泄漏排查仍未完成。

## 后续顺序

E5/F3 首批功能已真机通过：复用 BSD-2-Clause-Patent 的 UniXNU-j716f 原始 DWC3 CDC 传输状态机，单独适配 SDM845 GCC、QUSB2 v2、USB SID 0x740。
入口由 `polaris-usb=1` 控制；先保留固件 PHY 状态，启动超时或无 USB 事件时允许一次实测 DTB 的 QUSB2 恢复及设备复位。
主机已使用 `check-polaris-usb.py` 核对 USB 1209:0001、诊断标识、随机 nonce、源码构建身份和实际运行时日志。
日志范围为打开控制台后的 64 KiB RAM 环，包含覆盖丢失计数；不覆盖早期启动、跨重启保存、panic 后取回或 Apple USB 配对。

首轮 USB 候选 `d9e2024a045ff9627fa8edf60197e5be81edae1b` 的 [CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42958623959) 已通过，但真机停在 stage 4，未枚举 USB。用户回报 core=`5533300a`、HWPARAMS0=`4020400a`、SMR/S2CR=`80000740/00000004`、sCR0=`002d0406`；DMA 地址在 4 GiB 以上。现有 USB 流映射到 context bank 4。直接设置 S2CR BYPASS 未通过读回检查，与 Linux 高通驱动记录的固件 BYPASS→FAULT 行为一致，但尚未取得写后的寄存器原值。

下一候选保留 SMR/S2CR，检查 bank 4 没有其他已映射设备后，设置该 USB 上下文 SCTLR=0、CBA2R=1、CBAR=0x10000，采用上下文旁路；任何共享、地址宽度不符或读回失败均中止，并输出 IDR 和上下文诊断。主机测试涵盖上述固件行为、共享拒绝和失败恢复；这些测试不代替真机 DMA 和 USB 验证。E5/F3 仍未验收。

该候选 `e65132961eb477e590317c8f4595ec19892ff9a6` 的 [CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42961833349) 已通过，真机回报 `error=7 bank=4 sctlr=000000e0`、stage 4：检测到 bank 4 共享，未写入 SMMU。提前退出时 CBAR/CBA2R 诊断仍是默认零，不能视为实测值。下一版先按 SCTLR.M 位而非整个寄存器为零判断已旁路状态；若原共享 bank 不能直接复用，则仅检查 Linux 高通旁路方案采用的末尾 apps context bank。该 bank 必须已旁路，或未被前 128 个可靠 stream group 引用且转换关闭，才允许只重定向 USB 的 CBNDX；拒绝占用和读回不符，保留 bank 4 及其他设备映射。高通固件对扩展 stream group 的可见性限制仍在，不宣称完整 SMMU 所有权已建立。诊断拆为短行，显示原 bank、目标 bank、共享数、重定向状态及实际上下文值。等待新候选真机测试。

重定向候选 `4182fe6e121df63a1418197a243d92ced1924609` 的 [CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42964246296) 全部通过。最终打包镜像在关闭 USB 参数的 QEMU 显示检查中出现 STRESS_OK/CLEAN，心跳 5→8；真机于 2026-11-11 05:28 UTC 接受临时启动。用户回报 SMMU `error=0 bank=4 old=4 shared=1 moved=0`，随后 `FAILED stage=5`，SCTLR=`000000e0/000000e0`、CBAR=`0001f000`、SMR=`80000740`、S2CR=`00000004/00000004`。用户随后复核 CBA2R 末位为 1，消除了初次抄录为零的出入：该共享上下文可以原样旁路，不需要重定向。IDR=`4c017eac/5000002d/00005555`，即 64 个全局页、45 个 context bank、扩展 stream group 按固件限制只访问前 128 项。补充实测 `cmd=-1 h1=0162493b h6=07ea0020 h7=03080a65 fifo0=00000040`，将 stage 5 缩小到端点命令等待超时：复位和 FIFO 分配检查已通过，但尚不知第一条超时命令，也尚未证明实际 DMA 或 USB 正常。

后续候选增加启动构建编号、stage 5 具体停止原因、端点号、命令和原始状态；控制端点初始化在第一条失败命令后停止，防止后续成功命令覆盖错误。为 DTB 明确限制的 USB 2.0 路径始终选择 UTMI-as-PIPE，避免把 SDM845 BRANCH_HALT_SKIP 分支的 bit31 当作可靠运行状态；补齐 PHY 配置所需 GCC AHB2PHY 时钟 `0x6a004`，设备复位后重新清除 USB2 PHY 挂起位。启动命令或复位超时后允许一次 QUSB2 初始化并执行完整设备复位，复用同一 DMA 区，CORE_READY 最多等待 10 ms；失败时保留时钟和只读 SMMU 故障寄存器，这些寄存器可能含固件遗留值，不默认归因于本次传输。主机测试直接编译实际命令等待及 EP0 初始化函数，覆盖完成、状态错误、有界超时、首条错误停止和六个初始化失败位置；另覆盖用户回报的 IDR 与 FIFO 参数。46 项主机 unittest、原生检查和完整 CI 均已通过；真机结果如下。

### E5/F3 真机 USB 验收记录

- 源码 `638a344d865375c209f13f6109484eee7fd7d5fe`；[完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42969364633)。最终打包镜像在关闭 USB 参数的 QEMU 显示检查中出现 STRESS_OK/CLEAN、心跳 5→8。2026-11-11 06:14 UTC 写入测试 ESP 并临时启动，用户随后报告 USB_RUN 及 Windows CDC 枚举。
- 通过远程 USB 接入的是 XNU 自身的 `1209:0001` 诊断 CDC；主机实际核对诊断标识、ping、128 位随机串（32 个十六进制字符）往返和构建身份，再取回完整运行日志快照。原始报告在 `artifacts/usb-native-check-638a344/report.json`；日志响应 11,100 字节，截止序号 10,939，覆盖丢失计数 0，SHA256 `b2b19d1c3a3b6a6898f390d19fb325863bf3e2fa7b3fb53e60e4766908d15f77`。头尾序号、丢失计数及源码身份一致。
- 取回的原始运行日志直接包含 PID 1 I/O、独立子进程、`/tmp` 和 1,000 次进程回收成功标记及持续心跳，保留了本轮已有自检的真机证据；没有导出内核内部进程/task/页计数。
- 日志定位首个失败为 EP0 SETEPCONFIG：`dep=0 command=00000001 raw=00000401`，CMDACT 未清。一次 QUSB2 初始化和完整设备复位后进入 RUN，`phy_mode=1`；不能据此宣称时钟选择单独解决了超时，启动仍依赖恢复路径。
- 本轮直接读到 IDR0=`4c017e4c`，低字节表示 76 个 stream group，修正此前用户抄录的 `4c017eac`（172）。IDR1/2=`5000002d/00005555`，仍为 64 全局页和 45 个 context bank。复用 bank 4：SCTLR=`e0`、CBAR=`1f000`、CBA2R=`1`；SMMU 配置没有写入或重定向。实测 DMA 物理地址高于 4 GiB，并经成功收发验证了本条路径。
- 初次主机 status：configured=1、events=348、resets=2、cmd_errors=3、reply_overflow=0。针对该历史错误计数进行了约 10 秒、10 次随机串往返与状态取样：错误保持 3、重置保持 2、无回复溢出，events 370→410、rx 54→504、tx_done 4→24；报告在 `artifacts/usb-native-steady-638a344/report.json`。剩余历史错误尚未逐条定位，未取得零错误启动；不作为长期可靠性或吞吐基准。
- 原始内核 SHA256 `b8c1b55f607010e25d8ad8d3cabd93655be19c127b6828fa8124e240ccde9fc9`，打包内核 `a825dd63f2da8f0f51f869a03d589dc17dddfc196c72aeabcfa699ba2dd38ef1`，128 MiB ESP `38df6123c30c02fa6129c0bdde00d678a3e6ae76bc12f6ee49786d242403dc08`。镜像保留在 `artifacts/usb-clock-ready/`。日志仅覆盖控制台打开后的 64 KiB RAM 环；早期启动、panic 后提取、跨重启保存、POSIX 用户态终端、Apple USB 配对、USB 中断、挂起与长期压力均未验收。

下一步先固定该 USB 成功镜像及日志，再做 A7/F3 重复启动和 USB 重连可靠性，消除未定位的历史命令错误；随后进入 F2 的只读 UFS 识别与读回验证。

### F3 重连可靠性跟进

在已验证的 `638a344` 真机仍运行时，完成两次 USB/IP 导出设备的 detach/attach。原会话及两次重新枚举后的 nonce、构建编号和完整运行日志校验均通过；configured 始终为 1、无回复队列溢出。但 resets 为 2→4→6，cmd_errors 为 3→5→7；这证明收发可恢复，也暴露了随复位增长的命令错误，尚未逐条定位。证据在 `artifacts/usb-reconnect-638a344/`。这两次属于同一运行内核的远程 USB 重连，不计为手机重复启动或物理插拔，A7 仍待验证。

下一候选按 Linux DWC3 控制端点路径调整：处于 SETUP 阶段时保留已有 EP0 传输，其他阶段才执行 STALL/restart；控制端点恢复省去 CLEARSTALL。复位和断开清除半条输入、未发送回复及旧 DTR 状态，避免带入新会话。每次命令错误保存事件上下文、端点、命令及原始状态，供 status/log 取证。同时在首条端点命令前检查 QUSB2 CORE_READY，若固件状态未就绪则先使用已有的有界 PHY 恢复，避免已知的启动超时。48 项本地主机 unittest、原生检查和 ARM64 语法检查通过；新候选等待 CI 及真机验证。

### EP0 复位修正版验收结果

- 修正版源码 `7e3fa6ff5fe21e5ede1d0e7d44ab3536eb846f4b` 的 [完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/42973646239) 已通过。最终打包镜像在关闭 USB 参数的 QEMU 检查中出现 STRESS_OK/CLEAN，心跳 4→7；2026-11-11 06:54 UTC 写入 ESP 并临时启动真机。
- 首次原生主机检查通过 nonce、精确构建身份和日志校验；日志包含完整进程和文件系统自检成功标记。固件 QUSB2 未就绪，先恢复后初始化端点，未再观测到启动端点超时。初次 status 为 resets=2、cmd_errors=0、reply_overflow=0、last_error=none，使用 bank 4 原有旁路。
- 首次重连脚本在 detach 后立即 attach，遇到 Windows 共享暂未重新导出的 `Device not found`。该失败记录保留在 `artifacts/usb-reconnect-7e3fa6f/`；设备随后重新可见，接入后 nonce/构建/日志仍通过、cmd_errors=0。脚本补上最长 30 秒、最多 20 次的受限等待，每次失败日志保留；这次桥接释放时序问题不作为内核成功或失败证据。
- 随后完成正式十轮 USB/IP detach/attach。每轮先注入未换行的 `echo stale-prefix`，重连后首条 ping、随机串、精确源码身份和完整日志校验均通过。正式序列 resets=4→24，cmd_errors 始终 0，reply_overflow=0，日志覆盖丢失计数 0；约 22 秒完成。证据 `artifacts/usb-reconnect-7e3fa6f-10/report.json`。半条旧命令未污染下一会话。这十轮不计为手机重启或物理插拔。
- 不支持的 GET_DESCRIPTOR 类型 0x99 正确返回 STALL，随后正常 device descriptor 读取和随机串往返成功；resets 保持 24、cmd_errors 保持 0、配置未丢失。证据 `artifacts/usb-control-stall-7e3fa6f/report.json`，证明此类 EP0 控制请求拒绝后的恢复。
- 新镜像连续约 52.734 秒完成 256 次随机数据往返，长度覆盖 1、49、50、51、113、114、115、241、242、243、249、250 字符；测试包含回复长度跨 64/128/256 字节边界。cmd_errors=0、resets=24 均未增长，无回复队列溢出。证据 `artifacts/usb-continuous-7e3fa6f/report.json`。旧基线此前同类 256 次测试也通过，错误保持 7；旧基线问题主要随重连增长。两轮均是功能与短时稳定性检查，未建立吞吐或延迟性能基线。
- 首次日志响应 2,282 字节，截止序号 2,123、覆盖丢失计数 0，SHA256 `a2ebb5e7a6fd6387b2c2799ec304aecc28fdc928bc6779bee38ecb90c5cd59dd`；十轮重连末轮日志 SHA256 `0195b23127bdb58b53da25c25158e6a832f460559bfd6d5d935ba01e5f3aae9e`。
- 原始内核 SHA256 `722a10ca1c708f85e7175e8057a9e58d9d86e06d2f8e3c234a3a6d303791d023`，打包内核 `dfe2b6ec3b73736e53c2b105b775ca4032a72ec9d7354e4d48c7ab400e4130bb`，128 MiB ESP `0fa69a26986f7529516810a70ed63290e9a39b16f5f8e3b507cc000bfec71e47`。镜像及报告在 `artifacts/usb-reset-ready/`。

当前同一镜像完成 10 轮整机启动；十轮 USB 重连已通过。A7 的十轮次数与冷/热覆盖已完成。后续同镜像启动复用已记录、身份一致的 ESP，每轮重新核对构建编号、自检结果和变化心跳。完整 RAM、SMP、USB 中断、持久日志和 UFS 仍分别验收。

D6/D7 基本可写 RAM 文件系统 → C5/H2 进程回收压力 → E5 日志和 F3 交互通道 → F2 原生存储。G 组可与部分硬件驱动并行，启动 Apple 用户态不要求先完成相机、蓝牙等全部外设。

<!-- polaris-boot-series:start -->

### A7 同镜像整机启动序列

已完成 10/10 轮同镜像整机启动；冷启动按用户报告单独记录。每轮使用原生 USB 校验源码身份、PID 1、子进程、RAM 文件和 1,000 次进程自检标记、变化心跳，以及零 USB 命令错误。复用已验证 ESP；未再次写入 userdata。元数据见 [启动序列记录](usb-boot-series.json)。本轮十轮次数及冷/热覆盖已通过；冷启动条件来自用户操作确认，长期稳定性仍按 H 组分别验收。

| 轮次 | 进入方式 | 命令错误 | 日志丢失 | 日志 SHA256 |
| --- | --- | --- | --- | --- |
| 1 | 首轮方式未单独确认 | 0 | 0 | `a2ebb5e7a6fd6387b2c2799ec304aecc28fdc928bc6779bee38ecb90c5cd59dd` |
| 2 | 按热启动指令执行 | 0 | 0 | `f2f246c79708dd526e30eade836d0ea8e8591ff21b9fb465b180165dc6f7cb7d` |
| 3 | 按热启动指令执行 | 0 | 0 | `8d05ab8a08b6d3662bd4a764035937ff40da997e09e4b1d91ab10cd31f7f2b61` |
| 4 | 按热启动指令执行 | 0 | 0 | `8bfeb7bc4e4c379d0e7e2f47c831a0067c40a0f83886b5f923eeb1ccd49cccd4` |
| 5 | 按热启动指令执行 | 0 | 0 | `508841caf4ddfae3b6f1df6d216fc0bf855f536d4dae343a35d9439c07092811` |
| 6 | 按热启动指令执行 | 0 | 0 | `6a4315ab12984516966fcb7746e701f96b48ad3d7df585ade4a312dfa122cabc` |
| 7 | 按热启动指令执行 | 0 | 0 | `4165bf8ca56846a2155f62e137b43d02d95d42f8b8a5d4c24082b3a793468c5c` |
| 8 | 按热启动指令执行 | 0 | 0 | `497188444d61384763f95bf527409af1e213f9417507f6bea7161f651098e69e` |
| 9 | 按热启动指令执行 | 0 | 0 | `c10115b4eaa4b0871ccf2c5af61a85652e7f2422c3ec62dbae16fb22a9192605` |
| 10 | 用户报告关机后上电 | 0 | 0 | `efc5bdccfda39ae7a3bbeb4dd211a52cea252b6c4c4d745318daafd49fa831e2` |

<!-- polaris-boot-series:end -->

F2 已完成控制器/队列/SMMU 盘点和自有队列单次 NOP：在 `0x1023bc000` 的 16 KiB 新分配中验证描述符、请求及 NOP IN 响应 DMA，OCS=0、tag 匹配、保护区完整，最终 UTRL run-stop=0、doorbell=0。候选 bank 2 配置未改；首轮误把 FSR FORMAT 当故障的问题已按 `0xc00001fe` 掩码修正并保存记录。原有自检与变化心跳通过，USB 错误、回复溢出和日志丢失均为零。查询源码 `9feeaca` 已进一步通过 DEVICE/UNIT 0 READ_DESCRIPTOR：UFS 2.1、4 KiB 块、59,064,188,928 字节容量线索，原有自检/持续心跳和 USB 正常；当前 `88cf31d` 已完成单项 PRDT INQUIRY 与 READ CAPACITY，容量一致，原有自检/心跳通过且 USB 错误/丢失均为零。随后 `996fb95` 已在一轮普通重启中完成六个 4 KiB 扇区读回和主 GPT 校验，逐字节匹配参考；随后 `2791907` 已完成 ESP 中固定 FAT32 文件的原生读取与独立内容核验；当前 `abc75cf` 已通过固定 devfs 文件的实际读取与事件交接。下一步为通用只读块设备/文件系统。见 [UFS 计划和结果](ufs-readonly-plan.md)。

当前成功包：`artifacts/ufs-node-event-ready/`；实际启动卷：`private/ufs-node-event-deploy/update/updated-esp.img`，SHA256 `5e10f59584d2e7c01cb3f2bd50140d15636d97ad3fc630ac2e11a69a6e5545be`。本轮仅一次普通重启；十轮可靠性资格仍属于原 USB 基线。


### F2 首次原生扇区读取验收

源码 `996fb95`、一轮用户普通重启：6 条 READ(10)，LUN 0/LBA 0～5，各 4096 字节，
共 24,576 字节，经高于 4 GiB 的独立数据页返回，逐字节匹配预启动独立参考。
保护 MBR、主 GPT 头和表 CRC、容量及分区边界检查通过。两个后续 USB 采样读取
相同启动缓存，不会再次提交存储命令；采样间心跳继续增长。原有 IO/PROC/TMP/
1,000 次进程自检通过，USB 命令错误、回复溢出和日志丢失均为零。
SMMU 故障位无新增、配置未改，无介质写命令。未读取备 GPT，未挂载 UFS 文件系统，
没有 Apple 用户态；本轮不是持续读取压力或性能基准。[证据](ufs-read-result.json)。


### F2 固定 FAT32 文件读取验收（2026-11-12）

源码 `2791907`，一轮用户普通重启：在核验主 GPT 后，从固定 128 MiB ESP 范围
解析 FAT32 BPB、双 FAT、短名目录和簇链，读取 `EFI/UniXNU/READTEST.BIN`。
共 30 条单块 READ(10)，文件 9 簇、8193 字节；每个读取块 CRC32、簇链和完整
文件内容/ SHA256 均与更新后镜像及独立 mtools 结果一致。两个 USB 采样返回相同
启动缓存，未重发存储命令；持续心跳增长至 8，原有 IO/PROC/TMP/1,000 次进程
自检通过，USB 命令错误、回复溢出和日志丢失均为零。SMMU 未改、故障位无新增，
无介质写命令。此项是有限解析器的文件读取，不是 VFS 挂载或通用块设备验收。
[公开证据](ufs-fat-result.json)。下一步为只读块设备/VFS 接口；最小 Apple 用户态
可按既有复用计划并行推进，本轮仍未启动 Apple 用户态。

### F2 历史候选：固定文件的 devfs 接口

在已通过的 FAT32 文件读取上接 `/dev/polaris-esp-file`，本轮只验证固定文件的
用户态系统调用与实时物理读取，不将其记为通用块设备或 FAT 挂载。检查清单和
边界见 [第八轮候选](ufs-readonly-plan.md)。当时成功真机基线仍为 `2791907`，
本轮候选 `8959e7f` 已通过完整 CI（16 份运行报告）、最终打包 QEMU 模型和文件更新全卷回读；已按用户要求停止，未重启。下一项是用户手动普通重启后的独立原生 USB 验收，尚不计入硬件通过。

F2 devfs 本轮真机报告 `IO_FAILED stage=81 errno=060`（顺序读后端超时），**未通过**。
原失败处理触发 panic，USB 工作线程随之停止，无法从 CDC 补取日志。诊断版
`2dd96b8` 将保留运行并输出队列/工作线程进度；当时最后成功硬件基线仍为
`2791907` 的固定 FAT32 文件读取。详见 [失败记录和诊断计划](ufs-readonly-plan.md)。


诊断候选 `2dd96b8` 已通过完整 CI（86 项主机检查、16 份运行报告）与最终打包
QEMU 模型，已更新并完成全启动卷回读/缓存同步；按用户要求停止，未重启。
下一步采集 NODE_TIMEOUT 进度和原生日志，不将诊断镜像部署记为超时修复。


诊断版 `2dd96b8` 的原生 USB 日志已成功取得：首个请求仍 Pending、已认领/物理读
均为 0，故障定位到交接阶段。失败后保活和原生 CDC 取证成功，硬件文件接口仍未通过。
[证据摘要](ufs-node-timeout-result.json)。候选 `abc75cf` 改用事件唤醒/等待，QEMU
也改测真实异步队列；完整 CI 的 86 项主机检查、16 份运行报告通过，实际队列认领
9 次、模拟块读 10 次。已更新并全卷回读/同步，标题 `polaris: UFS NODE WAKEUP`，
按用户要求停止、未重启。下一项是该候选的原生文件设备验收。


### F2 固定 UFS 文件的 devfs 访问通过（2026-11-12）

`abc75cf` 本轮普通启动通过。9 个请求均完成、无超时/取消，10 条原生 READ(10)
顺序及块 CRC 匹配实际 ESP。`open/read/pread/close`、跨边界、EOF、负偏移、重复读、
写入拒绝与重开检查通过；累计返回 20502 字节。三次 USB 捕获中完成轨迹一致、
心跳继续变化，原有 IO/PROC/TMP/1,000 次进程自检通过，USB 错误、溢出和日志丢失
为 0。SMMU 未改、无新增故障位，无介质写入。[证据](ufs-node-result.json)。
固定只读字符设备视图已通过；通用块设备、文件系统挂载和 Apple 用户态仍待推进。


### G1 输入审计与无需重编内核的测试边界（2026-11-12）

已取得同一目标 OS DMG，并使用固定 UniXNU 原样审计器核对原版 launchd/dyld。结果见 [输入审计](apple-userspace-audit.json)和 [复用说明](apple-userland-reuse.md)。G1 的静态子项通过；G2～G4 的运行项仍未完成，手机没有更新。

同一 `abc75cf` 原始内核在 QEMU 跑通 Linux 新编译的自制程序包，离线手机文件树中打包内核和 EFI 完全不变，仅 RAM 根与启动参数不同，见 [内核复用证据](userspace-kernel-reuse.json)。这减少用户态试验的 macOS 编译次数，仍需更新文件和普通重启；当前没有热上传/执行，Apple 签名与加载器配置变更也仍需单独编译验证。


### G1/G3 当前实施：原版 dyld 的最早执行

源码候选 `f916ebb` 增加显式开启、仅 PID 1/两份精确原版 CDHash 的诊断入口；不授予 entitlement/平台身份，不将它描述为完整 Apple 信任链或 AMFI 集成。原版文件字节保持不变。先验收 startup 的真实 EL0 指令，再验收 `mach_init` 与页大小全局量；关闭模式、损坏代码页必须拒绝。最近已验证的真机内核为 `abc75cf`；候选构建和下列 QEMU Apple 早期执行验收已完成，文件已更新、尚未重启，真机 Apple 执行未验证。

53 个 cache/subcache 的静态检查见 [完整结果](apple-dyld-cache-audit.json)，全部为 4K 对齐映射、16K 签名块，代码块和原 System trust-cache 条目通过；G3 的运行项不因此通过。首次入口探针之后，按真实依赖接入已有大签名块路径及共享缓存。


### G1/G3 本轮验收结果（f916ebb，QEMU）

[完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43155794427)通过，16 份原有运行报告保留。新增原版 dyld startup、`mach_init` 返回和 `prepare` 入口，以及关闭模式、损坏代码页两组对照全部通过。最终打包内核也重复通过原版 `mach_init` 正向与损坏页拒绝；共 7 组记录，见 [原版执行证据](apple-userspace-runtime.json)。所有正向代码页错误计数为 0，损坏页触发 namespace=3/code=2 和 bad_hash=1；原版文件没有改写。

当前不再是只有静态样本：原版 dyld 的真实 EL0 执行已模拟取证。尚无原版 launchd 主函数、共享缓存实际装载、完整信任链、服务或界面通过。准备好的真机候选保留 `abc75cf` 的旧内核/ADT/RAM 根回退项，FAT 回读与最终打包 QEMU 已通过；后续已由用户准备 Mass Storage，文件写入与完整回读通过；更新后停止设备写入；用户随后普通重启并回报 DYLD/1 缺 libSystem。


### 真机文件更新，运行验收尚待进行

`f916ebb` 的 Apple dyld 候选已通过固件 Mass Storage 写入，12 个文件及 128 MiB ESP 的直接回读通过，见 [部署记录](apple-userland-deploy.json)。旧卷先精确核对，变更只在 userdata 的 ESP 窗口内，boot 和其他分区未写入。保留 `abc75cf` 的原内核/ADT/RAM 根回退项。更新时保持 Mass Storage 等待用户手动重启；用户后续画面回报及其限定见下文，不能以部署成功代替运行验收。


### 真机 Apple 早期执行：缺库退出已定位

用户启动后回报 namespace=6/code=1 且缺 libSystem，支持真机原版 dyld 到达依赖库解析阶段；证据为用户抄录画面，未取回原生 CDC 日志或核对精确 PC/构建身份。服务器用同一打包内核与 RAM 根继续运行，复现 `/usr/lib/libSystem.B.dylib` 不存在且没有 dyld cache 的 DYLD/1，见 [诊断](apple-dyld-missing-libsystem.json)。原版 launchd 主函数、共享缓存实际装载与系统服务均未通过。

下一阶段：同版本缓存的文件读取/分页映射后端、已有 16K 签名块完整性路径、缓存成员授权，然后推进 libSystem 和真实动态链接。不把复制同名文件、静态缓存审计或预链接配置存在记为运行通过。本轮仅诊断和保存证据，没有再次更新或重启手机。


## G3：原版小缓存签名和映射已模拟通过（57a7d7f）

[完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43166582757)通过：88 项主机检查、16 份原有运行报告。复用固定 UniXNU 已有的 `native-4k-signature-hash-cache.patch`，启用 `CONFIG_XNU_NATIVE_CS_HASH_CACHE=y`，没有改写 Apple 缓存文件或其签名。新增显式 `polaris-cache-probe=1` 的诊断授权：仅 PID 1、有效 ad-hoc / 16K CodeDirectory、53 个已审计原 System trust-module 成员的精确 CDHash。它只授权签名登记，不授予平台身份/entitlement，也未加载 AMFI 或 Apple trust-cache runtime。

自制无 libc 的 PID 1 打开同一原版 `dyld_shared_cache_arm64.02`（49,152 字节），实际执行 `F_ADDFILESIGS_RETURN`、32 KiB `mmap(MAP_PRIVATE, PROT_READ|PROT_EXEC)`、8 页数据读取、`munmap` 和 `close`，随后继续定时运行。QEMU 只读观察到：两个完整 16K 签名块认证成功、8 次原生 4K 缺页校验、一个就绪哈希缓存，bad_hash/no_hash 均为 0；实际 EL0 和 4K MMU 配置同时取证。没有执行缓存中的原版指令。

[10 组运行证据](apple-cache-runtime.json)均通过：旧内核仍拒绝 16K 签名；新内核正向；关闭授权返回 EPERM；隔离副本分别损坏第 0、1、2、3、4 个 4K 区段，每组均返回 EBADEXEC、bad_hash=1、未发布就绪缓存。第 4 区段的失败发生在第二个签名块，此时第一个完整块计数为 1。最终打包内核再重复通过正向和第二块损坏对照。主机未写入来宾寄存器、代码或返回值，所有 Apple 原输入保留不变。

真机候选在 `artifacts/cache-phone-ready-57a7d7f/`，菜单默认 `polaris: ORIGINAL CACHE 16K / 4K`。旧原版 dyld 探针和 `abc75cf` 自检的内核、ADT、HMAC 与根目录均以原字节保留为另两项；共享 EFI 字节一致。128 MiB FAT 文件逐一回读通过，最终打包内核正向/篡改检查通过；用户随后准备固件 Mass Storage，现已完成受保护文件更新：旧 ESP 精确匹配，主/备 GPT、容量和 USB 身份核对通过，16 个文件哈希及 128 MiB 整卷 O_DIRECT 回读通过，实际变更 61,091,840 字节。实际部署 ESP SHA-256 为 `65a33bdf49b0b73cc83f346664af99e5f86f801b7bb46fc474e63f30faaf32fa`；独立候选和实际更新保留的卷布局/时间戳不同，整卷哈希不同，但所有文件哈希一致。boot 与其他分区未写入，已停止设备操作，保持固件 Mass Storage 等待用户手动重启，见 [部署记录](apple-cache-deploy.json)。部署成功不计为该探针真机运行通过。

真机验收使用 `scripts/check-polaris-cache-usb.py`：核对原生 CDC 身份、精确构建 SHA、随机 nonce、USB 健康计数、完整零丢失日志、自制程序的 CACHE_OK 和两次独立快照间继续变化的心跳。该程序只证明自检结果与存活，内核内部 2/8 计数当前仅在 QEMU 直接观察；旧进程/压力 USB 验收器保持原要求。失败程序保持运行，不因预期签名错误主动 panic。

下一步分开处理：先完成此小缓存探针真机验收；再在 QEMU 复用只读 APFS/虚拟块设备路径，核对存储 ABI、组件绑定与 crypto_bootstrap 配置，提供同版本 OS/SystemOS 文件后端及 53 个缓存路径，继续观察真实 dyld。只读存储声明闭包已缩小为 12 个可执行组件和 7 个内核接口，但计划工具仍返回 loadable=false，未生成或启动该组件组合。主机已转换并核对只读 OS raw 输入；这不构成内核 APFS 挂载证据。原生 UFS 的通用可分页后端另行适配，当前固定 8193 字节字符设备不能装载约 3.3 GB 缓存。

目前 G3 的原签名页路径有了实际小文件运行证据；完整缓存映射、libSystem 初始化、TLS、原版 launchd 主函数、服务和 iPadOS 界面仍未通过。


## 原版小缓存探针真机通过（57a7d7f）

用户完成普通重启后，服务器接入 `1209:0001 / POLARISDBG01` 原生 CDC。两次独立捕获的构建身份均为 `57a7d7fa6b49e97ccdade7eb4e1928e142640e9c`，ping 与两组随机 nonce 往返通过，日志均含 `POLARIS_CACHE_START pid=1 pagesize=4096` 和 `POLARIS_CACHE_OK original=1 blocks16k=2 pages4k=8 map=RX cleanup=OK`。日志 end 从 1767 增至 1794，末尾心跳模 10 数字从 5 变为 6；cmd_errors、reply_overflow 和 dropped 均为 0。[真机结果及 8 份原始 USB 响应摘要](apple-cache-phone.json)已保存。验收过程未写介质或重启手机，当前保持常驻探针运行。

本轮真机通过范围为：自制 PID 1 对未改写的 49,152 字节原版 subcache 完成签名登记、32 KiB RX 映射、8 页数据自检和释放/关闭，并经原生 USB 核对精确构建和存活。内核内部“两块 16K / 八次 4K”计数与损坏页拒绝对照仍只在 QEMU 直接观察，未从手机导出；原版缓存代码没有执行。原全套进程/临时文件/UFS 自检的真机基线仍是 `abc75cf`，不以本轮较小程序替代那套验收。

下一项转到服务器侧的只读文件分页后端：核对现有 APFS/虚拟块设备方案的存储 ABI、绑定与 crypto_bootstrap 组合，提供同一目标 OS/SystemOS 文件路径，再继续原版 dyld 的完整共享缓存和 libSystem 加载。完整 cache、libSystem 初始化、TLS、原版 launchd 主函数和 iPadOS 界面仍待验证；当前无需再次准备 Mass Storage。


## G2/G3 只读存储组件：基础接口已模拟验证

独立原版 crypto bootstrap 输入和公开 IOStorageFamily 注释对象已由 [构建 43174437089](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43174437089) 编译通过。原始内核需要后续组件组合，打包/平台启动工具会拒绝未组合输入；手机仍运行已验收的 `57a7d7f` 小缓存探针，本阶段未访问或更新手机。

[基础接口证据](apple-storage-interface.json)记录：同一原版目标的 12 个组件可执行区段保持字节一致；实际 190 个存储方法槽中 176 项对应源内核绑定，14 个为原类自有方法，抽象析构陷阱和可用基类析构分别核对。QEMU 直接观察原存储类/源 IOService 大小为 0x90/0x88，原 AES/crypto 父类/源 IOService 大小为 0x120/0x88/0x88，继承链正确，CPU 为实际 4K 翻译。原 corecrypto 表位于原组件范围，首 16 项中 7 个回调指向原可执行区段，crypto_init/prng_ready 均为 1。

首轮 AppleImage4 因缺少 chosen.crypto-hash-method 退出，原失败保留；补上目标所用 sha2-384 后继续。只在 bsd_init 入口观察尚未加载的类会读到零，因此采用已有 offline-apfs-load 的真实 OSKext API，在 VFS 初始化后的检查点取证。该加载 API 返回 0；旧诊断的 native_apfs_registered 仍为 0，不能据此声称 APFS 注册或挂载已经通过。后续使用实际 VFS 注册返回、设备发现与只读挂载另行验收。

[绑定元数据](apple-storage-bindings.json)只包含接口名、地址与哈希，没有 Apple 可执行代码。AES 的共同 168 槽取自当前源 IOService 的真实 vtable，其余 13 项沿用固定 UniXNU ABI 头和绑定记录，并逐项比对原版 vtable；后续还必须检查新编译子类的全部 181 槽并运行 KAT。下一步编译已有 NativeAES/NativeVirtioBlock，组合后验证实际只读设备、两卷 UUID/设备名、挂载与 APFS 上小缓存的映射。完整 cache、libSystem、原版 launchd 主函数和真机通用存储仍未通过。

构建首轮因当前 Apple Clang 不识别一个上游警告抑制选项而失败，源码内核本身已链接成功；已移除该无效选项并保留 -Werror，修正版编译通过。原 RAM 根配置的 [完整回归 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43172241674)也通过；不能将它记为新存储路径通过。
