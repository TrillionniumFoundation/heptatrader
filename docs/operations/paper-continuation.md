# Continuous PAPER testing of one immutable artifact

Status: CURRENT
Applies to: controlled owner-operated PAPER-V4 integration
Implementation: `.github/workflows/ib-paper-qualification.yml`, `scripts/hepta_paper_rollout_host.py`

## Preconditions and scope

The workflow must be merged into main before dispatch. Required external inputs are
an isolated IB SDK/BID builder, a qualified PAPER host/runtime, the root-owned host
probe, a real [typed host driver](../technical/ib-paper-host-driver.md), protected
`ib-paper` environment, and an admitted non-secret campaign file. Source CI does not
supply these. LIVE, multiple active orders and multi-contract IB expansion stay off.

Only the configured owner identity can dispatch. Build has no Broker environment and
does not require mutation opt-in. A later campaign explicitly selects mutation mode
and the protected environment; it still cannot override host or Execution authority.

## Build once

In GitHub Actions select **IB PAPER Build Once and Continuous Campaign** (the workflow
file is `ib-paper-qualification.yml`), dispatch from main with:

```text
candidate_sha = exact current main SHA
rollout_stage = build
artifact_id   = empty
mutation_mode = false
```

Retain the successful build job's numeric artifact ID, source SHA and archive SHA-256.
The executable/manifest digests remain part of the immutable verified package. A TCP
probe or a build receipt does not authorize an order.

## Admit the host binding once

The operator installs the already verified artifact and real driver without granting
runner direct Broker egress. Root-owned control files and every parent directory must
not be caller-writable or symlinks. Host variables refer to:

```text
HEPTA_ROLLOUT_CAMPAIGN   root-owned absolute campaign JSON path
HEPTA_ROLLOUT_DRIVER     root-owned absolute installed driver path
HEPTA_ROLLOUT_STATE_ROOT absolute persistent runner-owned private state directory
HEPTA_IB_PAPER_HOST_PROBE_SHA256  installed probe digest
HEPTA_IB_BUILDER_IMAGE  admitted immutable builder image
```

The campaign fields are documented in the [harness contract](../technical/ib-paper-harness-contract.md).
Populate actual profile/account/host fingerprints from independently inspected host
state, not a caller's claimed profile. Record the executable, archive, driver and
portable harness digests. Compute the code/policy closure on the exact trusted checkout:

```bash
PYTHONPATH=scripts python3 -c 'from hepta_evidence_io import controller_digest; print(controller_digest())'
```

Install that manifest through the operator's protected deployment path. This revision
does not provide a command that creates trading credentials, opens sessions or disarms
the kill switch. Changing code/policy/profile/driver requires new admission, and cannot
be used to evade unresolved effects from a previous account campaign.

## Run and continue

Dispatch the same workflow from main with the **original** candidate SHA and numeric
artifact ID, `mutation_mode=true`, and `rollout_stage=canary`. After successful terminal
verification, a later dispatch may choose `pilot` or `extended` with the identical
artifact identity. The resolver admits the original successful owner/main build; it
does not require the candidate SHA to equal today's main SHA. The selected host always
rechecks its own isolation and root campaign before any possible order.

A persisted successful stage is reverified, not rerun. A new store executes the prior
stages sequentially. One environment approval covers that selected bounded campaign;
there are no repeated stage jobs or rebuilds. Unrelated main changes are harmless if
the pinned controller closure and all bound host inputs remain identical.

Inspect stored state without sending:

```bash
python3 scripts/hepta_paper_campaign.py \
  --store /absolute/path/to/campaign-store \
  --campaign /absolute/root-owned/campaign.json \
  --binary /absolute/verified/hepta-ib-executiond \
  --harness "$PWD/scripts/hepta_ib_paper_harness.py" --status
```

The status command reverifies completed receipts. It is not an authorization command.
The host entrypoint names stores by SHA-256 of the canonical binding under the configured
state root. Do not place them in disposable checkout or RUNNER_TEMP directories.

## Failure procedure

Stop adding risk. Keep the failed/running attempt, stable command IDs, driver calls,
original OMS and callbacks. A failed campaign cannot be retried by the workflow: its
active identity stays fenced. Use the existing runtime's command-status/reconciliation
and guarded exits under operator control; never guess the opposite side/quantity or
wipe the store and retry. Preserve the final recovery evidence even when the root
cause is only a lost workflow response.

Success and failure evidence are exported for Actions retention while host originals
remain. An abrupt runner loss may prevent upload; the on-host attempt is still the
recovery source. `campaign-exit.json` is wrapper diagnostics, not proof of flat state.
Missing uploads or no receipt must never be interpreted as a successful round trip.

## Heavy tests, maintenance and limits

`certify` additionally invokes the separately pinned twelve-scenario V5 qualifier;
its host variables `HEPTA_IB_PAPER_QUALIFIER` and corresponding SHA-256 are required
only for certification. Do not run it automatically for ordinary deployment retries.

Use `hepta_runtime_diagnostics.py --help` for bounded read-only journal capacity/replay
inspection. It does not rotate logs, clear campaign fences or authorize cleanup. True
cross-version persistent-schema rollback, continuous unattended operation, full margin/
Greeks-aware multi-asset IB trading and real account qualification remain separate
engineering/operational work, not consequences of these source tests.
