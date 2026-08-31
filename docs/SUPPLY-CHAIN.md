# Supply-chain policy

## Principles

1. 构建只消费固定 commit 和显式依赖；运行时不得自动下载 Broker SDK。
2. 默认公开 package 不包含第三方 vendor SDK、credential、账户配置或 market data。
3. 每个 release 生成安装 hash manifest、SPDX SBOM、SHA256SUMS 和可验证 build provenance。
4. 缺失来源、版本、license 或 redistribution authorization 时 fail closed：不打包、不发布、不以 stub 伪装支持。
5. 所有外部 GitHub Actions 必须固定到完整 commit SHA；容器 action 必须固定到 `sha256` digest。本地 action 可以使用仓库相对路径。

## Dependency boundaries

- OpenSSL 通过系统包提供，CI 安装 development headers；发布者负责记录目标系统动态依赖。
- IB API 由受控 runner 从已授权本地路径提供，`IBAPI_ROOT` 不得指向网络下载或可变工作区。
- CTP overlay 使用 content-addressed manifest，但因来源和分发授权未闭合而排除发布。
- XT/QMT SDK 不进入仓库或 package。
- legacy TinyXML/oneTBB-compatible source 在任何源代码分发前必须补齐原始 license 与 provenance。

## Review and merge controls

`CODEOWNERS` 标记 risk、execution、Broker adapter、systemd、workflow 和 CMake 等安全关键路径。GitHub ruleset 必须要求 pull request、owner review、全部 CI required checks、已解决 review thread、禁止 force push 和禁止删除 `main`。仓库内文件不能代替平台侧 ruleset；两者都需要。

外部 action pin、文档路径、systemd/install graph、发布权限边界和 source-size no-growth budget 由 `scripts/check_repo_contracts.py` 在每次 CI 中验证。

## Release authority

release workflow 只响应 `v*` tag，但 tag 本身不授予发布权。候选必须满足：

1. tag commit 等于当前 `main` head；
2. 同一 SHA 的 main push `CI` run 包含完整成功的 GCC/Clang Debug/Release、ASan/UBSan、repository-contracts 和 package job；
3. 只读 build-candidate job 完成 staging、manifest、SBOM、package 与 checksum；
4. 独立 publish job 进入受保护的 `heptatrader-release` 环境；
5. 环境变量 `HEPTA_RELEASE_APPROVED_SHA` 与候选 SHA 精确相等；
6. 下载后的候选重新通过 `SHA256SUMS`，之后才可 attestation 和发布。

制品必须从空 staging root 构建，不能把开发机安装目录复制进 package。安装树验证会拒绝 root、任一目录或文件的 group/world write、symlink 和 special file，防止只读二进制被可写父目录替换。

## Incident response

发现依赖被替换、hash 不匹配、来源不明或 credential 泄漏时：停止发布和相关服务；保留制品、SBOM、manifest、workflow logs 与 journal；撤销 credential；恢复到上一已验证制品；完成根因和影响范围审计后再恢复。
