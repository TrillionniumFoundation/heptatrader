# Documentation Control Plane Continuous Upgrade Plan

Status: current normative plan
Applies to: documentation, registries, generators, validators, CI and install tree
Verification: M0/M1 gaps and exact-revision gates
Authority: documentation-upgrade implementation sequence

## Objective

当前树只保留一套最新开发文档；规范事实单一、结构事实机器可读、动态完成状态由 evidence 派生，任何历史路径或复制正文不能污染后续开发。

## Sequence

1. **Physical cleanup**：删除 alias、legacy docs/media、old status、old registry 和所有 install/service/CI 引用。
2. **Authority closure**：document registry 恰好覆盖 `docs/`；法律与 vendor provenance 与开发文档分离。
3. **Generated views**：Capability Matrix、Contract Index、Module Map、Roadmap 只能从 registry 生成。
4. **Traceability**：capability → module → contract → verification → milestone/gap 全链路可验证。
5. **Runtime alignment**：module registry 区分真实 current target、planned target 和 shared-migration debt。
6. **Exact-revision evidence**：stacked PR、merge candidate、release、qualification 分层验证，不用 prose 宣告完成。

## Exit

- `docs/` 顶层仅有 `README.md` 和 document registry；
- `legacy/` 不含 Markdown、文本说明、PDF 或图片；
- old aliases、old PLAN、manual exact-head files 为零；
- generator `--check`、documentation checker、repository checker、module checker、install checker和全部 core CI 同一 SHA 通过；
- 所有清理后的引用不存在；
- M0/M1 状态只能由 evidence 关闭。
