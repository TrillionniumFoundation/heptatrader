# 契约与模块弃用策略

Status: current normative
Applies to: contracts, modules, capabilities and installation surfaces
Verification: `python3 scripts/check_documentation_control_plane.py` and compatibility tests
Authority: deprecation authority

弃用是有界状态机：

```text
active -> deprecated -> disabled-by-default -> removed
```

每次弃用必须声明 replacement、first deprecated version、compatibility window、state migration、remaining-use telemetry、removal gate 和 rollback。

禁止 active target 依赖 `legacy/`，禁止 deprecated adapter 返回成功，禁止旧 schema 无版本解释地继续写入，禁止在 active docs 复制历史操作手册，禁止通过示例重新激活旧路径。

Git history 是历史材料保存位置；active tree 只保留仍承担安全、法律或兼容责任的最小对象。
