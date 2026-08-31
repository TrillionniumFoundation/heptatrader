# Kill Switch 与安全退出

Status: current normative
Applies to: Execution and operations
Verification: kill-switch tests and external qualification
Authority: kill-switch authority

Kill switch engagement 必须立即阻断新风险、保留已证明安全的 cancel/strict reduce-only/flatten、记录 transition reason/actor/epoch/timestamp、使所有新 permit/plan 失效，并在 restart 后保持，直到 operator 明确解除。

解除前要求 authoritative snapshot、journal health、connection 和 reconcile 全部满足。Kill switch 检查失败本身按 engaged 处理。策略、Agent、Global Decision 和 Management 模块不能解除 Execution kill switch。
