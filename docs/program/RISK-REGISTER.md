# Program Risk Register

Status: current normative program view
Applies to: roadmap execution, modular migration and capability promotion
Verification: gap registry, incident review and milestone gates
Authority: program risk authority

| ID | Risk | Trigger / leading signal | Owner | Gap / exit milestone | Required mitigation |
|---|---|---|---|---|---|
| R-001 | Multiple truth branches or document paths | duplicate authority, compatibility alias, stale link or copied status | documentation | `G-DOC-001`, `G-DOC-003` / `M0`, `M1` | one registered graph, deterministic views, Git-only history and exact evidence |
| R-002 | Premature microservices | cross-process chatter, distributed locking, 2PC or latency growth before target boundaries are stable | architecture | `G-MOD-001`, `G-MOD-002` / `M2` | monorepo targets first; process split only by trust, state and failure domain |
| R-003 | Optimizer hot-path single point of failure | plan-deadline misses, global queue growth or risk-increasing reuse of stale plans | global allocation | `G-CONC-001`, `G-OPT-002` / `M3`, `M4` | hierarchical shards, expiry, no-plan/fallback states and independent safe-exit path |
| R-004 | Strategy proposal lacks an economic surface | central allocator receives only one target and cannot compare utility, uncertainty, factor, cost or capacity | strategy platform | `G-OPT-001` / `M4` | bounded StrategyProposal candidates/feasible region with utility, uncertainty, exposure, cost and capacity contracts |
| R-005 | Shared source or target ownership becomes permanent | one production source is compiled into multiple module targets or a broad root gains new claimants | platform | `G-MOD-001`, `G-MOD-002` / `M2` | one physical owner, exact same-gap exceptions, configured CMake graph validation and extraction parity tests |
| R-006 | Numeric drift across modules or languages | digest, rounding, risk or allocation mismatch for equivalent input | contracts / risk | `G-CON-001`, `G-NUM-001` / `M3` | fixed boundary units, canonical quantization, overflow rejection and cross-language golden vectors |
| R-007 | Global lock or queue contention | p99/p999, queue depth, starvation or emergency-lane delay increases | reliability | `G-CONC-001`, `G-REL-002` / `M3`, `M7` | shard/single-writer design, per-thread telemetry and same-fixture performance gates |
| R-008 | Ownership concentration | critical review, incident or release is blocked by one person | program | `G-TEAM-001` / `M7` | DRI, backup, cross-domain reviewer, verified GitHub teams, CODEOWNERS and branch ruleset |
| R-009 | Mock or Simulator result is reported as PAPER evidence | qualification claim lacks broker-observed exact-artifact records | IB / operations | `G-IB-001` / `M6` | protected external harness, exact source/binary/config/session identity and required fault scenarios |
| R-010 | Automatic capability escalation | lifecycle/configuration change implicitly grants credential, PAPER or LIVE authority | security | `G-LIFE-001`, `G-IB-001` / `M5`, `M6` | explicit A3/O4 change class; Management and Global Decision cannot grant venue mutation authority |
| R-011 | Evidence and prose diverge | PR body or document says closed while current required run is missing or failed | reliability | `G-DOC-003`, `G-REL-001` / `M0`, `M1` | live metadata/evidence dominates prose; no mutable SHA/status copy in normative docs or PR body |
| R-012 | Research leakage | revised, future or non-point-in-time data enters a decision or promotion | research validation | `G-RES-001` / `M3` | point-in-time contract, purge/embargo/OOS, immutable input/output digests and capability-free promotion |
| R-013 | Scheduler-probability concurrency evidence | a queue/fairness test relies on sleep, thread-start order, socket-connect count or rerun luck | reliability | `G-REL-001`, `G-REL-002` / `M0`, `M7` | explicit active-state barrier, bounded-queue witness, exact rejection, unrelated-owner progress and teardown-before-assert |
| R-014 | Stacked base silently transfers unresolved defects | child PR is green in one lane while its direct base remains failed or unaccepted | architecture / reliability | `G-REL-001` / `M0` | preserve stack identity, independently qualify the base, then rerun unchanged child head and merge candidate |

A risk entry is actionable only when its owner, observable trigger, linked open gap, exit milestone and required mitigation are explicit. Risk closure is derived from the linked gap and exact evidence; editing this table cannot close a risk.
