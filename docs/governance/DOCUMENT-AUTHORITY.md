# 文档权威与唯一真相规则

Status: current normative
Applies to: `docs/`, root README and documentation validators
Verification: `python3 scripts/check_documentation_control_plane.py`
Authority: documentation-governance authority

Hepta 文档控制平面由三类对象组成：

1. **Normative documents**：定义长期稳定的安全、架构、契约与治理语义。
2. **Machine registries**：定义模块、能力、契约、里程碑、gap、测试和预算的结构化事实。
3. **Generated evidence**：由 CI、release 或受控 qualification 在 exact revision 上生成的动态结果。

## 权威优先级

行为规范发生冲突时：

```text
CONSTITUTION / accepted ADR
  > versioned contract or schema
  > ModuleManifest / module registry
  > capability registry
  > roadmap / gap / milestone registry
  > explanatory prose
```

完成状态发生冲突时：

```text
same-revision generated evidence
  > registry-derived state
  > PR summary
  > hand-written status prose
```

## 唯一正文规则

- 每个主题只能有一个 canonical document。
- 旧路径可以保留 compatibility alias，但 alias 只能声明目标路径，不得复制规范正文、状态或命令。
- `docs/legacy/`、历史认证叙事、旧 round/finalizer 文档和已废弃 proposal 不属于 active graph。
- 版本历史由 Git 保存，不在 active tree 中复制归档。
- 法律文件、第三方 notice 和 vendor provenance 不属于开发文档清理范围。

## 元数据

每个 Markdown 在前 12 行内必须包含：

```text
Status:
Applies to:
Verification:
```

规范文档还应声明 `Authority:`。动态 SHA、CI 结果和“全部 closed”不得硬编码在规范文档中。
