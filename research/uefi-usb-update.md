# edk2 默认启动与 USB 文件更新

## 已验证的存储路径

固定固件为 `luuu20309/edk2-msm` 的 `a6fc3fcbe128dd734cf8ad0cd9915a780fd2fb31`，Android boot 镜像 SHA256 为 `534ebe6887b86d6c1f4cc0b6bc92651649b8aebeecd6bc6739e3d19332a5c7fb`。它与前面临时启动成功的镜像相同。

Polaris 配置启用了 SimpleInit 和 Linux Simple Mass Storage。入口为启动时音量键进入 SimpleInit，再选 Mass Storage 并确认；也可在 UEFI Boot Menu 找到 USB Attached SCSI (UAS) Storage。

该入口启动内置的小型 Linux，通过 `05f9:1234` 导出 UFS。`crng init done` 是正常 Linux 日志，不代表失败。实机导出了 6 个逻辑单元，接口声明 BOT 和 UAS；本份二进制没有枚举 fastboot 接口，不能把当前上游源码新增的 fastboot 命令当作这份二进制已有功能。退出这一小型 Linux 需要重新启动，无法直接返回原 edk2 菜单。

服务器已通过 USB/IP 识别 59,064,188,928 字节的主单元和 57,453,568,000 字节的 userdata。主、备 GPT 的 CRC、分区边界和 USB 设备身份核对通过。userdata 开头 128 MiB 的 FAT ESP 与成功查询镜像完全一致，SHA256 为 `fe1143b3168053c288fb659db3d7eab9310f04f233307eaaffb0b9163e84f63f`。

文件往返实测在该 ESP 创建 `EFI/UniXNU/USBPROBE.TXT`，写入块合计 20 KiB，通过对齐的 O_DIRECT 回读验证，再逐字节恢复原 ESP。未挂载或格式化手机磁盘，也未改 GPT、boot 或 recovery。公开摘要见 [结果](uefi-usb-update-result.json)。这是固件附带 Linux 的存储能力，不计为 XNU 原生 UFS 介质读取通过。

## 主机工具

`scripts/check-uefi-usb-storage.py` 默认只读核对；加 `--exercise-write` 才执行上述小文件往返并恢复。目标必须是 USB `05f9:1234` 下匹配已测容量及 GPT 的 userdata 分区；拒绝已挂载、被其他块映射占用、镜像不一致或身份变化的设备。

```sh
python3.11 scripts/check-uefi-usb-storage.py \
  --device /dev/disk/by-partlabel/userdata \
  --expected-image /path/to/verified-esp.img \
  --out /path/to/new-read-only-record
```

`scripts/update-uefi-usb-esp.py` 接收 `prepare-usb-test.py` 生成、已通过运行验收的 ready 目录。CI 和本地验收分别记录来源；镜像必须通过原有基线及拒绝控制，不以构建成功代替运行检查。它先在本地 ESP 中重建 EFI 文件树，检查源文件清单与哈希、FAT、一一提取的文件及启动卷几何，再计算改变的块。默认只预览，`--apply` 才更新 userdata 开头 128 MiB 内对应的块；改动量最多 96 MiB。完成后完整直接回读，失败时尝试恢复改变的块，原 ESP 副本保留在记录目录。

```sh
python3.11 scripts/update-uefi-usb-esp.py \
  --ready /path/to/qualified-ready \
  --expected-image /path/to/current-verified-esp.img \
  --out /path/to/new-update-record
```

实际执行加 `--apply`。后续更新的 expected image 使用上一次成功更新记录中的 `updated-esp.img`。可选 `--title` 只改变唯一启动项标题，工具通过 TOML 结构比较确保启动参数等其他设置完全一致。

## 固定启动入口与恢复

用户已明确选择把 edk2 安装到 boot，作为普通重启的默认入口。安装前通过 USB 完整回读当前 64 MiB boot；与早先独立保存的备份逐字节一致，SHA256 为 `9044bba9778f5291d4cc58842e401fa6eeb362b9b2e0739b41653b8272d60681`。备份只保留本地。

候选分区镜像由上述已验证固件原始字节加零填充至 64 MiB，SHA256 为 `1578f00d31f66e894eaca3593d8ad1fbd2a2ad0a0c8de2a33700cbf45dee3b92`。`scripts/install-edk2-default.py` 固定核对这两个镜像及固件哈希，并复用 USB、GPT、分区边界、独占打开与直接回读检查。已执行安装并通过整个 boot 分区直接回读；recovery 未修改。用户随后只用电源键普通重启，确认依次经过 Renegade、USB 更新后的 UniXNU 菜单和 XNU。服务器重新接入原生 CDC，验证同一 `9feeaca` 源码身份、完整基线自检、变化心跳和两项 UFS 描述符查询，USB 错误/溢出及日志丢失均为零。

该工具默认只读，安装加 `--apply`；恢复加 `--restore --apply`。恢复前要求当前 boot 与已安装候选逐字节一致。如需从 fastboot 恢复，可使用已核对的原始完整备份：

```sh
fastboot flash boot /path/to/original-boot.img
```

安装脚本以这台开发板和本次固定镜像为范围；更换主板、容量或固件时需重新核对输入，不能直接沿用本次身份与哈希。

## 当前限制与后续

- 文件更新完成后仍需要重新启动内核；XNU 已退出 UEFI Boot Services，不能像应用一样退出回菜单。
- 当前 XNU CDC 仍无重启命令；当前存储组件也没有远程重启接口。默认 edk2 入口可以去掉反复进入 fastboot 的步骤，软件重启与自动菜单选择另行实现。
- 原生 XNU UFS 的 PRDT、SCSI 容量及扇区读取资格沿用 [UFS 计划](ufs-readonly-plan.md)，本轮主机经 Linux 导出的读写不替代这些验证。

来源：[Polaris 构建配置](https://github.com/luuu20309/edk2-msm/blob/a6fc3fcbe128dd734cf8ad0cd9915a780fd2fb31/Platform/Xiaomi/sdm845/polaris.dsc)、[固件打包](https://github.com/luuu20309/edk2-msm/blob/a6fc3fcbe128dd734cf8ad0cd9915a780fd2fb31/Platform/Qualcomm/sdm845/sdm845.fdf)、[SimpleInit 入口](https://github.com/BigfootACA/simple-init/blob/29e062805776db5ab7a61d07ef040d43aed71161/src/gui/interface/apps/uefi_mass.c)、[固定组件描述](https://github.com/edk2-porting/edk2-msm-binary/blob/d097b8a8bf3641e5e0c8b36ef1ac9d7cc1a0bd94/Applications/LinuxSimpleMassStorage/LinuxSimpleMassStorage.inf)。


## 首轮实际文件更新与启动结果

通过上述文件更新工具重建 EFI 文件树，并把唯一启动项标题改为 `polaris: USB UPDATE VERIFIED`。内核、ADT、根镜像、EFI 程序和全部启动参数保持同一已验收基线，文件逐一回读核对。实际写入 19,128,320 字节的改变块，随后完整 128 MiB 直接回读一致；该轮 ESP SHA256 为 `87deb85d398cc846fb72d8486b7803403151dce7a35f95017dbff730126887a2`。这一步更新的是文件内容和 FAT 分配信息，未向手机重新传输整张 ESP 镜像。

普通重启中用户确认新标题生效，原生 CDC 重新接入后取回 5,530 字节运行日志，SHA256 `df3fa4442d8a8aa97b81220a870be573128bae9f507f2b7d1f326afe5d2fcf88`。基线自检、UFS 查询与持续心跳通过。这是默认启动链和 USB 文件更新验收，不代表换用了新的内核源码版本。

主机工具专项 7 项测试通过，覆盖 GPT 主备 CRC/表一致性、重复/缺失/重叠分区、目标分区显式选择、非块设备拒绝、改变块对齐与写入上限、路径穿越/外部符号链接拒绝，以及只改标题时启动参数不变。安装、更新预览和本机读写/回读也已分别实测。原始证据与备份只保留本地，仓库保存必要摘要。

## 当前运行镜像

最新成功源码 `2791907` 已通过同一路径更新并普通重启，固定 FAT32 文件读取、六扇区/主 GPT 校验及基线自检、心跳、USB 通过。当前 expected image 为工作区 `private/ufs-fat-deploy/update/updated-esp.img`，SHA256 `722a52712b8b060d681546ee169470365585245d8a6dd0815eb7b16ec032dd81`；后续更新必须匹配这份实际启动卷。[完整 CI](https://github.com/Clover2304/UniXNU-polaris/actions/runs/43126540937)、82 项主机测试、15 项运行验收与最终打包画面通过，来源见 [结果](ufs-fat-result.json)。固件辅助文件更新与 XNU 原生读取分别取证；本轮有限 FAT32 文件读取不代表 VFS 挂载，当前仍无 XNU 介质写入。
