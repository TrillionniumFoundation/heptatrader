# Build Hygiene Checklist

## 目标
减少仓库污染、避免误发布本地二进制、提升可复现性。

## 本地构建后应检查
1. `git status --short` 中不应出现大量 `*.obj/*.pdb/*.exe/*.dll`。
2. 配置文件不应包含真实凭据（`UserID/PassWord/AuthCode`）。
3. `runtime-logs` 保留最近必要记录，过旧日志归档或清理。

## 建议清理范围（按需）
```powershell
# 在仓库根目录执行
Get-ChildItem -Recurse -Directory -Include Debug,Release,.vs | Remove-Item -Recurse -Force
Get-ChildItem -Recurse -File -Include *.obj,*.pdb,*.ilk,*.ipdb,*.iobj,*.tlog | Remove-Item -Force
```

> 注意：清理前先停止 Hepta 相关进程，并确认不需要调试符号。

## 发布前最小门禁
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\release_check.ps1
```
- 必须 `OVERALL=PASS`
- 若失败，优先处理 `RISK_CONFIG / IB_HEALTHCHECK / IB_REGRESSION_ROUND`

## IB 构建收敛（防命令污染）
- IB 相关属性统一收敛到 `build/Hepta.IB.props`，避免手工在命令行拼接多个 `/p:` 导致漂移。
- `HeptaTrade/HeptaTrader.vcxproj` 已启用 `TreatAsLocalProperty`（`HeptaBuildProfile/IBApiRoot/IBApiBin/HeptaEnableIbApi`），减少全局属性串扰。
- `HeptaBuildProfile=ib-release` 时自动启用 IB；`IBApiRoot` 自动发现顺序：
  1. `/p:IBApiRoot=...`
  2. 环境变量 `IBAPI_ROOT`
  3. 仓库内 `Interface/IBApi/source/CppClient`
- `libbid.lib` 为**可选**依赖：若存在则自动链接；不存在时提示并退回 source-only IB 构建，不中断编译。

### 唯一推荐构建命令（Windows x64 Release + IB）
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_release_ib.ps1
```
