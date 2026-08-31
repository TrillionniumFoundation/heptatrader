# Identity and Capability Contract

Status: current core contract
Applies to: session supervisor, Gateway, native/MCP clients and operator profiles
Verification: peer identity, lease, token, capability and negative tests
Authority: Agent-side authorization boundary

授权决策绑定 OS peer identity、session ID、lease generation、capability set、socket trust domain、expiry 和 audit sequence。token 只证明受控 session possession，不授予 Broker truth或绕过 Execution。

普通 Agent 只能获得 read、bounded intent、cancel/flatten 等明确 capability；raw place 属于独立 operator profile。未知 capability、过期 lease、UID/GID 不匹配、附加组不符、token path 不安全或 audit persistence 失败时拒绝。

Capability 名称是版本化有限集合。tool 名称不是 capability；一个 tool 的 capability requirement 必须由 tool catalog 声明并由 Gateway 与 Execution 双重约束。Management 不能通过配置把 unsupported/LIVE capability 变为可用。
