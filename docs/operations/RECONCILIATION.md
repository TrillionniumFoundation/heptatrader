# Reconciliation and Uncertain Outcomes

Status: current normative
Applies to: OMS, state authority, Execution and qualified venue adapters
Verification: crash/replay, disconnect, duplicate/out-of-order, divergence and qualification scenarios
Authority: authoritative recovery

启动、重连、周期 shadow reconcile 和 uncertain command resolution 都比较 durable command/event projection 与 Broker-observed open orders、positions、cash/account and terminal states。

动作优先级：terminal latch/block > manual isolation > warn > resolved. Open-order或position mismatch、unknown send outcome、epoch/fence disagreement必须关闭 new-risk gate。现金/PnL等字段能否降级取决于启用的 risk rule；未知值不能当零。

uncertain retry 复用原 command ID 和 payload，先 query durable outcome/Broker correlation，不盲目发送新 order。duplicate/out-of-order callback 以 venue identity、sequence和lifecycle validator处理。无法证明收敛时保持终态 latch，operator 只能走明确 remediation/safe-exit 流程。
