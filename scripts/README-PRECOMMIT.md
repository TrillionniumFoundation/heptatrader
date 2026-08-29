# Hepta Pre-commit Hook

安装：

```powershell
pwsh -File .\scripts\install_precommit_hook.ps1
```

作用：
- 每次 `git commit` 前自动执行：
  - `scripts/hepta_secrets_check.ps1`
- 若发现疑似明文凭据，直接阻止提交。

注意：
- 项目目录必须已是 Git 仓库（存在 `.git`）。
- 如未初始化：先执行 `git init`。
