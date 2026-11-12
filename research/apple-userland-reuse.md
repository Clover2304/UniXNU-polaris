# Apple 用户态启动：现有适配复用盘点

更新时间：2026-11-12。目标仍为 iPadOS 17.7.11 / 21H461 / iPad7,1，当前已验证内核源码为 `abc75cfb6a7f3a209af4865f575a9b70387c916e`。本文记录复用盘点与静态输入检查，不表示 Apple 用户态已执行或通过真机验证。

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

共享缓存所在的独立 SystemOS DMG 尚未下载或审计。下一项是复用上游 dyld fixture/probe，核对原签名授权、entitlement、加载器和最早 EL0 入口依赖，准备独立 Apple 执行配置；当前成功镜像保留。

## 已验证：自制用户态可复用内核

[复用记录](userspace-kernel-reuse.json)：在 Linux 编译并签名另一份自制 PID 1/子程序，沿用 `abc75cf` 的同一原始内核运行 QEMU，进程、I/O、`/tmp`、1,000 子进程压力和常驻检查通过。随后制作离线文件树预览，只替换 RAM 根 `root.img` 与配置中的两个 CDHash，并移除不适用于该程序的 `polaris-ufs-node=1`；其余文件均按原 manifest 校验。已经打包的手机内核逐字节保持 SHA-256 `4b3126eb76b27b0113696951b47a39174f6eef6d7b5340e012674982f64bb4bc`，EFI 也未变化，无需 macOS 或重新编译内核。此预览没有安装、没有重启、没有新增真机验收。

适用边界：自制程序和 RAM 文件变更可在 Linux 重做程序包与配置；实际应用仍需一次 Mass Storage 文件更新和普通重启。原生 CDC 当前只有固定诊断命令，没有程序上传、交互 shell 或热执行能力。内核驱动、系统调用、加载器和安全策略改变仍需重编内核。当前策略要求自制程序具有 `CS_ADHOC|CS_HARD|CS_KILL`，按 PID 1/直接子进程及精确 CDHash 授权，并拒绝平台标记和 entitlement；实际 Apple 样本的 CodeDirectory flags 是 `0x2`，不能沿用该自测策略直接执行。RAM 树另有限制：16 个条目、32 MiB 镜像，不足以放入完整系统和共享缓存。

本轮手机保持运行，无需提前进入 Mass Storage。下次确有已验收的候选文件时再请求更新；完成整卷回读与同步后，仍停下等待用户手动重启。
