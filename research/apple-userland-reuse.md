# Apple 用户态启动：现有适配复用盘点

更新时间：2026-11-11。目标仍为 iPadOS 17.7.11 / 21H461 / iPad7,1，当前已验证内核源码为 `1d38b03539ec26b146ceda717c6cf831ab572cec`。本文是源码与计划盘点，不表示 Apple 用户态已通过真机验证。

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

新增 `scripts/check-apple-signature-gate.py`，复用已有检查思路，在 Linux/macOS 编译实际 `ubc_subr.c` 中的 CodeDirectory 页粒度判断。当前从已保存的 Darwin 23 补丁源文件提取并通过 8 个组合；[结果](apple-signature-gate.json)记录完整输入与提取代码哈希。4K 内核配默认 integrity-cache=0 时，4K 签名页通过、16K 签名页被拒；已有 cache=1 路径允许后者进入下一步。这只测试一道实际检查，不证明完整签名完整性、entitlement、dyld 或原版程序执行。

下一次内核 CI 会对本次实际组装的 XNU 源码执行同样检查，并随查询镜像携带报告。目标 Apple 二进制的实际签名页粒度尚未读取；在取得样本前，不将上述条件性结果当作该二进制的实际失败原因，也不直接改变当前成功内核配置。
