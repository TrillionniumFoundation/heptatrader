# Pocket4 IB PAPER incident: read-only, fail-closed recovery gate
**2026-10-13 → 2026-10-14 (UTC+08:00). Release: HOLD; no automatic broker authority.**

## Incident and original four-window source experiment

The exact four pre-registered EUR/USD source-price windows (00:20, 03:35,
06:50, 10:05 on 2026-10-13 local) finished naturally. Each original entry
was irreversibly CENSORED due to `ORIGINAL_READINESS_HOLD_OR_STALE`;
all 16 fixed 15/30/60/120-minute maturities were likewise CENSORED
with `NO_ELIGIBLE_ORIGINAL_PREACTION`. **Zero price-labeled prospective
predictions, zero new experiment orders, no backfill.**

The original plan / predecision seal stayed byte-identical:

- Original plan SHA256:
  `02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04`
- Original seal SHA256:
  `1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b`
- Original final cohort SHA256:
  `857914d7222cc6e85058d9a19c3bf8765690e172a91a4de9cd2d22ebecc3890a`
- New independent all-four terminal recheck SHA256:
  `99e599a7f4c9836f8ebdbfeba5ffc5dccae4c61265b398845917de8af52be199`

Separate health custody captured **three** later exact readiness status
files, but all three had `READINESS_SNAPSHOT_STALE_OR_FUTURE`.
The first 00:20 complete monitor snapshot was never archived; later
healthy observations cannot reconstruct or rescue the original failure.
This is a source-readiness/age limitation, NOT evidence of model alpha.

## Live PAPER connectivity incident and carefully limited recovery

The existing PAPER Execution suffered repeated
`IB_PAPER_ADAPTER_CONNECT_FAILED` failures (over 470 systemd restart
attempts historically observed). Both the IB Gateway and its user
services were subsequently stopped/restarted by a separate recovery
path. After the manual `ibgateway.service` came up at 23:29, the
established API TCP connection was actually observed at
`127.0.0.1:4002`. Execution later re-entered `active/running`
at **23:39:29**, its original IB session snapshot showing 0 active
broker orders, **one existing broker position**, and coherent account,
positions, risk and cash snapshots. There is no authority to mutate
this position as part of a research audit.

Research did **not** restart either trading Driver or the execution
engine. The existing FX and BTC PAPER Drivers were observed running,
but process availability cannot alone prove new-order eligibility.
The native runtime observation still says `paper_authorized=false`;
do not reinterpret a connected socket as authorization.

The operator restarted only two pre-existing **read-only observers**:
`heptatrader-pnl.service` and subsequently
`heptatrader-account-nav.service`. PnL replay returned
`complete=true` with true fee accounting; the account observer still
reports `ACCOUNT_NAV_FX_RECONCILIATION_FAILED` and the all-in account
NAV/flows/taxes/financing/Flex statements remain incomplete.

## New executable, no-trade recovery gate

`research/train_v2/ib_paper_recovery_gate.py` is read-only. It performs
no broker API calls and **never starts/stops/restarts** any service.
Only the local read-only systemd status, immutable private PAPER period,
PnL snapshot, source-readiness JSON and bounded journal are inspected.
Every output is a previously nonexistent private 0600 fsync'ed JSON;
older failures are never rewritten.

A basic `NRestarts` and `active` check has a subtle false-positive:
manually stopping and starting the systemd service may reset NRestarts
to 0, even though the actual native connection has changed. New
identity checks pin all three properties across a **minimum real
five-minute observation interval**:

1. Actual nonzero `MainPID`;
2. Exact systemd 32-hex-character `InvocationID`;
3. The positive `ActiveEnterTimestampMonotonic` (microseconds).

Changing any identity, an unknown identity in an old witness, a missing
current field, a different restart counter, or a short interval is
a permanent recovery HOLD. The old `real-asof-*.json` audit files did
not record these fields; they are **rejected** for new trust rather than
silently upgraded.

Even a stable session can never authorize order placement or training
through this tool. It must independently prove fresh original IB quote,
>=20/24 usable readiness probes, source monitor age <=30s and
quote age <=5s, a complete true-cost account NAV and original native
Root/Owner/Witness/behavior provenance. A stale monitor source or a
signed-but-empty source is HOLD. The five-minute stability requirement
is necessary but NOT sufficient.

## Actual pocket4 read-only receipts

Original basic gate:
`~/.local/state/heptatrader/operator-evidence/ib-paper-recovery-live-20261013/real-asof-post-reconnect-2349.json`.

Original counter-only >5-minute gate:
`real-asof-stable-reconnected-20261013-2355.json` showed
zero restart count but still HOLD (unusable source and NAV/root gaps).

Enhanced gate:
`enhanced-legacy-baseline-rejection-20261013.json`
SHA256
`22275c27f0ce9f16f0fa489ae8ffc8134aa825477240fd8f74172223028`.
This explicitly returns `EXECUTION_PROCESS_CHANGED_SINCE_PRIOR_SAMPLE`
because the old unqualified witness lacks required invocation identity.

New enhanced initial baseline:
`enhanced-new-identity-baseline-20261013.json`
SHA256
`30acdd90b58f7a6830a8c1634e91134d782e8117b953fd77101d64a1e54c8aeb`.
Current observed identity was PID 59279, InvocationID
`b64a7cbe65d8494683af0e521eedfbda` and unchanged monotonic start.
A new second sample must be >=5 minutes later; it may prove
continuity **but cannot remove independent source / account HOLD**.

## Safety and test requirements

Synthetic and local read-only tests validate no-order/zero promotion,
stale original quote and age, paper period and source prefix identity,
journal failure censoring, changed start time/PID/InvocationID, old
witness with missing identity, restart counter reset after manual
restart and original cost/PnL separation.

No original pre-window research files, broker credentials, account IDs,
the native execution binary, the IB Gateway session or the FX/BTC
trading Drivers are edited by this audit.

**No production trading upgrade.** The next actionable tasks are:
fully witnessed IB PAPER recovery and account reconciliation, source
cache sustained >=20/24 evidence and independently completed original
model/root/owner ledger acceptance. Keep unsuccessful evidence intact.
