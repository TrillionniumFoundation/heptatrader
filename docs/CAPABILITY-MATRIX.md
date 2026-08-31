# Capability matrix

本矩阵是对外声明和发布判定的唯一能力基线。目录、类名或示例配置的存在不等于已支持。

| Surface | Build status | Runtime status | Release gate |
|---|---|---|---|
| Typed protocol / MCP / CLI | Built by default | Implemented | Core CI + protocol tests |
| Tool Gateway / session supervisor | Built by default | Implemented | Core CI + forbidden-symbol gate |
| Execution coordinator / OMS / reconcile | Built by default | Implemented | Core CI + durability tests |
| Deterministic Simulator | Built by default | Implemented | Simulator E2E and install verification |
| IB PAPER | Optional `HEPTA_ENABLE_IBAPI=ON` | Conditional | Pinned SDK build + controlled PAPER qualification evidence |
| IB LIVE | No certified profile | Unsupported | New threat model, authorization and independent qualification required |
| CTP | Legacy overlay only | Unsupported / fail-closed | Origin, license, transport, lifecycle, reconcile and PAPER evidence required |
| XT / MiniQMT | Event scaffold only | Unsupported / fail-closed | Reviewed transport, authoritative state, risk and qualification required |
| QMT CSV bridge | Documentation only | Offline research input only | No automatic execution path |
| Legacy monolith | Explicit opt-in | Deprecated | Never accepted as canonical production authority |

## Status meanings

- **Implemented**：默认构建、测试并包含在核心安装树中。
- **Conditional**：代码存在，但需要仓库外受控资源和同一提交的通过证据。
- **Unsupported / fail-closed**：所有可能产生真实副作用的调用必须返回失败，不得伪造成功。
- **Deprecated**：仅用于历史迁移或研究，不允许扩展为新的生产旁路。

任何状态升级都必须同时更新本文件、README、测试、安装图、运行手册和资格认证流程。不得只修改宣传文本。
