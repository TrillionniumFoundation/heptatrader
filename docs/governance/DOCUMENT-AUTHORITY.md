# 文档权威与唯一真相规则

Status: current normative
Applies to: `docs/`, root README, registries, generators and validators
Verification: `python3 scripts/generate_documentation_views.py --check` and `python3 scripts/check_documentation_control_plane.py`
Authority: documentation-governance authority

Hepta 文档控制平面由三类对象组成：规范性文档、机器注册表和 exact-revision 生成证据。

## 权威顺序

行为语义冲突时：

```text
CONSTITUTION / accepted ADR
  > versioned schema and contract
  > ModuleManifest and module registry
  > capability registry
  > program registries and generated views
  > explanatory prose
```

状态冲突时：

```text
exact-revision generated evidence
  > registry-derived state
  > generated Markdown view
  > PR summary or discussion
```

## 唯一正文

- 每个主题只能存在一个 canonical document 或 registry entry。
- Compatibility alias、redirect Markdown、复制正文、`docs/legacy/` 和 `docs/proposals/` 全部禁止。
- 历史开发文档、图像和 PDF 只存在于 Git history，不放入 active tree 或 `legacy/`。
- 生成视图只能由 `generate_documentation_views.py` 产生。
- 所有 `docs/` 文件必须被 document registry 恰好注册一次。
- 法律文本和 vendor provenance 不属于开发文档；前者位于仓库根，后者采用 JSON manifest/provenance。

## 元数据

每个规范性或生成 Markdown 在前 12 行内必须包含 `Status:`、`Applies to:`、`Verification:` 和 `Authority:`。规范文档不得硬编码 mutable commit SHA、当前 workflow 结论或“全部 gap 已关闭”等动态结论。

## 修改规则

新增主题前先确认不存在已有 authority；修改 registry 后重生成视图；删除文档时同时删除所有 registry、install、service、CI 和代码引用。一次变更如果无法让生成器与 checker 在同一树通过，不得进入评审。
