# Release Process

Status: current normative
Applies to: public core artifacts and future qualified optional packages
Verification: exact main candidate, reproducible build, install tree, SBOM, provenance and protected publish
Authority: release process

默认公开 release 是 IB-disabled core。它包含 Simulator/Gateway/CLI/MCP、canonical docs、schemas、research runner和service templates，不包含 Broker SDK/credential、CTP/XT payload或LIVE capability。

## Two-phase authority

1. **Build candidate**：read-only token，在两个干净目录独立构建/安装，比较 staging-independent manifest、archive和SBOM，生成 checksums和toolchain observation。
2. **Publish**：受保护 environment 验证明确批准的 exact SHA、candidate digest和前序 evidence后，才拥有最小 release/attestation 权限。

Tag 必须等于当前 `main` exact head；历史可达 commit、开发机旧 build、手工替换单个 binary均不允许。release archive 只含 install allowlist。Rollback 使用上一完整已验证 artifact/config snapshot，并保持 kill switch直到权威状态恢复。
