# Windows 依赖一键化（Sprint #1）

## 目标
通过 `scripts/bootstrap_win_libs.ps1` 一次完成依赖库构建与投放：

1. 编译 `D:\quant\HeptaDLL-main\heptaHeptaDLL.sln`（`Release|x64`）
2. 编译 `D:\quant\HeptaDLL-main\tinyxml\tinyxml_lib.vcxproj`（`Release|x64`，显式支持 `WindowsTargetPlatformVersion=10.0`）
3. 复制 `heptaHeptaDLL.lib` 与 `tinyxml.lib` 到 `D:\quant\HeptaTrader-master\x64\Release`
4. 输出两个产物的 `sha256` 校验和与执行 summary

## 使用方法
在 `HeptaTrader-master` 根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_win_libs.ps1
```

可选参数：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_win_libs.ps1 `
  -HeptaDllRoot "D:\quant\HeptaDLL-main" `
  -TraderRoot "D:\quant\HeptaTrader-master" `
  -Configuration Release `
  -Platform x64 `
  -WindowsTargetPlatformVersion 10.0
```

## 设计约束
- **幂等**：可重复执行，目标目录同名 `.lib` 会被覆盖为最新构建产物
- **失败即非 0 退出**：任一步骤失败会立即中止并返回 `exit 1`
- **路径兼容**：对 `heptaHeptaDLL.lib` / `tinyxml.lib` 的常见输出目录做了候选查找
- **可审计**：每次执行都会打印产物大小与 `SHA256`

## 验收标准
运行后终端应出现：
- `STATUS: PASS`
- `Bootstrap Summary` 表格（包含 `Name / SizeBytes / SHA256`）
- 输出目录 `x64\Release` 下存在：
  - `heptaHeptaDLL.lib`
  - `tinyxml.lib`
