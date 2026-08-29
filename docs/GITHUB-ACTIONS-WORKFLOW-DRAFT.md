# GitHub Actions Workflow 草案（可迁移）

> 当前仓库可先本地执行，不要求立即启用 `.github/workflows/`。
> 
> 目标：把本地 `gate-local.ps1` / `scripts/ci_gate.ps1` 迁移为 CI 门禁。

## 建议文件

- `.github/workflows/ci-gate.yml`

## 草案 YAML

```yaml
name: ci-gate

on:
  workflow_dispatch:
  pull_request:
    branches: [ main, master ]
  push:
    branches: [ main, master ]

jobs:
  gate:
    runs-on: windows-latest
    timeout-minutes: 30

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Run local CI gate
        shell: pwsh
        run: |
          ./gate-local.ps1 -NoLaunch -SkipHealthcheck

      - name: Upload gate artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: ci-gate-logs
          path: runtime-logs/ci-gate-*/
```

## 迁移要点

1. **无 IB 实盘环境时**：在 Actions 上默认加 `-NoLaunch -SkipHealthcheck`。
2. **强门禁阶段**：保留 whitelist + regression + release_check（视 runner 能力决定是否开启 healthcheck）。
3. **失败判定**：依赖 `ci_gate.ps1` 退出码（非 0 即 fail）。
4. **日志归档**：保留 `runtime-logs/ci-gate-*`，便于排查。

## 后续可演进

- 增加 matrix（Python 版本、Debug/Release 构建）
- 增加 C++ 编译与单测步骤
- 将 `ci_gate_summary.json` 解析后写入 Job Summary
