# 发布流程

Status: current target contract
Applies to: release candidate, artifacts and publication
Verification: Lane C evidence, protected release environment and provenance
Authority: release authority

默认 release 是 **IB-disabled core package**。

前提：tag commit 等于当前受批准的 main/merge candidate；Lane C 全部成功；toolchain/action pin 可验证；version、capability registry、install graph 一致；无未处理 P0 authority/security regression；protected release environment 完成独立审批。

**Build candidate（只读）**

1. 两个干净 build/staging 目录独立构建；
2. 比较 install manifest、SBOM 和 deterministic archive；
3. 生成 SHA256SUMS、toolchain observation 和 artifact identity；
4. 上传仅与 exact SHA 绑定的候选 artifact。

**Publish（受保护写权限）**

1. 下载 exact candidate；
2. 验证批准 SHA 和所有 digest；
3. 生成 provenance/attestation；
4. 最后创建 release。

IB PAPER 只能作为单独、已资格认证的 optional artifact；PAPER 资格不继承到 LIVE。
