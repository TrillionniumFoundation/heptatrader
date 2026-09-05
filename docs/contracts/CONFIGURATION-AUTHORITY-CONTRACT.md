# Configuration Authority Contract

Status: current target contract
Applies to: deployment, Management Control Plane, Gateway and Execution
Verification: source-conflict, profile-lock, digest and restart tests
Authority: configuration ownership and precedence

配置分为 immutable release defaults、deployment-reviewed environment、credential injection 和 runtime policy generation。每个字段只有一个 authority，来源冲突必须拒绝而不是按模糊优先级猜测。

配置对象必须包含 schema/version、environment、execution domain、module set、policy versions、effective time、generation 和 canonical digest。`sim` 与明确资格认证的 `paper` 是唯一可接受 profile；LIVE、CTP 和 XT/QMT 不得通过 account 名称、环境变量或隐藏配置推断开启。

影响 risk、venue、credential、network、journal 或 capability 的配置变更需要 restart/fencing 或显式热更新协议；读取失败、签名/digest 不匹配和 generation 回退均 fail closed。
