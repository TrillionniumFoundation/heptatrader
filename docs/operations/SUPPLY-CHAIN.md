# 供应链安全

Status: current normative
Applies to: dependencies, CI actions, toolchains, artifacts and vendor SDKs
Verification: lock files, dual clean build, install-tree identity, SPDX SBOM and provenance checks
Authority: supply-chain authority

## Source and dependency controls

- GitHub Actions 使用 exact commit pin；
- hosted runner 使用固定 major image（当前 release evidence 为 `ubuntu-24.04`），并把实际 image/version 写入 toolchain observation；
- compiler、CMake、Ninja、Python、OpenSSL 和构建 profile 记录精确版本；
- third-party dependency 有来源、版本、license、digest；
- proprietary SDK 不进入公开 core artifact；
- IB API 只允许作为受保护 PAPER qualification 的显式外部输入；
- 构建使用干净目录，禁止复用开发机旧产物；
- credential、token、raw account ID 不进入日志、SBOM、qualification evidence 或 release。

## Reproducible candidate evidence

`.github/workflows/release-candidate-evidence.yml` 是只读、手动触发的候选观察 lane。它在同一 exact SHA、同一 `SOURCE_DATE_EPOCH` 和同一记录工具链下执行两个相互独立的 IB-disabled Release configure/build/install。每个 staging tree 先通过 `scripts/check_install_tree.py`，然后交给 `scripts/generate_release_evidence.py`。

生成器执行以下 fail-closed 验证：

1. 两个 install root 都必须是真实、非 symlink、非 world-writable 的目录；
2. 递归条目只能是 regular single-link files 和安全目录；
3. symlink、hardlink、special file、set-id、world-writable、读取期间 identity 变化全部拒绝；
4. relative path、mode、size 和 SHA-256 集合逐项一致；
5. toolchain metadata 使用 closed/bounded JSON，拒绝 duplicate key 与 secret-like value；
6. source identity 必须是精确 40 位 Git SHA；
7. 任一不一致时不产生可接受 evidence directory。

成功输出为：

- `install-manifest-v1.json`：路径、mode、size、SHA-256 和 tree digest；
- `SHA256SUMS`：安装树每个文件的确定性校验和；
- `sbom.spdx.json`：绑定 source/version/file checksums 的 SPDX 2.3 SBOM；
- `provenance-v1.json`：绑定 Git SHA、版本、profile、两次构建、toolchain 与 subjects；
- `evidence-index-v1.json`：前述 evidence 文件的 digest 索引。

该 evidence 证明的上限只是“该 exact IB-disabled core 候选在记录环境内可重现”。它不执行 publish，不签发 PAPER/LIVE capability，也不能代替 protected publish、独立批准或 IB PAPER real-environment qualification。

## Install and consumer boundary

install tree 拒绝 symlink escape、special file、world-writable 文件和未声明路径，并必须包含 Apache-2.0 `LICENSE` 与 `NOTICE`。consumer 在解包前验证 artifact digest，在解包后重新验证 install manifest、路径、mode、size 和文件 digest；只检查压缩包 hash 或只检查单一 binary 均不充分。

Vendor provenance/法律 notice 是保留对象，不属于历史开发文档清理范围。`legacy/` 与 vendor-designated 内容不因项目 Apache-2.0 许可证自动获得再许可，默认 runtime install allowlist 不包含这些内容。
