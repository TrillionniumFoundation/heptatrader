# Program Risk Register

Status: current normative program view
Applies to: roadmap execution, modular migration and capability promotion
Verification: gap registry, incident review and milestone gates
Authority: program risk authority

| ID | Risk | Trigger / leading signal | Owner | Required mitigation |
|---|---|---|---|---|
| R-001 | Multiple truth branches/doc paths | duplicate authority or stale link | documentation | one active graph, generated views, exact evidence |
| R-002 | Premature microservices | cross-process chatter/2PC/latency growth | architecture | monorepo targets first; process split only by trust/failure domain |
| R-003 | Optimizer hot-path SPOF | plan deadline misses or global queue | global allocation | hierarchical shards, expiry, no-plan/fallback, safe exit independent |
| R-004 | Proposal lacks economic surface | central allocator only receives one target | strategy platform | utility/uncertainty/factor/cost/capacity contract |
| R-005 | Shared source/target ownership | same `.cpp` compiled into multiple modules | platform | G-MOD-002 extraction and parity tests |
| R-006 | Numeric drift | cross-language digest/risk mismatch | contracts/risk | fixed boundary, canonical quantization, golden vectors |
| R-007 | Global lock contention | p99/p999 and queue-depth growth | reliability | shard/single-writer/per-thread telemetry |
| R-008 | Owner concentration | critical PR blocked by one person | program | DRI+backup+reviewer and team CODEOWNERS |
| R-009 | Mock misreported as PAPER evidence | qualification claim without broker-observed artifacts | IB/operations | exact external qualification schema and protected environment |
| R-010 | Automatic capability escalation | lifecycle config grants PAPER/LIVE | security | explicit change class; Management cannot grant venue authority |
| R-011 | Evidence/prose divergence | PR says closed while exact run failed | reliability | generated evidence dominates prose; no manual status docs |
| R-012 | Research leakage | revised/future data enters decision | research validation | point-in-time contract, purge/embargo/OOS, immutable digests |

每个风险必须关联 gap、owner、observable trigger 和 milestone exit；仅写“关注”不构成缓解。
