# Release process

## Release scope

默认公开 release 是 **IB-disabled core package**。它包含 Gateway、Simulator Execution、CLI、MCP adapter、systemd units、policy、文档和可观测性工具；不包含 Broker SDK、Broker credential、CTP overlay、XT/QMT transport 或 LIVE capability。

## Preconditions

1. 候选提交必须是 `main` 的精确当前 head，而不只是可从 `main` 到达的历史提交。
2. 同一提交的 `CI` push run 必须全部成功，并至少包含：`repository-contracts`、GCC/Clang 的 Debug/Release 四个 core job、`asan-ubsan` 和 `package`。
3. `VERSION` 为合法 semantic version，且 `Interface/include/heptaVersion.h` 与其一致。
4. `docs/CAPABILITY-MATRIX.md` 与实际构建范围一致，不存在未处理的 P0/P1 安全回归。
5. GitHub 环境 `heptatrader-release` 必须由平台管理员启用 required reviewers；环境变量 `HEPTA_RELEASE_APPROVED_SHA` 必须被设置为本次明确批准的 40 位候选提交 SHA。
6. 对 IB PAPER 的任何声明都必须附带同一 commit 和制品哈希的受控资格认证证据；否则维持 Conditional。

仓库文件不能替代 GitHub 平台侧的环境保护。若环境未配置、批准 SHA 为空或不匹配，publish job 必须失败。

## Tag and two-phase workflow

在全部前提完成后，为当前 `main` head 创建 `v<VERSION>` tag。`.github/workflows/release.yml` 分为两个权限隔离阶段。

### 1. Build candidate

该阶段只有只读 repository/actions/checks 权限，并执行：

1. 校验 tag 与 `VERSION` 完全一致；
2. 拉取 `origin/main`，要求 tag commit 与其精确相等；
3. 通过 `scripts/verify_release_ci.py` 查询同一提交的完整成功 CI job 集；
4. 运行仓库契约和 Python 测试；
5. 重新配置、构建并运行 Release CTest；
6. 安装到隔离 staging root，验证 root、全部目录、文件 mode、symlink、special file 和 systemd 引用；
7. 生成安装 hash manifest、SPDX 2.3 SBOM、TGZ package 与 `SHA256SUMS`；
8. 仅上传不可变候选 artifact，不拥有 release 发布权限。

### 2. Publish

该阶段依赖 build candidate 成功，并进入受保护的 `heptatrader-release` 环境。它拥有最小化的 contents/attestation 写权限，并执行：

1. 下载与 `GITHUB_SHA` 精确绑定的候选 artifact；
2. 要求 `HEPTA_RELEASE_APPROVED_SHA == GITHUB_SHA`；
3. 重新校验 `SHA256SUMS`；
4. 对制品生成 GitHub build provenance；
5. 最后才创建 GitHub Release。

release 不复用开发机上的旧 build 或安装目录，也不能由任意匹配版本号的历史 tag 直接获得发布权限。

## Verification by consumers

解包前校验 `SHA256SUMS` 和 provenance。解包后在目标 root 再运行 `verify_install_tree.py`。部署者必须自行创建受控 OS identity、credentials、env 文件和 network policy，并执行 `RUNBOOK-STARTUP.md`。

## Rollback

回滚使用上一个已验证 tag 的完整制品，不在目标主机手工替换单个二进制。停止服务、保存 journal/alerts、恢复上一制品和配置快照、重新执行安装树及启动检查。任何 authoritative state 不确定时保持 kill switch engaged。
