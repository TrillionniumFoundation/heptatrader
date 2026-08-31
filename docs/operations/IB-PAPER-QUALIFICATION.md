# IB PAPER 资格认证

Status: current target external contract; no qualification claim
Applies to: optional IB PAPER artifact and controlled qualification environment
Verification: protected external qualification lane and evidence verifier
Authority: IB PAPER qualification authority

build 成功不等于 PAPER qualified。有效资格认证必须绑定 exact source SHA、exact `hepta-ib-executiond` binary digest、exact config/IB API/harness digest、受控 host、真实 PAPER session、不可逆 account/host fingerprint、完整 scenario evidence 和 verification receipt。

必需场景：

1. connect + authoritative snapshot；
2. disconnect/reconnect + epoch change + reconcile；
3. partial fill；
4. duplicate/out-of-order status；
5. real Broker reject；
6. stale quote；
7. uncertain outcome；
8. cancel race；
9. reconcile divergence；
10. lease/fencing；
11. kill switch；
12. terminal recovery。

证据目录必须私有、无 symlink/hardlink/special file、所有文件 digest/size/path 稳定、无未引用文件。mock、Simulator、手写 JSON、不同 commit 或 rebuilt binary 不能产生 `qualified=true`。

SDK、config、binary、harness 或环境身份变化会使资格失效。IB LIVE 永远不由 PAPER 资格继承。
