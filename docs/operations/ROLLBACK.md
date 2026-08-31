# 回滚

Status: current normative
Applies to: modules, runtime deployments and releases
Verification: rollback fixture and protected operations process
Authority: rollback authority

回滚单位是已验证的完整 module/runtime artifact + config snapshot，而不是目标机手工替换单个二进制。

1. engage kill switch / stop new risk；
2. 保存 journal、alerts、current artifact/config identity；
3. drain 或停止受影响模块；
4. 恢复上一已验证 artifact/config；
5. 验证 install tree、state migration/replay；
6. 启动并完成 authoritative reconcile；
7. 仅在安全门满足后恢复新风险。

Contract major downgrade 必须有显式 state migration。无法证明兼容时保持停止或 flatten-only。
