# 契约索引

Status: current generated view
Applies to: `docs/contracts/contract-registry-v1.json`
Verification: `python3 scripts/check_documentation_control_plane.py`
Authority: contract catalog view

核心契约分为三层：

1. **输入/决策契约**：snapshot、proposal、optimization、allocation、intent；
2. **执行契约**：permit、command、event、venue、reconciliation；
3. **管理契约**：ModuleManifest、lifecycle、capability 和 qualification。

兼容规则：additive optional field 仅在默认语义明确时允许 minor version；删除、重命名、单位或 authority 改变必须 major version；producer 先支持新旧读取，consumer 完成迁移后才能停止旧写入；schema digest、canonical serialization 和 unknown-field policy 必须测试；contract 不能通过自由文本扩展权威字段。
