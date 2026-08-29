# HeptaTrader 项目结构优化记录

## 本轮已完成

1. **工程引用一致性检查**
- 检查了 `.vcxproj` 中源码/头文件引用是否缺失。
- 结果：未发现缺失引用。

2. **CMake 路径清理（HeptaStrategy）**
- 文件：`HeptaStrategy/CMakeLists.txt`
- 移除不存在目录：`../heptaStrategys/include/`

3. **Visual Studio 工程路径清理（Windows）**
- 文件：
  - `HeptaTrade/HeptaTrader.vcxproj`
  - `HeptaSimulator/HeptaSimulator.vcxproj`
- 已移除过时路径：
  - `../heptaStrategys/include/`
  - `../heptaStrategys/lib/Debug`
  - `../heptaStrategys/lib/Release`
  - `../heptaStrategys/lib/X64/Debug`
  - `../heptaStrategys/lib/X64/Release`

4. **Linux vcxproj 收口清理（本轮新增）**
- 文件：`HeptaTrade/HeptaTrader_Linux.vcxproj`
- 修改项：
  - `RootNamespace` 改为 `HeptaTrader_Linux`
  - Include 目录由历史 `heptaStrategys_Linux/heptaCTPLIB_Linux` 改为：
    - `../HeptaStrategy/`
    - `../Interface/include/`
    - `../Interface/`
  - Debug/Release 链接依赖改为相对路径与当前命名：
    - `../Interface/CTPTradeApiLinux/thostmduserapi_se.so`
    - `../Interface/CTPTradeApiLinux/thosttraderapi_se.so`
    - `libHeptaStrategy.a`
    - `libheptaHeptaDLL_Linux.a`
    - `libTinyXml_Linux.a`
  - 删除硬编码绝对路径依赖（`/root/projects/...`, `/home/tester/...`）

## 仍待处理（建议）

1. `HeptaTrade/CMakeLists.txt` 中仍有一行注释包含旧绝对路径示例（不影响构建）。
2. `README.md` 中目录说明仍有历史名称 `heptaStrategys`（文档层面，建议后续手工整理）。

## 备注
当前机器缺少 `cmake/msbuild/cl`，尚未做编译验证。建议安装工具链后进行一次 Windows + Linux 的完整构建回归。
