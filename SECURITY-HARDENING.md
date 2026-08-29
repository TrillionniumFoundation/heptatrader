# Hepta 安全加固（Step 1：Secrets 外置）

本步骤目标：**彻底避免账号/密码/AuthCode 明文进入仓库**。

## 1) 原则
- 仓库中只保留 `*Config.xml.example` 模板。
- 实际凭据从本机环境变量注入（或启动前生成本地私有配置）。
- 真实配置文件不提交版本库。

## 2) 建议环境变量
- `HEPTA_MD_FRONT`
- `HEPTA_TD_FRONT`
- `HEPTA_BROKER_ID`
- `HEPTA_USER_ID`
- `HEPTA_PASSWORD`
- `HEPTA_APP_ID`
- `HEPTA_AUTH_CODE`
- `HEPTA_PRODUCT_INFO`

## 3) 本机生成配置（推荐）
使用 `scripts/render_hepta_config.ps1` 从 `HeptaTraderConfig.xml.example` 生成本机私有 `HeptaTraderConfig.xml`。
脚本会强制校验必填环境变量、拒绝写回 `.example`，并采用临时文件原子替换，避免半写入配置。

示例：
```powershell
pwsh -File scripts/render_hepta_config.ps1 \
  -Template ".\HeptaTrade\HeptaTraderConfig.xml.example" \
  -Output ".\HeptaTrade\HeptaTraderConfig.xml"
```

## 4) 发布前检查
- 确认仓库内 `HeptaTraderConfig.xml` 不含真实凭据。
- 执行关键词扫描：`UserID=`, `PassWord=`, `AuthCode=`。
- 运行 `scripts/hepta_secrets_check.ps1 -StrictEnv`，在发布/CI 场景将缺失环境变量视为失败。
- CI 中加入 secrets 扫描（建议）。

## 5) 你当前状态（已完成）
- 明文凭据已清空。
- `.gitignore` 已增强，避免配置与构建垃圾误提交。
