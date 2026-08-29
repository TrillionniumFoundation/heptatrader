# CI Gate（PR Gate / Release Gate）

用于 PR 验证与发布前门禁。当前发布门禁明确限定 **IB + CTP**，**XT 不参与放行判定**。

唯一 canonical phase gate 是 `hepta_ops` 的
`python3 scripts/hepta_ops.py release check --phase dev|rc|paper`（Linux/CI）。
Windows 的 `scripts/hepta.ps1 -Action release` 目前仍是 legacy
`release_check.ps1` 的兼容包装（参数名与 phase 对齐，但实现/receipt 链路尚未
完全迁移）；旧的 `ci_gate*.ps1`、`release_check.ps1` 和根目录
`gate-local.ps1` 均保留用于兼容/研究，不作为新的 Agent-OS/PAPER 默认入口。

---

## 0) Canonical release workflow (phase-based)

All local release decisions should use the single phase entrypoint below. It
runs the syntax/static preflight once and records it as `STATIC_CHECKS` in the
same `release_check.json` summary; the repository's full static contract gates
run in the one authoritative IB-off CTest lane. CI/release jobs should consume
those summaries rather than invoking a second copy of either gate.

On Linux (and in CI), use the native Python entrypoint:

```bash
umask 077
install -d -m 700 build-agent-os-release
cmake -S . -B build-agent-os-release \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DBUILD_TESTING=ON \
  -DHEPTA_SOAK_PROFILE=release \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF \
  -DHEPTA_BUILD_LEGACY_MONOLITH=OFF \
  -DHEPTA_BUILD_LEGACY_SIMULATOR=OFF
cmake --build build-agent-os-release --target hepta_agent_os_test_binaries --parallel 2
# CMake may create its cache with group-write bits; the canonical consumer
# requires the cache and report to be owner-readable/writable only.
chmod 600 build-agent-os-release/CMakeCache.txt
ctest --test-dir build-agent-os-release --output-on-failure
python3 scripts/hepta_ops.py release check --phase dev
python3 scripts/hepta_ops.py release check --phase rc \
  --build-dir build-agent-os-release \
  --soak-report build-agent-os-release/execution-gateway-short-soak.json \
  --soak-profile release \
  --report build-agent-os-release/ci-release-check.json
python3 scripts/hepta_ops.py release check --phase paper \
  --build-dir build-agent-os-release \
  --soak-report build-agent-os-release/execution-gateway-short-soak.json \
  --soak-profile release \
  --rc-report build-agent-os-release/ci-release-check.json \
  --rootful-report /path/to/rootful-receipt.json \
  --p1-report /path/to/p1-receipt.json \
  --authority-report /path/to/paper-authority-receipt.json
```

The Python command is authority-free: `paper` consumes an independently
produced **native disposable-VM aggregate** at `--rootful-report`
(`execution-native-systemd-aggregate.v6`), plus the producer-verified P1 and
PAPER candidate receipts. It verifies their schema/pass state and rejects any
receipt that claims authority was granted. It never
invokes `systemctl`, provisions a host, or enables broker access.

The older Docker/rootful effective-systemd rehearsal remains useful for
diagnostics and regression testing, but it is not a PAPER admission input.
Only the native aggregate reconstructed from three distinct externally
attested VMs can satisfy the rootful prerequisite.

```powershell
# Fast, offline developer feedback (sim profile; no broker/systemd access)
powershell -ExecutionPolicy Bypass -File .\scripts\hepta.ps1 -Action release -Phase dev

# Legacy Windows compatibility release check (the canonical phase gate is the
# Linux/Python command above; this wrapper still uses release_check.ps1).
powershell -ExecutionPolicy Bypass -File .\scripts\hepta.ps1 -Action release -Phase rc

# Legacy Windows PAPER compatibility gate (may run its own soak when supplied a
# build tree); the canonical Linux/CI gate above consumes an existing receipt.
powershell -ExecutionPolicy Bypass -File .\scripts\hepta.ps1 -Action release -Phase paper `
  -SoakBuildDir .\build-agent-os-release -SoakProfile release
```

The parameterized CTest soak accepts `HEPTA_SOAK_PROFILE=pr-smoke|release|nightly`:
PR smoke is two rounds (the former CTest short gate), while release and nightly
use the full eight rounds.
CTest writes `execution-gateway-short-soak.json`; the phase command consumes
that existing receipt and never launches a second soak. An explicit
`--rounds` remains available for controlled diagnostics when invoking the
standalone runner outside the release gate.

The phase command is a process simplification only; it does not remove safety
authority. The following gates remain mandatory for their respective phases:

| Phase | Mandatory gates |
| --- | --- |
| `dev` | Simulator config/profile lock plus the offline static preflight (no PAPER authority) |
| `rc` | The single IB-off Release CTest matrix (including the parameterized soak), source/config checks, and the separate IB-on targeted runtime/security job in CI |
| `paper` | The RC-equivalent config/static/soak checks plus independently produced rootful/systemd, P1 liveness, PAPER-admission-candidate, network-isolation, reconciliation, and end-flat receipts |

The phase script does not pretend to perform the host-bound IB healthcheck,
CTP regression, or rootful/network work itself.  Those checks stay in their
dedicated CI/root-owned jobs and are consumed as evidence at promotion time;
the script only validates the RC-equivalent receipts and the already-produced
CTest soak.  `paper --rc-report <receipt>` is optional; when supplied, the
protected `hepta.release-check.v1` receipt must be `phase=rc`, `overall=PASS`,
authority-free, and explicitly bound to the current config digest, soak
profile/round count, and current soak artifact digest.  Omitting it leaves the
paper gate's independent RC-equivalent checks unchanged.

Rootful, P1, and PAPER gates are never inferred from a local smoke result and
cannot be skipped by passing `-SkipHealthcheck`, `-SkipRegression`, or a short
PR soak. They must retain their existing root-owned/systemd and fail-closed
entrypoints and evidence receipts.

## 1) Compatibility: legacy PR Gate（允许 no-gateway）

以下入口仅服务旧 Windows/研究工作流；它们不构成 Agent-OS/PAPER 的默认
放行门。新的开发检查请使用上面的 `hepta_ops release check --phase dev`。

```powershell
powershell -ExecutionPolicy Bypass -File .\gate-local.ps1
```

等价：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ci_gate_pr.ps1 -ProjectRoot "$PWD"
```

可选（仅 PR 场景）：

```powershell
powershell -ExecutionPolicy Bypass -File .\gate-local.ps1 -NoLaunch
powershell -ExecutionPolicy Bypass -File .\gate-local.ps1 -SkipHealthcheck
powershell -ExecutionPolicy Bypass -File .\gate-local.ps1 -NoLaunch -SkipHealthcheck -SkipRegression
```

> `-NoLaunch -SkipHealthcheck -SkipRegression` 是 **no-gateway PR 兜底模式**，不可用于发布。

---

## 2) Compatibility: legacy Windows Release Gate（发布强制规则）

本节保留给尚未迁移的 Windows/IB+CTP 兼容链路。Agent-OS 的 Linux/CI 发布
判定以 phase gate 和独立 receipt 为准，不要把本节脚本当作第二套 canonical
实现。

发布前必须执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ci_gate_release.ps1 -ProjectRoot "$PWD"
```

### Release Gate 硬性要求（IB+CTP only）

Release gate 通过条件必须同时满足（全部 `pass=true` 且非 `skipped`）：

1. `BUILD`（`HeptaTrader.sln` Debug|x64）
2. `IB_HEALTHCHECK`
3. `IB_REGRESSION_ROUND`
4. `CTP_REGRESSION_ROUND`
5. `RECONCILE_CRITICAL_BLOCK`（启动对账报告不得有 CRITICAL）

因此，发布门禁不允许：

- 仅 no-gateway 通过就放行
- 跳过 IB/CTP 回归中的任一项
- 依赖 XT 检查结果作为放行依据

### Release Gate 明确禁止

- `-SkipHealthcheck`
- `-SkipRegression`

以上参数在 `ci_gate_release.ps1` 中会直接失败。

---

## 3) 机器可读输出（JSON 报告）

每次执行 `ci_gate*.ps1` 都会生成：

- `runtime-logs/ci-gate-<timestamp>/ci_gate_summary.json`
- `runtime-logs/ci-gate-<timestamp>/ci_gate_summary.txt`

示例路径：

- `runtime-logs/ci-gate-20260228-181530/ci_gate_summary.json`
- `runtime-logs/ci-gate-20260228-181530/build.stdout.log`
- `runtime-logs/ci-gate-20260228-181530/healthcheck.stdout.log`
- `runtime-logs/ci-gate-20260228-181530/ib_regression.stdout.log`
- `runtime-logs/ci-gate-20260228-181530/ctp_regression.stdout.log`
- `runtime-logs/ci-gate-20260228-181530/reconcile.stdout.log`

`ci_gate_summary.json` 关键字段：

- `overall`: `PASS|FAIL`
- `exitCode`: 门禁退出码
- `scope`: `IB+CTP`
- `excludes`: `["XT"]`
- `checks[]`: 每个检查项的 `name/pass/exitCode/detail/artifacts`

---

## 4) 失败退出码

### `ci_gate_pr.ps1` / `ci_gate.ps1`

- `0`：全部通过
- `10`：Build 失败
- `11`：Whitelist 失败
- `12`：Regression 失败
- `13`：IB Healthcheck 失败
- `16`：对账报告缺失或存在 CRITICAL
- `90`：脚本缺失
- `99`：未预期异常

### `ci_gate_release.ps1`

- `0`：全部通过
- `10`：Build 失败
- `11`：Whitelist 失败
- `12`：Regression 失败
- `13`：IB Healthcheck 失败
- `14`：禁止参数（`-SkipHealthcheck` / `-SkipRegression`）
- `15`：必需检查缺失或被跳过
- `16`：对账报告缺失或存在 CRITICAL
- `90`：脚本缺失
- `99`：未预期异常

---

## 5) 可执行命令（含验证样例）

### 正常发布门禁

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ci_gate_release.ps1 -ProjectRoot "$PWD"
```

### 仅校验 summary 策略（不重跑门禁）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ci_gate_release.ps1 `
  -ProjectRoot "$PWD" `
  -PolicyCheckOnly `
  -GateSummaryPath .\runtime-logs\ci-gate-20260228-181530\ci_gate_summary.json
```

### 故意失败样例（验证禁止 skip）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ci_gate_release.ps1 -ProjectRoot "$PWD" -SkipRegression
# 预期：exit code = 14
```
