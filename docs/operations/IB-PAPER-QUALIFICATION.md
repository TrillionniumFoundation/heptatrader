# IB PAPER Qualification

Status: current external target contract
Applies to: optional `hepta-ib-executiond` and controlled PAPER hosts
Verification: protected self-hosted workflow and independent evidence verifier
Authority: IB PAPER qualification

Build success、Simulator、mock、read-only smoke或手写 JSON不能产生 `qualified=true`。有效 qualification 绑定 exact source/tree、broker-owning binary SHA-256、config digest、pinned IB API、controlled harness、host/account fingerprints、real PAPER session和完整 scenario evidence。

## Required scenario families

authoritative startup snapshot、disconnect/reconnect、partial fill、duplicate/out-of-order status、broker reject、stale quote、uncertain outcome、cancel race、reconcile divergence、lease/fencing、kill switch和terminal restart/recovery。每个 scenario 同时提供 Broker-observed callbacks、OMS/journal/state证据和断言 token。

Evidence root 禁止 symlink/hardlink/special/world-writable/unreferenced file；所有文件有 declared size/digest并在读取期间保持 inode/path稳定。raw account、username、credential或host secret不得出现。

Qualification 只适用于该 exact identity。重新构建、改 config、SDK、harness、host或session都需要新 campaign。PAPER qualification 永不授予 LIVE。
