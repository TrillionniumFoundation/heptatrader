# Canonical Config Source + Profile Lock (ISSUE-P0-001)

## 新增内容

- 单一配置入口：`scripts/resolve_hepta_config.py`
- 统一环境变量：
  - `HEPTA_CONFIG_PATH`（主入口）
  - `HEPTA_PROFILE`（`sim|paper|live`）
  - `HEPTA_CONFIG_SHA256`（启动前计算）
- 兼容旧变量：`HEPTA_TRADER_CONFIG_PATH`（仅兼容，建议迁移）

## 启动指纹

以下脚本启动时会打印：

`CONFIG_FINGERPRINT config_path=<...> profile=<...> sha256=<...>`

- `scripts/run_hepta_with_logs.ps1`
- `scripts/release_check.ps1`
- `scripts/ci_gate.ps1`

## 冲突检测（Fail-Fast）

当出现以下情况时立即失败并输出明确错误：

1. `--config` / `HEPTA_CONFIG_PATH` / `HEPTA_TRADER_CONFIG_PATH` 多源同时存在且路径不一致。
2. 指定 `--profile`/`HEPTA_PROFILE` 与配置推断 profile 不一致。
3. `profile=sim` 但配置中 `IBServer.Mode=IB`。
4. `profile=paper|live` 时配置为 `*.example` 模板文件（禁止生产 fallback 到模板）。

## Profile 识别规则

优先级：

1. `--profile`
2. `HEPTA_PROFILE`
3. 配置文件推断：
   - `<Runtime Profile="..."/>` 或 `<Runtime><Profile>...</Profile></Runtime>`
   - 否则：`IBServer.Mode=IB` 且账户 `DU*` => `paper`
   - 否则：`IBServer.Mode=IB` => `live`
   - 其他 => `sim`

## 迁移说明

- 旧：依赖脚本内候选路径自动挑选配置。
- 新：所有运行/检查脚本统一先调用 `resolve_hepta_config.py`。
- 建议：只保留 `HEPTA_CONFIG_PATH`，删除 `HEPTA_TRADER_CONFIG_PATH`。

## 验证

运行：

```powershell
powershell -ExecutionPolicy Bypass -File validation/test_config_resolver.ps1
```

通过后会看到 baseline、配置源冲突 fail-fast、以及 production profile 禁止 .example fallback 等 PASS。
