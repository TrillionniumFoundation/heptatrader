# Exact decision input attribution: facts, limits and runtime integration

## Historical records are not silently reconstructed

A real closed execution plus commission is an economic fact. It is not proof of
which model input produced the opening command. Price/time proximity, the same
confidence, a repeated BUY response, current history, and a reused broker order
number do not supply the missing link. Existing unbound episodes remain in
`execution_rewards` with explicit learning exclusions. No historical PnL is
rewritten and no missing model input is invented.

The inspected OpenClaw global store had no diagnostic event, capture session,
capture event or command-log payload carrying the actual request. The search of
post-period session/trajectory files did not find the literal trading request.
Further internal-cache inspection was blocked by the execution platform and was
not bypassed. This is a scoped search limitation, not proof that no copy exists
anywhere. Existing model-input hashes alone are not reversible input records.

## New producer: original callsite, not inferred matching

`heptatrader_attributed_driver.py` loads the exact PAPER base code pinned by its
release manifest. For BTC it composes the pinned economics adapter and existing
profile first.
The outer `Capture` is purely observational and makes no trading decision.

1. At the actual `infer model run` call, capture the literal CLI prompt, model
   selection and thinking controls, exact supplied quote-history snapshot,
   instrument, position and current period. Authentication headers, environment,
   token files, arbitrary process arguments and stderr are not captured.
2. Retain the original response envelope and the exact decoded decision returned
   by the unmodified model function. There must be one request, one successful
   response and the original decision value.
3. At the original successful durable PREPARED write, bind the native command ID
   to the *same returned Python object*. Equal copies and modified decisions do
   not qualify. No metadata is inserted into the strategy decision or state.
4. Append those observations to a private, hash-chained journal with bounded
   queue/storage. The trading thread never waits for the journal writer. Loss,
   overflow, malformed records or failed writes deny attribution, not risk exit.

`captured_at_ms` is the callsite observation and `written_at_ms` is actual journal
persistence. This does not falsely claim the asynchronous record was fsynced
before a submit. A PREPARED binding states that the original state write had
already succeeded. The request/response/decision/binding DAG order, original
command creation time and record hashes must all agree before credit is emitted.

The scope is the exact local infer CLI request and controls. Unobserved provider
internal system context, server-side routing or hidden model state is not claimed
to have been captured. The raw response can preserve any provider run identity
actually returned. The local request ID is not renamed as a provider-issued ID.

## Consumer, economics and local learning credit

Each producer has an immutable manifest containing the exact installed source
hashes, original base/profile hashes, period and output scope. The learning
configuration must explicitly register that manifest's hash. A journal header
claiming PAPER/source authority by itself is not sufficient.

The importer requires the exact request -> response -> decoded decision ->
original command graph. It independently checks the output-envelope relationship,
command direction/instrument, chronology, current period, complete chain, private
files and producer loss health. Conflicts remove that command's attribution;
there is no fallback to an earlier favorable match. Missing and malformed
attribution does not erase a separately valid financial observation.

After joining the registered exact binding to the accounting replay, a closed,
fee-complete and terminally reconciled episode may enter `rewards` as a
BEHAVIOR_BOUND_VALUE_TARGET_ONLY. The `input_attribution` field retains original
request/manifest/prompt identities and source-prefix witnesses. All actual fees,
negative returns and original execution IDs remain intact.

This is not a canonical Agentd owner acknowledgement, not Laya on-policy reward,
not a PPO behavior propensity, not an action-policy gradient and not evidence
that a financial critic has been trained. It supplies the previously missing
input attribution required before such independently specified learning work.

## Deployment and verification

For an already installed PAPER attribution producer,
`install_decision_attribution.py --instrument EUR.USD --inspect-only`
(or `BTC.USD`) checks the exact active unit command, private manifest,
period and immutable runtime hashes **without** contacting a broker or
restarting a service. Reinstalling **byte-identical** code on an active
attributed lane returns `ALREADY_CURRENT_NOOP` even when nonflat or under
an operator opening hold. The installation path continues to refuse an in-place
change to a differing runtime.

To prepare an upgrade for an existing producer, run:

```sh
python3 -B scripts/install_decision_attribution.py --instrument EUR.USD --prepare-upgrade
python3 -B scripts/install_decision_attribution.py --instrument BTC.USD --prepare-upgrade
```

Preparation reads the installed manifest, period, pinned runtime and BTC profile,
then builds a complete versioned candidate from the selected source checkout.
It preserves the existing producer configuration and binds the candidate's exact
runtime bytes. The recorded `source_sha` is the observed checkout HEAD;
`runtime_sha256` identifies the bytes actually copied, including any source edits
in that checkout. `runtime_bytes_changed` compares those bytes with the parent
manifest. The existing Boolean `trading_behavior_changed` cohort remains true
when already true, and changed bytes conservatively enter that cohort. Only an
explicit false parent with identical bytes remains false. This is an identity
classification; `behavior_equivalence_assessed=false` states that preparation
has not judged code equivalence or measured a trading improvement.

Preparation does not require a flat lane, a current quote, a cleared
opening hold, or unrelated files in the checkout to be clean. Repeating the
same preparation verifies and reuses the existing candidate.

Preparation does not contact the broker, read or alter driver state, write a
service configuration, or stop a process. Its output identifies a prepared
candidate; it is not a receipt that the running driver has changed. Activation
uses a separate owner-controlled continuity procedure supported by the installed
runtime. Inspection, preparation and an idempotent no-op do not grant new risk
or model promotion.

The first actual installation requires a clean committed source, PAPER mode
on port 4002, no operator reset hold, a flat/no-pending/unfenced target lane and an
authoritative read-only snapshot. It copies the currently executed base and BTC
profile byte-for-byte into an immutable release. Only the chosen PAPER driver is
stopped and restarted. A startup failure restores its prior unit; native
Execution/Gateway are never restarted. No canary or validation order is sent.

The observation launcher keeps HOLD, the historical 0.62 predicate, BTC fees and
cooldowns, cash sizing, fresh quotes, native risk, original-command recovery and
all risk exits unchanged. Restarts cannot claim a preexisting reconstructed
`last_decision` as an in-memory original object; the next actual inference creates
new exact provenance. Old pending commands must resolve under the original
runtime rather than being overwritten by installation.

Only the Laya trainer/reader needs new registration and code. The resident
installer's optional `--only trainer` updates that service without interrupting
feed or existing future evaluation. The scheduling ledger and candidates remain
in the same period directory, with their existing cadence and consumption state.

Tests include exact-body retention, no environment-secret collection, clone and
mutation rejection, lost journals, period/source/file conflicts, native pending
identity, unchanged state writes and the entire BTC economics invariant suite
with provenance outside the original adapter. Synthetic test outcomes are not
presented as real trading rewards. Live receipts distinguish INPUT_CAPTURED,
COMMAND_BOUND, CLOSED_AND_ATTRIBUTED and VALUE_MODEL_TRAINED; these stages are not
interchangeable.


Attribution source witnesses stop at the original COMMAND_BINDING byte offset.
Later prompts or responses do not extend that witness or create spurious reward
versions. Runtime releases and configurations are independently content-addressed,
so adding a verified FX producer to an already registered BTC producer never
requires overwriting a prior immutable configuration.
