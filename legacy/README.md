# Legacy Source Quarantine

Status: current boundary marker
Applies to: `legacy/` inactive source only
Verification: `python3 scripts/check_repository_integrity.py`
Authority: legacy quarantine boundary

`legacy/` 仅保存尚未删除的历史源码和受法律/供应链要求保留的 vendor provenance。它不是开发文档、构建入口、runtime dependency、capability 或部署路径。

- active target 不得 include、link、execute 或 install `legacy/`；
- 不在本目录维护历史开发计划、运行手册、截图说明或产品介绍；
- 新功能不得进入本目录；
- 删除或迁移遵循 [`../docs/governance/DEPRECATION-POLICY.md`](../docs/governance/DEPRECATION-POLICY.md)。

历史内容由 Git history 提供，不在 active tree 中复制。
