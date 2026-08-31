# IB PAPER qualification protocol

Status: repository-side qualification contract. This document does not claim that a real qualification campaign has already run.

## Purpose

`hepta-ib-executiond` may be built on a controlled runner with an authorized local IB C++ API, but build success alone does not qualify unattended PAPER execution. A valid qualification must bind one exact source commit, one exact broker-owning binary, one controlled harness, one real IB PAPER session and all required fault scenarios into an independently replayable evidence set.

Simulator, mock, test-double, hand-written status JSON, a read-only smoke test, a different commit, a rebuilt binary or an incomplete scenario list cannot produce `qualified=true`.

## Trust boundary

The GitHub workflow `.github/workflows/ib-paper-qualification.yml` runs only on a separately administered self-hosted runner in the protected `ib-paper` environment. The runner supplies:

- an authorized, local and pinned `IBAPI_ROOT`;
- executable `HEPTA_IB_PAPER_QUALIFIER` harness code;
- a loopback IB Gateway or TWS session connected to a PAPER account;
- operator approval for bounded PAPER mutations.

The repository does not contain broker credentials and cannot create genuine Broker evidence by itself. The external harness owns test orchestration only. It may not weaken runtime risk, journal, fencing, reconciliation or kill-switch logic.

## Invocation

Qualification is deliberately explicit and mutation-gated:

```bash
export HEPTA_IB_PAPER_QUALIFIER=/opt/hepta/controlled/ib-paper-qualifier
export HEPTA_QUALIFICATION_MUTATIONS=1
./scripts/run_ib_paper_qualification.sh build/ib "$RUNNER_TEMP/heptatrader-ib-evidence"
```

The wrapper refuses read-only mode, a modified source checkout, a missing or symlinked harness, a missing broker-enabled binary and an existing evidence destination. It executes the harness in a private temporary directory, independently verifies the complete result, and only then atomically publishes the final evidence directory.

The harness receives these arguments:

```text
--repository-root <path>
--build-dir <path>
--execution-binary <path>
--expected-binary-sha256 <64 hex>
--expected-git-sha <40 hex>
--required-scenarios <comma-separated canonical IDs>
--evidence-dir <private directory>
--result <evidence-dir>/qualification-result.json
--mode bounded-mutations
```

Equivalent `HEPTA_QUALIFICATION_*` environment bindings are also exported. The harness must write only the result and referenced evidence files beneath the supplied private directory.

## Canonical result schema

`qualification-result.json` uses exact schema `hepta.ib-paper-qualification.v1`. Unknown or duplicate keys, non-UTF-8 input, non-finite JSON numbers and non-canonical values are rejected.

```json
{
  "schema": "hepta.ib-paper-qualification.v1",
  "qualified": true,
  "mode": "bounded-mutations",
  "git_sha": "<40 lower-case hex>",
  "binary": {
    "name": "hepta-ib-executiond",
    "sha256": "<64 lower-case hex>"
  },
  "harness": {
    "name": "ib-paper-qualifier",
    "sha256": "<64 lower-case hex>"
  },
  "broker": {
    "venue": "IB",
    "environment": "PAPER",
    "transport": "TWS_API",
    "api_version": "<observed API version>",
    "session_id": "<campaign session ID>",
    "account_fingerprint": "sha256:<non-reversible account fingerprint>",
    "host_fingerprint": "sha256:<controlled runner fingerprint>",
    "origin": "broker-observed",
    "simulated": false,
    "test_double": false
  },
  "started_at_ms": 1800000000000,
  "completed_at_ms": 1800000120000,
  "scenarios": []
}
```

Never place a raw account number, username, credential, auth token or host secret in qualification evidence. Account and host identities use deployment-controlled salted fingerprints.

Each scenario is an exact object:

```json
{
  "id": "partial_fill",
  "status": "PASS",
  "started_at_ms": 1800000010000,
  "completed_at_ms": 1800000020000,
  "assertions": [
    "FINAL_TERMINAL_STATUS",
    "PARTIAL_FILL_OBSERVED",
    "REMAINING_QUANTITY_RECONCILED"
  ],
  "evidence": [
    {
      "path": "scenarios/partial_fill/broker-callbacks.jsonl",
      "kind": "broker-callbacks",
      "sha256": "<64 lower-case hex>",
      "size": 1234
    },
    {
      "path": "scenarios/partial_fill/oms-journal.jsonl",
      "kind": "oms-journal",
      "sha256": "<64 lower-case hex>",
      "size": 2345
    }
  ]
}
```

## Required scenarios and invariant assertions

The result must contain the following scenarios exactly once and in canonical order:

1. `connect_authoritative_snapshot`: connection ready plus complete authoritative account, position and open-order boundaries.
2. `disconnect_reconnect`: disconnect observed, risk increase blocked, connection epoch changed and reconciliation complete.
3. `partial_fill`: partial fill observed, remaining quantity reconciled and final terminal status reached.
4. `duplicate_out_of_order_status`: duplicate callback is idempotent, out-of-order callback is rejected or reconciled and projection converges.
5. `broker_reject`: real Broker rejection retained durably and order not left active.
6. `stale_quote`: stale quote rejects risk increase with no place-send attempt.
7. `outcome_uncertain`: uncertainty is durable, no blind retry occurs and authoritative resolution is obtained.
8. `cancel_race`: callback/cancel race converges without duplicate cancellation effect.
9. `reconcile_divergence`: divergence is detected, risk increase blocked and state resolved or terminally latched.
10. `lease_fencing`: stale lease rejected, current lease accepted and stale authority causes no venue send.
11. `kill_switch`: switch engagement blocks risk increase while bounded exit paths remain available.
12. `terminal_recovery`: process restart, journal replay and Broker reconciliation complete without duplicate risk increase.

The precise required assertion tokens and evidence-kind pairs are versioned in `scripts/verify_ib_paper_qualification.py`. Changing them requires code review and a schema-version decision; the harness cannot omit or substitute them.

## Evidence filesystem contract

The verifier requires:

- evidence root owned by the verifier user with mode `0700`;
- result and every evidence item to be a single-link regular file;
- no symlink, hard link, special file, group/world-writable file or replaceable directory;
- canonical relative paths under `scenarios/<scenario-id>/`;
- stable inode, size, timestamps and path identity throughout each read;
- exact declared SHA-256 and byte size;
- no evidence file reused between scenarios;
- no unreferenced files except the generated verification receipt;
- bounded result, evidence-file and campaign sizes.

After all checks pass, the verifier writes `qualification-verification.json` with schema `hepta.ib-paper-qualification-verification.v1`, the exact result digest, binary and harness identities, scenario receipts and evidence totals. The receipt is mode `0600` and published with the rest of the directory.

## Qualification state

A successful repository CI run does not imply IB PAPER qualification. A successful manual qualification workflow only applies to its exact commit, binary SHA-256, harness SHA-256, Broker session and evidence artifact. Rebuilding, changing configuration, changing the harness or changing the IB API invalidates that identity and requires a new campaign.

IB LIVE remains outside this protocol and is always NO-GO unless a separate, explicitly reviewed LIVE authorization and qualification system is created.
