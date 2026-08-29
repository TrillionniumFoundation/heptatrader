# Hepta Security Check

运行命令：

```powershell
pwsh -File .\scripts\hepta_secrets_check.ps1
```

功能：
1. 扫描项目中可能的明文凭据泄露（UserID/PassWord/AuthCode 等）
2. 检查 Hepta 所需环境变量是否齐全
3. 检查本机运行配置 `HeptaTrade/HeptaTraderConfig.xml` 是否存在

返回码：
- `0`：未发现明显泄露
- `2`：发现疑似泄露项（请修复后再提交）
