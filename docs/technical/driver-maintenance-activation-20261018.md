# Activate a prepared driver under the existing maintenance hold

## Why this path exists

A prepared attribution release previously could not replace either resident
driver: the continuity shield refused ordinary manual stop, while maintenance
checks reused mutation readiness and quote/lease conditions needed for trading.
That made an empty, deliberately held PAPER owner depend on reopening the risk
path or replacing the native execution runtime first.

This command provides a single-lane maintenance transaction using the existing
operator lock, independent native kill marker and durable maintenance hold.
It does not introduce a new permit file or require a new quote, decision lease,
owner-fence release, or native service restart.

## Command

Prepare a release with the existing installer first. Use its exact manifest path
and SHA-256 and the built owner-audit client:

~~~sh
python3 -B scripts/activate_prepared_driver.py \
  --instrument EUR.USD \
  --candidate-manifest /absolute/prepared/EUR.USD/manifest.json \
  --candidate-sha256 MANIFEST_SHA256 \
  --sessionctl /absolute/build/bin/Release/hepta-sessionctl \
  --sessionctl-sha256 CLIENT_SHA256
~~~

BTC uses the same entry point with BTC.USD. The command checks the actual process
argv and immutable runtime members; a matching next-start unit definition alone
is not activation evidence. Repeating an already active exact candidate returns
ALREADY_ACTIVE without another audit or restart.

## Maintenance transaction

The whole operation holds the existing operator-maintenance.lock. It verifies
the installed and prepared release binding, selected process identity, existing
nine stop shields, persistent hold and independent native kill marker. Other
risk units keep their identities and stop policy throughout the transaction.

The new hepta-sessionctl owner-audit command loads an existing private token and
existing encrypted catalog without creating or rewriting either. It derives the
owner from that record, discovers the native service identity through the
existing authenticated Unix protocol and issues only AuditSessionOwner (op15).
Expired or fenced records remain readable for this audit; the command cannot
renew them, release a fence, or grant a decision lease.

The controller consumes an Accepted, authoritative and complete native result
with zero active/uncertain commands, zero active broker orders, finite zero
position and gross position, a completed recovery barrier and no pending
post-fill reconciliation. This version intentionally covers maintenance while
the account is flat. It does not generalize a lane-specific Accepted result into
proof that unrelated account exposure is zero.

Once this fresh proof exists, only the selected driver's existing stop-shield
drop-in temporarily permits normal systemctl stop. The controller confirms the
old process has ended, takes the existing driver.lock, changes only the selected
ExecStart drop-in, restores and verifies the stop shield, releases driver.lock,
and starts the candidate. No manual process signals or dependency stops are
used. The independent native kill marker remains engaged.

An unsuccessful native recovery audit can request the adapter's normal broker
connection recovery. The command waits on newer same-service runtime journal
observations before another fresh audit; it does not repeatedly trigger op15
during that recovery. The journal observation never authorizes stopping.

## Failure recovery and state continuity

Every drop-in replacement compares the expected existing bytes and identity
under the operator lock. Restoration inspects actual bytes even if a rename
succeeded before a later fsync or daemon-reload error. Another writer's third
version is not overwritten.

A failed stop leaves the original process running with its stop shield restored.
A stopped or failed-to-start candidate is restored to the original configuration
before starting the old release. If the candidate is already running, rollback
must obtain another fresh native audit and use the same normal stop sequence.
An unknown replacement process is not stopped.

The stopped state file is preserved byte for byte while driver.lock is held.
After startup, transaction counts, P&L and existing entry/order associations must
remain intact. The rolling market-price history and normal decision/lease
timestamps may advance. Existing BTC historical entry correlations and
reservations are not mistaken for an open position or erased.

## Verified native compatibility on Pocket4

The resident execution binary is R14, built from c269bc558aa07054d3bf347d69bf4a90e647bf70.
Its existing op15 already supplies the causal recovery barrier. Place/flatten
dispatch and owner audit share the policy lock; final broker sends recheck the
native risk block. Broker mutation generation invalidates earlier barriers,
accepted orders become visible before callbacks, and reduce-only final checks
bind the current position and connection generation.

On 2026-10-18, an initial audit correctly reported a required new connection
epoch. Normal adapter recovery completed at broker epoch 3. Fresh FX and BTC
audits then returned SESSION_FX_OWNER_ZERO_CONFIRMED and
SESSION_CRYPTO_OWNER_ZERO_CONFIRMED with all zero/complete fields. FX bid/ask
freshness was unavailable at the time; it was not used as a maintenance gate.
Native execution/gateway processes, hold, kill marker and period stayed intact.

Targeted C++ tests exercise the real encrypted catalog, private token files,
Unix service and wire codec. They cover missing-store noncreation, shared cleanup
lock coexistence, expired/fence-pending owner audit, pre-RPC scope rejection,
incomplete/wrong-owner native replies and no credential/catalog rewriting.
Python transaction tests exercise stop/start failures, post-rename persistence
errors, lock ordering, candidate rollback and concurrent identity changes.

## Actual activation and training continuation

The Pocket4 activation and configuration continuation were completed on 2026-10-18.
The final independent process snapshot was taken at 2026-10-18T04:40:06.252000+00:00.

| Lane | Previous PID | Actual PID | Actual manifest SHA-256 |
| --- | --- | --- | --- |
| EUR.USD | 4140608 | 3356965 | aa01e8a3cd6fac8fa0fa447199caaed31467ba94059edba5b94f05ce4c400df3 |
| BTC.USD | 4123557 | 3359204 | 30c6566d9250c21a3c04ed999016e77191d98fd2ed8358d096ce3b899a8234b4 |

Each lane consumed its own fresh native zero-owner audit, completed the normal
stop/start sequence, and observed three distinct state versions from the new
process. Both returned ACTIVATED. Their actual base driver SHA-256 is
16504eaef4d539bb612175b6e9a6783b8015520d3978477a510aa5f645300ab7.
The controller SHA-256 is 71e8aabee12080dc601231a0f54ccf18d716c174734fa70cc6077bf1f2f28c55.

All nine stop shields were effective after both activations. Native execution,
gateway and evaluator retained their actual process identities. The kill marker,
durable maintenance hold and P&L period retained their identities. FX entries/exits
remained 357/357 and BTC 4/4, with zero position and no pending command. Full business
continuity validation passed; no transaction or historical entry association was reset.

The trainer then used its supported SIGUSR1 drain and normal service start to load
an append-only registration configuration. Existing registrations were retained:
EUR.USD increased from 3 to 4 and BTC.USD from 1 to 2. Actual trainer PID is 3365362.
All 64 resident source pins, policy effective time and runtime directory were preserved.
At that cutover, 41 existing plans, 64 recorded outcomes for the original trial and
47 existing job records were unchanged; the evaluation-trial counter stayed 41.
A normal reward sync after the new process started returned ATTRIBUTED_SETTLED_REWARDS_READY.

By the final snapshot, 46 training jobs were complete, with 0 deferred candidates. The calendar queue fix
had already allowed the previously blocked training sequence to advance. Original
trial evaluation remains attached to its original plan and deadline. New producer
registration does not require a new trade or immediate reward-count increase.

Private host receipts are under
/home/alex/.local/state/heptatrader/operator-evidence/driver-handoff-20261018/:
EUR.USD-driver-maintenance-activation.json,
BTC.USD-driver-maintenance-activation.json,
trainer-driver-attribution-deployment.json and
driver-activation-and-training-final.json.

## Integration regression closed before merge

Main change #430 added entry-guard observations to the base driver while two
source consumers still pinned the preceding bytes. Core CI run 39092812855
reported 66 errors: 50 BTC loader identity failures, three BTC prepare failures,
and 13 cost-adapter loader failures. Both fixed source pins now name the reviewed
base SHA-256 12fb61ad9355737379e8a8908c3ee7861e726d6787d894a9c7a6cd4afdff7ebf.
Only those two constants changed. Strict byte validation and the existing tamper
rejection test remain in place; immutable installed releases were not rewritten.

Validation completed on Pocket4: 11 maintenance transaction fault tests, both
native owner-audit/catalog suites, and all 128 tests in the four affected BTC,
attribution, prepared-upgrade and cost-adapter modules passed. Hosted CI status
is reported separately from these completed host tests.
