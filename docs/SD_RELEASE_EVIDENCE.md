# SD.cpp 最新发行核验记录

核验日期：2026-10-25（UTC）。这是本次开发验证的快照，不是安装器的版本锁定；安装时仍动态请求官方最新发行元数据。

## 官方发行选择

- 元数据：https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/latest
- 当时返回：`master-1005-ea209db`。
- 官方 ZIP：https://github.com/leejet/stable-diffusion.cpp/releases/download/master-1005-ea209db/sd-master-ea209db-bin-Darwin-macOS-26.6.2-arm64.zip
- 官方 SHA256 与实际下载计算结果一致：`d183e8d87a1d1b26f376ff68f4d15f3322648805687cb01c25a97c9ac5acdc50`。
- 官方发行列表：https://api.github.com/repos/leejet/stable-diffusion.cpp/releases?per_page=100
- 列表中 `master-1006-0886f11` 的发布时间是 `2026-10-24T23:46:39Z`，`master-1005-ea209db` 是 `2026-10-24T23:58:04Z`。不能根据编号或列表第一项自行推断官方 latest；安装器遵循 latest 接口，不写死这些 tag。

## 实际包静态检查

本次仅下载、核验摘要、解压到临时目录并读取二进制，不执行 `sd-server`、`sd-cli` 或任何下载脚本。

共 7 个文件：

- `sd-server`
- `sd-cli`
- `libstable-diffusion.dylib`
- `ggml.txt`
- `stable-diffusion.cpp.txt`
- `start-server.bat`
- `start-server.sh`

`sd-server`、`sd-cli` 和动态库的 ARM64 Mach-O 静态最低 macOS 为 **26.0.0**。文件名中的 `26.6.2` 是构建标识，不能取代实际 Mach-O 最低系统要求。

在实际 `sd-server` 二进制中发现 `--model-dir`、`/sdcpp/v1/models/load`、`/sdcpp/v1/models/status` 字符串。对应 tag 的官方源码也包含模型管理路由：https://github.com/leejet/stable-diffusion.cpp/blob/master-1005-ea209db/examples/server/routes_models.cpp 。

这些检查证明发行包格式、架构、最低系统声明和模型管理接口的静态存在，不证明已在用户的 Mac 上启动、加载模型或完成生成。用户设备上的实际运行仍需另行验证。

## 安装行为

稳定安装位置是 `~/AI/apps/sd.cpp`（自定义根目录时相应变化）。完整运行包写入后记录所有文件摘要，复用及升级前检查缺失或修改；不只核对主程序。新旧主包切换失败时恢复旧目录，最多保留一个经完整核验的上一版备份。旧的版本编号目录和未知入口不自动删除。
