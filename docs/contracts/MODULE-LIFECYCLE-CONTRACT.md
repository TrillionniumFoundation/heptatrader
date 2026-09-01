# Module Lifecycle Contract V1

Status: current normative
Applies to: Management Control and simulator strategy modules
Verification: lifecycle-faults and rollout-rollback CTests
Authority: generation-fenced lifecycle state

每个模块版本绑定 module/version、artifact/config/model digest 与单调 generation。允许状态为 REGISTERED → WARMING → SHADOW → ACTIVE → DRAINING → STOPPED；任意非 stopped 状态可被 fail-closed QUARANTINED。所有 transition 使用 expected generation，旧 generation、时间回退、过期/不健康 evidence 和非法状态跳转均拒绝。

ACTIVE 升级会保存 previous-active identity 并进入 WARMING；若 shadow diverges 或运行故障，Management 可 quarantine 新版本，再以新 generation 恢复经健康验证的 previous active。Management 不持有 broker credential，不参与 tick hot path，也不能绕过 Execution。机器 schema 为 `schemas/module-lifecycle-v1.json`。
