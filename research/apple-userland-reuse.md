# Apple 用户态启动：现有适配复用盘点

更新时间：2026-11-12。目标仍为 iPadOS 17.7.11 / 21H461 / iPad7,1，最近完整真机自检基线为 `abc75cfb6a7f3a209af4865f575a9b70387c916e`；`f916ebb` 原版 dyld 已通过 QEMU 早期执行，真机按用户画面回报到达缺 libSystem 的依赖查找阶段。新候选 `57a7d7f` 已在 QEMU 通过原版小缓存的 16K 签名/4K 缺页校验和拒绝对照，该小缓存探针现已普通启动，并通过精确构建原生 CDC、程序自检和变化心跳的真机取证；内部哈希计数和拒绝对照仍仅模拟观察。完整缓存和原版 launchd 主函数仍未通过。

## 结论与基线

验收表的“未验证”首先应转为现成实现的核对和集成，不代表从零开发。首次 Apple 用户态尝试可以采用已有项目的实验性 AMFI/entitlement、平台身份与 Sandbox 兼容思路，不需要先恢复完整 Apple 安全服务或全部外设。仍要保留当前成功镜像，按实际依赖逐项验证。

普通 K20 / Mi 9T 对应 davinci；K20 Pro / Mi 9T Pro 对应 raphael，二者的适配来源要区分。查到的代码如下：

- [Antcating/ios-davinci](https://github.com/Antcating/ios-davinci/tree/3b0dab6970850ba4b02017572d920f81a09e0bca)，固定提交 `3b0dab6970850ba4b02017572d920f81a09e0bca`。其来源是 ios-sweet，公开构建链为 XNU 8796 / iOS 16；README 仍包含 sweet 模板内容，本文依据具体补丁代码盘点，不把其界面宣传直接当作本项目或该板的验收证据。
- [UniXNU-raphael README](https://github.com/JohnLeeeeeee678/UniXNU-raphael/blob/main/README.md)说明其复用 guacamole / ios-sweet 的 SM8150、XNU 8796 与 iOS 16.7.x 路线；可参考封装和工程组织，不能据此确认当前 iPadOS 17 路线兼容。
- 本项目固定的 UniXNU `37987aea410691df3c1e0e146a1bfd6127932149` 使用 XNU `10063.141.1` / Darwin 23.6，源提交 `d8b80295118ef25ac3a784134bcf95cd8e88109f`。版本差异需要逐个核对接口、结构体与运行行为。旧内核的二进制偏移、结构体布局和整套补丁不能直接套用。

## 可复用内容与迁移边界

| 项目 | 已查到的实现 | Polaris 下一步 |
| --- | --- | --- |
| AMFI 接口占位 | davinci `0025-sweet-builtin-amfi-no-entitlements.patch` 注册基础 AMFI provider，代码页哈希仍由内核处理 | 比对 Darwin 23 的 AMFI 回调和初始化顺序；占位仅作为启动阶段选项 |
| Entitlement / 平台身份 | `0035-sweet-amfi-entitlements-platform-binaries.patch` 从经签名哈希校验的 XML 解析权限，并对根文件系统中的签名程序设置平台标记 | 优先移植语义；核对新版本 entitlement 格式和 API。旧版 DER-only 路径不提供权限，不能推断已覆盖目标镜像 |
| CS_SIGNED | `0036-sweet-amfi-cs-signed.patch` 补齐状态，解决进程的签名 blob / entitlement 查询链 | 对照当前 flags 传播及 csproc_get_blob 行为，作为有明确触发条件的小补丁验证 |
| Sandbox 兼容 | `0050-sweet-sandbox-policy.patch` 注册名为 Sandbox 的 MAC policy，避免缺失策略引起 ENOPOLICY，并处理少数需返回数据的操作 | 记录 iPadOS 17 实际调用和返回值要求，迁移最小兼容行为，不假定旧版本操作表完整 |
| 4K / 签名粒度 | 固定 UniXNU 已有 `native-4k-user-abi.patch`、`native-4k-signature-hash-cache.patch` 和 `check-code-signing-granules.py` | 先核对实际启用选项和目标 Apple 程序 CodeDirectory 粒度；代码存在不等于当前配置已启用或已获 Apple 程序验证 |
| Trust cache / 预链接 | 固定 UniXNU 已有 `native-trust-cache-runtime.patch`、NativePrelink；本仓库已有 `polaris_prelink_defconfig` 和 board.toml 的目标镜像/组件配置 | 作为已有实现集成；先用目标文件与 ABI 验证组件依赖，不把预链接配置的存在当成成功结果 |
| 根镜像 / 系统服务 | davinci `tools/mkiosroot.sh`、`sweet-root-tweaks.py` 及相关 HFS/KeyStore/身份补丁提供依赖线索 | 对照 iPadOS 17 的根文件、dyld cache、launchd 和服务依赖重做清单；D22AP 身份与 iOS 16 路径不直接搬用 |

具体签名补丁链接：[0025](https://github.com/Antcating/ios-davinci/blob/3b0dab6970850ba4b02017572d920f81a09e0bca/xnu/patches/0025-sweet-builtin-amfi-no-entitlements.patch)、[0035](https://github.com/Antcating/ios-davinci/blob/3b0dab6970850ba4b02017572d920f81a09e0bca/xnu/patches/0035-sweet-amfi-entitlements-platform-binaries.patch)、[0036](https://github.com/Antcating/ios-davinci/blob/3b0dab6970850ba4b02017572d920f81a09e0bca/xnu/patches/0036-sweet-amfi-cs-signed.patch)、[0050](https://github.com/Antcating/ios-davinci/blob/3b0dab6970850ba4b02017572d920f81a09e0bca/xnu/patches/0050-sweet-sandbox-policy.patch)。

## 调整后的推进顺序

1. 保留 UFS QUERY → PRDT/容量/块读取这条硬件支线；同时启动 G1～G4 的 Apple 用户态支线。最早的 Apple 程序可从适合的 RAM 根环境尝试，不以完整原生存储、GPU、触控或 SMP 为先决条件。
2. 盘点目标镜像中最小 Apple 程序、签名/entitlement、dyld 与共享缓存依赖；结合固定 UniXNU 的已有实现，生成“已覆盖 / 缺接口 / 需移植”清单。
3. 单独验证 Apple Mach-O 执行和首次动态链接。将失败归因于代码页、签名/权限、加载器、线程/SIMD 或 Mach 接口，避免一次套入全部旧补丁后失去定位能力。
4. 再尝试原版 launchd，使用 USB 日志定位缺失的服务接口；优先复用已经解决同类问题的补丁。首次启动标准是原版程序真实执行并有可定位日志，后续再扩展守护进程与界面。

当前 PID 1/子程序的精确 CDHash 测试策略只服务于已验证的自制程序基线；Apple 启动实验需要单独选择和验证签名/entitlement 路径。这里尚未改动内核策略、刷新手机或触发新内核构建。此前“数周”是未经复用盘点的保守预算；应在版本移植和首个 Apple 程序测试后重新估算，不把 G 组每个待验证项都按原创开发计时。

## 第一项已执行的兼容检查

新增 `scripts/check-apple-signature-gate.py`，复用已有检查思路，在 Linux/macOS 编译实际 `ubc_subr.c` 中的 CodeDirectory 页粒度判断。已先对保存的 Darwin 23 补丁源执行，随后对本次 macOS CI 实际组装的源码执行，8 个组合均通过；[结果](apple-signature-gate.json)记录完整输入与提取代码哈希。4K 内核配默认 integrity-cache=0 时，4K 签名页通过、16K 签名页被拒；已有 cache=1 路径允许后者进入下一步。这只测试一道实际检查，不证明完整签名完整性、entitlement、dyld 或原版程序执行。

查询内核 `9feeaca` 的完整 CI 已通过；发布的报告来自这次实际编译输入，提取 gate 的哈希和 8 个结果与先前本地检查一致。当时尚未取得目标 Apple 二进制，未将条件性结果当作实际失败原因，也未直接改变成功内核配置；2026-11-12 的样本检查见下文。

## 已取得的目标输入与预链接计划

已通过 Apple CDN 的 ZIP 范围读取取得同一 `17.7.11 / 21H461 / iPad7,1 / j120ap` 镜像的 BuildManifest、KernelCache、DeviceTree 和两份 trust cache；四个组件均与 Erase identity 中的 SHA-384 Digest 相符。该阶段未下载整个 IPSW 或系统根 DMG；后续已取得 OS DMG，见下文。manifest 内的摘要核对不等于 Apple 签名信任链验证。必要元数据见 [apple-target-inputs.json](apple-target-inputs.json)，Apple 二进制只保留在本地。

解压后的 ARM64 原版内核 SHA256 为 `f18b4df865405f2a287b84977d7aca745e056d656d9fc9df1662970ce2969de7`，与固定 UniXNU 的目标 pin 完全一致。板配置所选的 10 个预链接根组件均存在。原版版本是 `10063.142.1.703.8`，自编源码是 `10063.141.1`，仍须验证 ABI 与运行行为。

复用了固定 NativePrelink `3380d3006068cdaab69454b37668e6b532ed2584` 的只读 `plan`，输入为该原版内核与本次 CI 通过的查询源码 `9feeaca` 对应打包内核。依赖闭包为 18 个可执行组件、7 个 kernel interface；13,156 个组件指针和 24,460 个内核符号指针均完成计划解析。该结果只覆盖工具枚举的绝对指针，工具仍明确返回 `instruction_relocation_complete=false`、`abi_qualified=false`、`loadable=false`。没有生成或加载组件，也没有运行 Apple 用户态程序。

下一步直接沿用已有用户态审计及预链接生成工具，对实际 launchd/dyld 样本核对代码页、trust cache 与加载器依赖，再选择最小执行实验。不能把静态指针计划或缓存内存在组件，记为 G2 的运行验收通过。


## 已完成：原版 launchd / dyld 静态检查

2026-11-12 从同一 Apple CDN 镜像范围下载 `044-76040-038.dmg`（5,157,406,069 字节），ZIP CRC32 与所选 BuildManifest 的 SHA-384 Digest 均相符。未下载整个 IPSW。Linux 以只读 UDIF/APFS 解析提取原样 `/sbin/launchd` 和 `/usr/lib/dyld`，复用固定 UniXNU 的未修改 `audit-target-userspace.py`；Apple 文件只保留在私有工作区。

[审计记录](apple-userspace-audit.json)确认两者为普通 ARM64 subtype 0，最低版本 17.7；所有实际 segment 布局可直接表示为 4 KiB 映射。原 CodeDirectory 的签名页均为 4 KiB：launchd 核对 131 个代码槽，dyld 核对 223 个。二者 CDHash 均各自唯一出现在同镜像的原 static trust cache 中。修改 launchd 的一个代码字节、移除 trust-cache 条目两个负向对照均被审计器拒绝。这些结果没有验证完整 CMS/Apple 信任链、entitlement 传播、SIMD、动态链接、系统调用或原版程序执行；G1 只能标记静态部分通过。

实际文件摘要与固定 UniXNU 的目标样本一致：

| 文件 | 字节 | SHA-256 | CDHash |
| --- | --- | --- | --- |
| `/sbin/launchd` | 561856 | `8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25` | `1d52c69747dffc4b1937d241bdbc9cee3b22df18` |
| `/usr/lib/dyld` | 937056 | `f8df3c58fae6e409db740df22c98aa699aee110b73523541a63f1bd5e23cf705` | `b479f90e05545314cb843410c3d98ef84d926710` |

共享缓存所在的 SystemOS DMG 后续也已下载并审计，详见下文。已复用上游 dyld fixture/probe 的方法准备独立 Apple 执行实验；当前成功镜像保留。

## 已验证：自制用户态可复用内核

[复用记录](userspace-kernel-reuse.json)：在 Linux 编译并签名另一份自制 PID 1/子程序，沿用 `abc75cf` 的同一原始内核运行 QEMU，进程、I/O、`/tmp`、1,000 子进程压力和常驻检查通过。随后制作离线文件树预览，只替换 RAM 根 `root.img` 与配置中的两个 CDHash，并移除不适用于该程序的 `polaris-ufs-node=1`；其余文件均按原 manifest 校验。已经打包的手机内核逐字节保持 SHA-256 `4b3126eb76b27b0113696951b47a39174f6eef6d7b5340e012674982f64bb4bc`，EFI 也未变化，无需 macOS 或重新编译内核。此预览没有安装、没有重启、没有新增真机验收。

适用边界：自制程序和 RAM 文件变更可在 Linux 重做程序包与配置；实际应用仍需一次 Mass Storage 文件更新和普通重启。原生 CDC 当前只有固定诊断命令，没有程序上传、交互 shell 或热执行能力。内核驱动、系统调用、加载器和安全策略改变仍需重编内核。当前策略要求自制程序具有 `CS_ADHOC|CS_HARD|CS_KILL`，按 PID 1/直接子进程及精确 CDHash 授权，并拒绝平台标记和 entitlement；实际 Apple 样本的 CodeDirectory flags 是 `0x2`，不能沿用该自测策略直接执行。RAM 树另有限制：16 个条目、32 MiB 镜像，不足以放入完整系统和共享缓存。

用户态复用验证时手机保持运行；后续在候选验收完成并由用户准备 Mass Storage 后，才执行下述文件更新，完成回读与同步后仍等待用户手动重启。


## 已完成：共享缓存输入检查

[共享缓存审计](apple-dyld-cache-audit.json)：原 SystemOS `044-76034-038.dmg`（3,303,014,400 字节，ZIP 内压缩传输 1,321,525,120 字节）通过 ZIP CRC32 和 BuildManifest SHA-384；SHA-256 与 board.toml 的 pin 一致。只读提取 53 个 cache/subcache（不含 symbols），共 3,301,588,992 字节。完整的原 CodeDirectory 签名块逐一核对，53 个 CDHash 均唯一存在于同镜像的原 System trust cache。四个 4 KiB 分片中分别翻转一位，均使对应完整 16 KiB 签名块哈希失配。

实际 cache 映射地址、大小和文件偏移均为 4 KiB 对齐；代码签名块则全部为 16 KiB。这两种粒度不能混为一谈。当前探针沿用默认 `XNU_NATIVE_CS_HASH_CACHE=0`，足以检查签名页为 4 KiB 的 launchd/dyld 入口；真正加载 cache 前，需要启用并验证固定 UniXNU 已有的大签名块缓存路径，不能把本次静态审计记为内核 fault-path 通过。

审计复用了固定 UniXNU 的 cache 检查逻辑；Linux 适配仅替换编译器、输入路径和头文件 pin 校验。结构偏移由 Apple dyld `a571176e8e00c47e95b95e3156820ebec0cbd5e6` 原格式头经本地主机编译得到。APFS 读取用 Dissect，压缩资源块改用固定 libzbitmap，并对未加密数据使用原 extent 查找后的有界区间读取；提取结果再以完整原签名块校验。没有改写 Apple 文件、缓存布局或 CodeDirectory，也没有验证完整 Apple CMS 信任链。

## 当前候选：两份原版文件的最早执行探针

候选源码 `f916ebb123801e2f5956407ccc0fcc51a6aade81`，构建与既有回归见 [CI 43155794427](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43155794427)。本地/远端主机检查、macOS 内核编译、EFI 和 16 份原有运行报告均通过；新增原版 Apple 执行检查见下文。QEMU 运行检查时尚未更新手机；后续实际文件更新见文末。

新增 `polaris-apple-probe=1` 独立诊断模式：仅 PID 1、有效的原 CodeDirectory 和已固定的两个精确 CDHash 可通过。普通启动仍使用原自制程序策略；探针模式不授予 entitlement 或平台身份，不声称接入 AMFI 或 trust-cache runtime。loader 强制签名存在，重新核对已有缓存 blob，并保留原代码页验证及 HARD/KILL 执行约束。它用于定位最早原版代码执行依赖，不能作为 launchd 服务运行所需的完整安全兼容层。

RAM fixture 采用当前 N4KROOT v3，保留原版 `/sbin/launchd`、`/usr/lib/dyld`，原文件字节完全不变；包大小 1,511,424 字节。先观察 dyld 的真实 EL0 指令字节和 startup 检查点，再观察 `mach_init` 返回、页大小全局量，最后逐步接近依赖库准备。拒绝对照包括关闭实验模式和只在隔离输入副本翻转一个 dyld 代码字节。

本地 QEMU 6.2 观测器复用固定上游 GDB observer，只适配可用的 CPU max、Ubuntu AAVMF 路径与 SCTLR 寄存器名称。先用旧成功内核和自制程序实测 EL0 指令跟踪及检查点；其后又对新内核执行了下面的原版 Apple 输入检查。


## 已通过：原版 dyld 的真实 QEMU 执行

[运行证据](apple-userspace-runtime.json)记录同一 `f916ebb` 源码内核上的 7 组检查：原始内核的 startup、`mach_init` 返回、`dyld4::prepare` 入口、关闭探针、损坏 dyld 页，以及最终打包内核的 `mach_init` 返回和损坏页对照。全部通过；原 Apple 文件、CodeDirectory 和原 trust-cache 输入均未改写。QEMU 使用 CPU max，1 CPU / 受控 1 GiB 内核窗口；这不是 SDM845 外设或真机执行资格。

- 真实 EL0 入口与原 dyld 字节一致，32 个不同执行 PC 的指令字节逐一匹配原文件，startup 检查点到达。
- 原版 `mach_init` 在 EL0 正常返回，实际 x0=0；原 dyld 自身全局量为 page_size=4096、page_mask=4095、page_shift=12、kernel_page_shift=12。随后真实进入 `dyld4::prepare`。
- 正向检查中原代码页 bad_hash/no_hash 均为 0。关闭独立模式时，原版文件被自制程序策略拒绝，没有进入原 dyld。
- 隔离输入副本在 dyld 文件偏移 `0x372b8` 翻转一位，原签名保持不变；实际 bad_hash=1，`mach_init` 没有返回，panic 参数为 CODESIGNING / INVALID_PAGE（namespace=3、code=2）。最终打包内核重复通过该拒绝对照。
- `_amfi=0`，授权是本项目显式开启的两份精确 CodeDirectory 诊断；没有授予 entitlement、加载 AMFI/Apple trust-cache runtime 或证明完整 Apple 信任链。

第一次观察已看到原版代码，但 QEMU 6.2 在 EL0 对内核虚拟地址的读取返回 E14，主机评分器因此退出；原始记录保留。后续观测复用上游的真实 TTBR1 4K 页表遍历，只读物理内存中的计数器，保存每次转换与原虚拟读取错误，并恢复调试器模式。没有写入来宾代码、寄存器或返回值；修正观测后，正向计数与全套检查通过。

原版 launchd 文件已由内核加载，但其主函数没有作为本轮通过目标；共享缓存尚未实际装载，libSystem/TLS、基本服务、系统界面均未完成。本轮把 G1/G3 中“原版加载器的早期实际执行”推进到模拟通过，不能记为 iPadOS 启动成功。

## 已准备的真机文件与回退入口

本地候选目录 `artifacts/apple-dyld-phone-ready-f916ebb/` 保存 FAT 回读通过的 128 MiB ESP，SHA-256 `69918767d14dcaa31279735154dc6878a760c2d609d2a2daec4a34065eb4d906`。最终打包内核 SHA-256 `8d8e0a277253f2ae371adc475dd58246d98ad14bd0312114e23ffd7c2f8b0ac1`，已在 QEMU 原版 dyld 正向/损坏页检查中实际使用；QEMU 仅更换对应的虚拟平台 ADT。

菜单默认 `polaris: ORIGINAL DYLD PROBE`，等待 10 秒；第二项 `polaris: verified self-tests abc75cf` 保留旧成功内核、其 ADT、HMAC 记录与自制 RAM 根的原字节。共享 EFI 二进制也与旧成功版本相同。Apple 入口启用原生 USB 诊断，关闭本轮不需要的原生 UFS 探针；回退项保留原测试参数。回退菜单项的真机选择仍待验证。

用户已准备固件 Mass Storage 并共享 USB；现在已核对旧 ESP 并完成文件更新、同步和完整回读。设备写入已停止，尚未重启或验证真机 Apple 执行。


## 文件已更新，等待用户手动重启

[部署记录](apple-userland-deploy.json)：2026-11-12 经固件 Mass Storage 更新 EFI 文件。USB 身份、已知容量、主/备 GPT 和目标 userdata 分区均核对通过；原 128 MiB ESP 与上次保存的 `5e10f59584d2e7c01cb3f2bd50140d15636d97ad3fc630ac2e11a69a6e5545be` 完全一致后才开始更新。实际卷布局中的 12 个文件均先读回核对，物理写入约 39.3 MiB；同步、清理块缓存后的 O_DIRECT 整卷回读通过。

实际部署 ESP SHA-256 为 `90bce8dfa4b7c93bd43099db7419cb17850a389f900f9a62fa1cd293f963fb6d`。它保留旧卷几何与布局，因而与前述独立制作候选 ESP 的整卷哈希不同；所有已部署文件哈希均与候选清单一致。boot 和其他分区未写入，旧 `abc75cf` 回退内核/ADT/RAM 根及原启动参数保留。

更新完成时停在固件 Mass Storage，未由代理重启。用户后续普通重启时，默认等待 10 秒启动 `polaris: ORIGINAL DYLD PROBE`；`polaris: verified self-tests abc75cf` 为回退项。更新记录本身不代表原版程序运行通过。用户随后回报的真机依赖查找错误见下文；回退项手机菜单选择和完整 iPadOS 启动仍未验证。


## 真机首次启动回报：DYLD/1 缺少 libSystem

用户普通重启后回报 `namespace 6 / code 0x1`，并说明找不到名为 libSystem 的 dylib。官方 XNU 定义 6 为 DYLD，目标 dyld 定义 1 为 `dylib_missing`。服务器未发现可导出的原生 CDC，未获取手机 USB 日志或独立核对真机 PC/构建身份；真机证据来源是用户抄录的错误画面，见 [诊断记录](apple-dyld-missing-libsystem.json)。该回报支持原版 dyld 已执行到依赖库解析，不代表 launchd 主函数或服务已经运行。

对同一最终打包内核与同一 RAM 根继续运行 QEMU，不在 prepare 入口停下，实测也到达 DYLD/1。保留的内核退出说明为 `Library not loaded: /usr/lib/libSystem.B.dylib`，`Referenced from: ... /sbin/launchd`，理由为该路径不存在且没有 dyld cache。捕获 152 次实际 syscall 事件，最后的 abort-with-payload 参数含 namespace=6、code=1；代码页 bad_hash/no_hash 均为 0。它是同一最小输入缺少运行库的模型复现，不替代真机完整日志。

原版 launchd 的 LC_LOAD_DYLIB 明确依赖 `/usr/lib/libSystem.B.dylib`，此外还依赖 ObjC、Sandbox 与 Swift 库。只读检查同一原 OS 卷确认 libSystem、libobjc、libswiftCore 没有独立文件；原共享缓存 image catalog 中可定位 libSystem。当前 1.51 MB RAM fixture 仅含原版 launchd/dyld，未提供共享缓存，因此需要推进存储与动态链接阶段。

下一轮先在服务器验证：复用已有只读卷/缓存加载工具，确定与当前源内核匹配的文件读取和 mmap 路径；接入同版本已校验的 53 个 cache/subcache，并验证已有的原 16K 签名块支持。当前两份精确 CDHash 的诊断授权也需按已核对的原 System trust-cache 成员扩展并验证，不能直接视为整个缓存已获准装载。随后再依据真实缺接口推进 libSystem、ObjC/Swift 和 launchd 主函数。当前 16 条目/32 MiB RAM 根无法容纳约 3.3 GB 缓存，完整缓存接入需要可分页的文件后端；本轮未重编内核或再次写入手机。


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


### 原版 APFS 实际 VFS 注册及缺组件对照

[真实注册证据](apple-apfs-registration.json)：在同一个 12 组件候选上，观察原版 APFS kmod_start 实际执行，跟随源内核 vfs_fsadd 到原调用栈返回，返回值为 0，返回 PC 位于未改写的原 APFS 可执行区段。输出表含 apfs，操作表指针和捕获的 144 字节与原版表完全一致。省略 APFS 的候选保留相同 native source、平台参数和已初始化原 crypto；真实加载 API 返回非零且未返回注册表，拒绝对照通过。旧 native_apfs_registered 诊断扫描仍为 0，后续以实际调用、返回表和挂载验收，不能用该旧计数器替代结果。

原 APFS 模块启动与 VFS 注册现在为模拟通过，磁盘读取、实际挂载与完整共享缓存仍未通过。NativeAES 和 NativeVirtioBlock 的实际 CI 对象已编译，分别 181 和 190 槽；主机符号比较脚本曾在平台工具的解码结果上失败，保留的原对象改用 LLVM 明确的 no-strip 模式后，所有方法签名比对通过。该修正保持全表检查，新内核仍需重新链接与运行。构建依赖已区分：仅存储辅助脚本/专用配置的变化由存储工作流构建，公共内核、驱动、测试和板配置仍触发完整 RAM 根回归。


## G2/G3：只读 APFS 文件分页已模拟通过（caf4c4c）

[存储 CI 43181491189](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43181491189)已通过，实际链接固定上游 NativeAES / NativeVirtioBlock；编译对象的全部 181 / 190 个方法槽与已测原接口逐项一致。原 RAM 根的[完整回归 43178890800](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43178890800)另行通过。本轮没有更新或访问手机，真机仍保留已验收的 57a7d7f 小缓存探针。

[运行证据](apple-storage-runtime.json)记录了真实 QEMU 4K 来宾中的两卷只读挂载、文件读取和小缓存分页。OS 卷 UUID 为 `04769D26-2CA0-4825-B443-E2E5F88300F2`，SystemCryptex 为 `8846FE78-2910-4297-AF97-D59BB6F8FC16`；同一 IORegistry 条目还核对卷名与实际 BSD 名，本次分别为 disk3s1 / disk2s1。设备编号不能作为跨启动的固定身份。前两轮测试把卷角色反置，造成目标路径不存在；失败记录保留，已更正配置和卷身份验收。

正确卷上打开 launchd 最初返回 EIO。主机核对原 launchd/dyld 均为 type 4 的 zlib 压缩文件；保持同一原始源内核、同一自制程序和只读输入，在原 12 组件组合上加入同版本 AppleFSCompressionTypeZlib 后通过，共 13 个原组件的可执行区段保持原字节。原 launchd/dyld 分别读取并比较 256 字节文件前缀，SEEK_END 核对大小 561856 / 937056；没有在来宾中计算两文件的完整哈希。

自制 PID 1 从原 SystemCryptex 的真实 APFS 文件打开 49,152 字节 subcache，执行签名登记、32 KiB RX 私有映射、8 页读取、释放和关闭。实际观察两个完整 16K 签名块、八次 4K 页校验、一个就绪哈希缓存；bad_hash/no_hash 为 0，327 次底层读取零失败。对只读 OS 文件请求写打开返回 EROFS，未执行文件写入。NativeAES 已注册、接口/KAT 通过、完成 3 次请求；unsupported_key=1 是 KAT 的预期拒绝项，本轮镜像未加密，不代表加密卷已验证。

同一最终内核关闭缓存诊断授权后，两卷挂载与原文件读取仍正常，缓存签名登记实际返回 EPERM；完整块、页校验和就绪缓存计数均为 0，进程继续心跳。缺少解压组件的 EIO、此前缺传输的 ENOENT 和缺 APFS 的真实加载拒绝记录均保留。自制探针源码与生成器已保存；公开生成器重建的程序和 RAM 根与本次正向运行逐字节一致，含 Apple 前缀字节的生成头只留在私有输出。

这一项把服务器路径从“组件注册”推进到“真实文件分页”。完整 53 文件共享缓存映射、原缓存指令执行、libSystem/TLS、原版 launchd 主函数和 iPadOS 界面仍未通过；真机通用 UFS 块设备/文件分页需要独立接入，不能用 QEMU VirtIO 结果替代。

接下来先在服务器复用这套只读两卷后端，给原版 dyld 提供完整缓存路径，观察实际映射及首个缺失接口；保留签名拒绝和自制程序基线。到需要验证原生 UFS 后端时再安排 Mass Storage 更新，当前无需操作手机。


## G3：原版 dyld 完整共享缓存已模拟装载（11c5ae3）

[存储构建 43189781602](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43189781602)与[完整回归 43189781663](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43189781663)均通过；主机检查共 92 项。新增启动器入口要求显式模式、PID 1、精确启动器 CDHash、有效原生 4K 签名及无平台/entitlement 权限，旧原版程序与缓存成员授权仍分别核对。关闭启动器模式的同内核对照实际拒绝启动器，原版 launchd/dyld 未进入。手机未访问或更新，当前真机小缓存基线仍为 57a7d7f。

[完整运行证据](apple-full-cache-runtime.json)：自制 PID 1 实际挂载两卷、chroot 并 exec 原版 launchd，内核加载原版 dyld。真实原版缓存加载器到达并返回其原调用者，x0=1，loadAddress 非零且等于原缓存首址加实际 slide，errorMessage=0；实际使用系统共享映射，原选项 forcePrivate=false。原 dyld 32 个实际 EL0 PC 的指令字节与原文件匹配，其自身页全局量为 4096 / 4095 / 12 / 12。

全部 53 个原缓存文件完成签名登记，201,083 个原始 16K 代码块认证通过，发布 53 个哈希缓存；观察到 254 次原生 4K 页校验，bad_hash/no_hash 均为 0。49,254 次底层读取零失败。原缓存映射总量为 3,294,543,872 字节；53 个文件含签名等内容总量约 3.3 GB。实际共享映射调用包含 53 个文件与一个匿名配置记录，共 62 个映射记录。这是服务器完整缓存映射通过，不是把全部文件复制进小 RAM 根。

保留的失败说明了所需布局：缓存挂在 OS 的 /dev 下时，全部签名预检已完成，但真实共享映射函数返回 EPERM，返回栈匹配。其[原 XNU 检查](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/vm/vm_unix.c#L2400)要求相应缓存位于进程根卷或认可的 Preboot 卷。修正版在现有 /private/preboot 上挂 RAM devfs，只在 RAM 中创建 Cryptexes，再把只读 SystemCryptex 挂到该目录；实际共享映射随后成功。没有关闭该检查或写入原 APFS 镜像。DYLD_SHARED_REGION=private 的对照请求未选择私有映射，失败结果保留。

原启动认证链仍是单独的未完成项。未配置诊断启动标志时，实际先在 LIBIGNITION/35 退出；仅 boot-arg 设置 dyld_flags=8 时，内核为 8、commpage 仍为 0，也未通过。自制 PID 1 在挂载与 chroot 成功后调用已有 kern.dyld_flags 接口，将实际内核和 commpage 同时发布为 8，才进入本轮缓存诊断。该模式不构成 Ignition 或 Apple 启动认证链通过，也不授予 entitlement；AMFI 表仍为 0。一次 EL0 后的内核间接指针读取失败已保留完整部分记录；观测器扩展既有只读 TTBR1 物理读取后完成取证，没有修改来宾代码、寄存器或返回值。

公开生成器在 Linux 重建的启动器与 RAM 根，与最终通过运行逐字节一致，完整哈希记录于证据文件。公开内容只有自制源码、配置、哈希与结构化结果；Apple 文件、镜像、含前缀字节的生成头和原始内存记录留在私有目录。

下一步继续原版 dyld 的库解析、libSystem 初始化与 TLS，再验证真实 launchd 主函数。原缓存内代码执行、libSystem/TLS 和 launchd 主函数尚未作为本轮通过目标；真机通用 UFS 文件分页也仍需独立接入，不能用服务器 VirtIO 结果替代。当前无需准备 Mass Storage。
