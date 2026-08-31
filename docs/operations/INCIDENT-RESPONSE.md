# 事故响应

Status: current normative
Applies to: runtime incidents and on-call response
Verification: incident drills, fault matrix and post-incident review
Authority: incident response authority

优先级：

1. 保护账户和外部状态；
2. 阻断新风险；
3. 保持 cancel/reduce/flatten；
4. 保存 journal、event、snapshot、config 和 binary identity；
5. 建立 Broker authoritative truth；
6. reconcile；
7. 再决定恢复。

P1 包括 send 无 durable command、journal failure 且新风险门仍开放、position/order break 泄漏到风险授权、kill switch 无法执行、stale snapshot 被接受、epoch/fencing mismatch、uncertain exposure 超过 reconcile deadline。

事故期间禁止删除 Journal、切换 command ID、手改权威状态或临时开放 LIVE/原始 Broker 路径。
