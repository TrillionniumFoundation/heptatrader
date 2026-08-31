# Release process

## Release scope

默认公开 release 是 **IB-disabled core package**。它包含 Gateway、Simulator Execution、CLI、MCP adapter、systemd units、policy、文档和可观测性工具；不包含 Broker SDK、Broker credential、CTP overlay、XT/QMT transport 或 LIVE capability。

## Preconditions

1. `main` 上的 CI 全绿。
2. `VERSION` 为合法 semantic version，且 `Interface/include/heptaVersion.h` 与其一致。
3. `./scripts/dev_core.sh` 在干净 Linux 环境通过。
4. `docs/CAPABILITY-MATRIX.md` 与实际构建范围一致。
5. 不存在未处理的 P0/P1 安全回归。
6. 对 IB PAPER 的任何声明都附带同一 commit 的受控资格认证证据；否则维持 Conditional。

## Tag and build

创建 `v<VERSION>` tag 后，`.github/workflows/release.yml` 会：

1. 校验 tag 与 `VERSION` 完全一致；
2. 运行仓库契约和 Python 测试；
3. 重新配置、构建并运行 CTest；
4. 安装到隔离 staging root；
5. 验证所有必需路径、mode、symlink 与 systemd 引用；
6. 生成安装 hash manifest 和 SPDX 2.3 SBOM；
7. 生成 TGZ package 与 `SHA256SUMS`；
8. 对制品生成 GitHub build provenance；
9. 发布 GitHub Release。

release 不复用开发机上的旧 build 或安装目录。

## Verification by consumers

解包前校验 `SHA256SUMS` 和 provenance。解包后在目标 root 再运行 `verify_install_tree.py`。部署者必须自行创建受控 OS identity、credentials、env 文件和 network policy，并执行 `RUNBOOK-STARTUP.md`。

## Rollback

回滚使用上一个已验证 tag 的完整制品，不在目标主机手工替换单个二进制。停止服务、保存 journal/alerts、恢复上一制品和配置快照、重新执行安装树及启动检查。任何 authoritative state 不确定时保持 kill switch engaged。
