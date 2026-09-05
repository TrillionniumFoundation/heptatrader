# Release Process

Status: current normative
Applies to: public core artifacts and future qualified optional packages
Verification: exact main candidate, reproducible install tree, SPDX SBOM, provenance and protected publish
Authority: release process

默认公开 release 是 IB-disabled core。它包含 Simulator/Gateway/CLI/MCP、canonical docs、schemas、research runner、service templates、`LICENSE` 和 `NOTICE`，不包含 Broker SDK/credential、CTP/XT payload或LIVE capability。

## Two-phase authority

### Phase 1 — read-only build candidate

`release-candidate-evidence` 使用 read-only token 和固定 major runner image，在两个干净 build/staging directory 中从同一 exact SHA 构建。两个构建共享记录的 `SOURCE_DATE_EPOCH`，同时记录实际 runner image、compiler、CMake、Ninja、Python 与 OpenSSL 版本。

候选只有在以下条件同时成立时才可交给 publish review：

1. source checkout 等于 workflow dispatch 的 exact SHA；
2. repository、documentation、module 和 install-tree validators 通过；
3. 两个 IB-disabled install trees 的 path/mode/size/SHA-256 全部一致；
4. 无 symlink、hardlink、special/set-id/world-writable 或读取期间变化；
5. `install-manifest-v1.json`、`SHA256SUMS`、`sbom.spdx.json`、`provenance-v1.json` 和 `evidence-index-v1.json` 完整生成；
6. provenance 明确声明 `vendor_sdks_included=false`、LIVE forbidden，且 PAPER 未被该 evidence 资格化。

该 workflow 只上传 evidence artifact，不创建 tag、release、attestation、deployment 或 capability 状态。

### Phase 2 — protected publish

未来 publish workflow 必须运行在独立受保护 environment，且只接受明确批准的：

- 当前 `main` exact head；
- Phase 1 evidence index digest；
- install tree digest；
- SPDX SBOM digest；
- provenance digest；
- 预期 version/tag；
- 最小 publish/attestation credential。

Publish environment 的 approver 不能是候选作者或 publish 执行者。publish job 不重新编译、不替换单一 binary、不重新生成能力声明；它只能发布已批准的完整 artifact/evidence set。Tag 必须等于当前 `main` exact head。历史可达 commit、开发机旧 build、手工替换文件或仅有绿色 PR check 均不允许。

## Optional PAPER package

IB PAPER 仍是独立资格域。即使 core release 可重现，也只有与 exact binary、config、official SDK、harness、host/account/session 和 broker-observed scenarios 绑定的受保护 qualification receipt，才能使那个特定 PAPER artifact 进入可发布候选。PAPER qualification 永不授予 LIVE。

## Rollback

Rollback 使用上一完整已验证 artifact、manifest、SBOM、provenance 和 config snapshot；不得拼装版本。Execution 在 journal replay、authoritative snapshot 和 reconciliation 完成前保持 new-risk gate closed，kill switch 的安全退出优先级不因 release 回退而降低。
