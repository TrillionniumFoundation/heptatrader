# Round37 Consolidation Contract

Status: canonical local-offline consolidation contract.

Round37 does not add a new trading feature or authorize a broker mode. It
turns the Round31-Round36 increment into a recoverable, reviewable Agent OS
product boundary.

## Product Definition

HeptaTrader is an Agent-native trading operating system in the Codex/OpenClaw
shape:

```text
Codex / OpenClaw / local Agent
  -> MCP / SDK / heptactl
  -> OS-owned Unix Tool Gateway
  -> OS-owned session and risk policy
  -> OS-owned Execution Service
  -> explicitly enabled venue adapter
```

The Agent operates the OS-exposed trading tools directly. It does not receive
broker credentials, own a broker socket, bypass risk policy, allocate durable
order IDs, or reconcile venue state.

## Five Workspace Layers

`policies/heptatrader-workspace-layout-v1.json` defines the retained boundary:

1. `agent-os-product`: native runtime, Agent adapters, service units, plugins,
   and CI.
2. `legacy-compat`: historical monoliths, strategy/simulator trees, direct
   broker scripts, and root compatibility wrappers.
3. `ops-evidence-tooling`: declarative operations registry, release builders,
   verifiers, tests, policies, and documentation.
4. `external-evidence-store`: runtime logs, evidence indexes, requests, and
   immutable retention objects.
5. `external-vendor-build-cache`: build trees, licensed SDK overlays, and
   generated caches.

Run the no-growth audit with:

```bash
python3 scripts/hepta_ops.py run repository.layout.audit
```

Runtime evidence and build caches are intentionally not source. The audit
warns when they exceed their externalization budgets and fails if volatile
content enters Git.

## Compatibility Wrapper Policy

New operational entry points must be declarative jobs in
`ops/hepta-ops-v1.json`. Generated compatibility wrappers live only under
`compat/hepta-ops-generated/` and are reproduced with:

```bash
python3 scripts/hepta_ops.py install \
  --output compat/hepta-ops-generated --check
```

Historical root wrappers remain a frozen compatibility inventory. They are
not moved or deleted while a deployed unit or running process still resolves
one of those paths. Retirement requires this sequence:

1. inventory every systemd, cron, shell, and operator reference;
2. map the wrapper to one canonical `hepta_ops` job;
3. migrate deployed references and complete an offline restart rehearsal;
4. preserve the wrapper for one published compatibility window;
5. remove it only in a separately reviewed cleanup change.

This preserves incremental code and service continuity while preventing
another wrapper tree from growing.

## Native Module Boundaries

Round37 splits high-coupling transport code without changing the wire
protocol:

- `typed_tool_framing.cpp` owns bounded frame transport;
- `typed_tool_protocol.cpp` owns typed request/session protocol behavior;
- `typed_tool_result_codec.cpp` owns result encoding and validation;
- `unix_execution_service.cpp` owns the server and execution dispatch;
- `unix_execution_service_client.cpp` owns client transport;
- `unix_execution_service_internal.h` contains the narrow shared internal
  declarations.

`policies/heptatrader-code-quality-v1.json` freezes line-count and CMake source
reference budgets so these modules cannot silently merge back into
monoliths:

```bash
python3 scripts/hepta_ops.py run repository.quality.check
```

Further extraction starts with characterization tests and preserves one
protocol implementation. It must not create a second order path.

## Agent-OS-Only Source

The strict full source archive remains a provenance parent. Product delivery
uses a second deterministic Agent-OS-only archive derived from that verified
parent:

```bash
python3 scripts/build_heptatrader_agent_os_source_bundle.py \
  --strict-source-tar /private/strict-source.tar \
  --strict-source-manifest /private/strict-source-manifest.json \
  --version 0.1.0-beta.1-round37 \
  --output /private/agent-os-source.tar \
  --manifest /private/agent-os-source-manifest.json
```

The Agent OS archive excludes the legacy strategy, simulator, Interface,
unsafe direct-broker, Tools, runtime-log, local build trees, repository CI,
compatibility wrappers, evidence/distribution tooling, and vendor metadata.
Its embedded manifest retains the verified strict-source lineage and Git
identity. A no-Git configure automatically registers the 61-test product
profile; the strict-source parent retains the 80-test repository profile.
Repository layout, source packaging, evidence, and retention governance remain
external gates and are not copied back into the product merely to satisfy
tests.

## CI Contract

The formal CI graph now enforces:

- Release builds with IB disabled and legacy compositions disabled;
- a mandatory pinned real-IB SDK compile with legacy compositions disabled;
- strict-source to Agent-OS-only derivation, extraction, and the 61-test
  no-Git product profile;
- nightly ASAN, UBSAN, and TSAN offline native suites;
- nightly `gcovr` line coverage at the policy floor;
- workspace-layout, code-quality, source-baseline, and wrapper no-growth
  gates on the strict-source repository profile.

Sanitizer and coverage jobs are offline and IB-disabled. They do not connect
to a broker or authorize PAPER/LIVE.

## Remaining External Gates

Local consolidation can certify source, packaging, protocol, process, and
offline fault behavior. Final certification remains `pending-external` until:

1. three independent disposable VMs prove the four-UID rootful systemd
   Agent/WATCH runtime;
2. an external immutable object store proves indefinite retention, legal
   hold, root-owned trust, and a trusted signed receipt.

After those gates, PAPER still requires a separate capped authorization.
LIVE remains isolated and unauthorized.
