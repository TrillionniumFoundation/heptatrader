# 本地开发

Status: current
Applies to: developers changing active runtime, docs or research
Verification: `./scripts/dev_core.sh`
Authority: developer entrypoint

默认入口：

```bash
./scripts/dev_core.sh
```

该入口必须至少执行 repository/document truth、documentation control plane、schema catalog、module discipline/registry、research static verification、Release core build、core CTest 和 Python contract tests。

模块开发应优先运行 changed-module target 和 contract tests，再运行完整 core。构建目录必须位于 repository `build/` 或受控 runner temp，禁止写入源码树或任意系统路径。

本地成功不是 merge 或 qualification 证据；exact candidate 必须通过对应 CI lane。
