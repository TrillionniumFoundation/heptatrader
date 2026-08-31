# Startup and Readiness

Status: current normative
Applies to: installed Simulator and qualified PAPER services
Verification: install-tree, config, identity, journal, snapshot and reconcile startup gates
Authority: startup sequence

启动顺序：验证二进制/安装树 → OS identity/socket/path mode → config/schema/digest → credential presence（仅 PAPER）→ journal ownership/replay → execution epoch/fence → venue connection → authoritative account/order/position snapshot → reconciliation → quote freshness → risk gate。

任何关键阶段失败保持 new-risk gate closed。ready 只在完整权威状态建立后发布；process alive、socket 可连或 Broker TCP connect 均不等于 ready。停止/重启前先冻结新风险、排空权威事件，并保存 journal/incident evidence。
