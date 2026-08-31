# 供应链安全

Status: current normative
Applies to: dependencies, CI actions, toolchains, artifacts and vendor SDKs
Verification: lock files, reproducible build, SBOM and provenance checks
Authority: supply-chain authority

- GitHub Actions 使用 exact commit pin；
- hosted/self-hosted toolchain 记录精确版本；
- third-party dependency 有来源、版本、license、digest；
- proprietary SDK 不进入公开 core artifact；
- 构建使用干净目录，禁止复用开发机旧产物；
- install tree 拒绝 symlink escape、special file、world-writable 文件和未声明路径；
- artifact 生成 SPDX SBOM、manifest、SHA256SUMS 和 provenance；
- consumer 在解包前后分别验证 artifact 和 install tree；
- credential、token、raw account ID 不进入日志、SBOM、qualification evidence 或 release。

Vendor provenance/法律 notice 是保留对象，不属于历史开发文档清理范围。
