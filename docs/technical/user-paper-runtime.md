# Single-operator user PAPER runtime

Status: CURRENT
Scope: one non-root Linux operator; IB PAPER only, not external qualification.

## Paths and ownership

The user profile uses the same Gateway, Execution, journal, native client and
IB adapter as the system deployment. `HEPTA_IB_PAPER_USER_SCOPE=1` is explicit;
the system profile remains the default. It does not turn a denied system
credential into a user credential or select a testing seam.

Installed programs reside below `~/.local/share/heptatrader/releases/`; `current`
selects the stopped-state deployment. Configuration and credentials belong in
`~/.config/heptatrader`, durable journals/leases in `~/.local/state/heptatrader`,
and sockets/control in `/run/user/<uid>/heptatrader`. The user service assets are
installed as data under `share/heptatrader/user-systemd`; link those into
`~/.config/systemd/user`. The matching user tmpfiles configuration goes under
`~/.config/user-tmpfiles.d/heptatrader.conf`. No system unit, polkit rule or sudo
whitelist is part of this profile. Updates must stop the service before replacing
its current program selection and must preserve journal/lease state.

## Explicit trust boundary

Gateway, Execution and client share the operator UID. Socket permissions and the
private runtime directory exclude other users, but this is not isolation against
malicious same-UID programs or mutually untrusted Agents. Such deployments must
retain the separate-UID system profile. The optional model entry disables shell,
unified execution, apps and automatic dependency installation; it exposes only
the existing MCP process, not a Broker SDK or configuration-writing tool.

The user kill-switch reader pins the actual operator-owned directory and checks
identity, mode, group and links with the same fail-closed observation algorithm.
It does not call the testing opener. Directory replacement/uncertainty remains
sticky. The existing lease cleanup interlock uses one owner-checked lock in the
private user runtime; its flock and store ownership checks are unchanged.

## Profile and operational bounds

Only a non-root operator, a canonical DU PAPER account and loopback PAPER port
are accepted. The configured contract set contains one CASH contract. Quantity
and gross-position ceilings are at most 25,000, order notional at most 35,000,
active orders exactly one, rate at most two sends/minute and quote age at most
5,000 ms. A user profile cannot select the expanded external qualification mode.
The credential uses a separate `PAPER-USER-V1` prefix and binds custody, operator
UID, contract, quote age and existing profile limits. The native
`hepta-ib-profile --env <private-config>` utility uses the actual policy parser
and serializer; it does not open a Broker connection or issue an attestation.

Bootstrap cash baselines must come from a completed actual account observation,
not invented zero balances. A baseline is not a transfer or a reset of existing
account cash. Existing live state must not be overwritten to make startup pass.
The user is responsible for keeping the selected account free of conflicting
external operations; drift continues to fail the existing reconciliation checks.

The execution-owned HFXR1 restart checkpoint is advanced only after a durable
post-checkpoint fill chain proves that the Broker changed CASH exposure after the
last persisted observation. Recovery binds the durable native send to the local
Broker acceptance and terminal cumulative execDetails using owner scope,
instrument, venue correlation when present, Execution service epoch and Broker
connection epoch. Numeric IB order IDs are explicitly not identities: they may be
reused after reconnect, so pre-checkpoint acceptances and later execution replays
from another service epoch cannot advance the checkpoint. The derived expectation
is still checked against the fresh Broker account attestation before Execution
becomes ready. Ambiguous, partial-only, over-limit or mismatched evidence fails
closed; startup never edits a balance merely to satisfy attestation.

## Startup diagnostic holds

An unscoped IB error 2172 is an advance client-upgrade advisory, not an order
rejection. Its original callback remains in observability and OMS. Only an
unscoped event without account, order reference or advanced rejection evidence
is excluded from admission blocking; scoped, unknown, connection and actual
order errors remain fail-closed.

A real `IB_FX_CASH_ATTESTED_BALANCE_MISMATCH` is not repaired by changing cash,
loosening tolerance or accepting a stale campaign position. In the user PAPER
profile, a connected authoritative event stream with same-epoch completed
position, active-order and terminal downloads may retain a **diagnostic-only**
service. Health reports `startup_recovery_read_only=true`,
`mutation_ready=false` and the original cash-mismatch reason. Place, flatten and
cancel all stay blocked; cash/campaign snapshots remain non-authoritative and
no HFXR1 checkpoint, owner settlement or driver pending state is rewritten.
`risk.fx_cash_attestation_mismatch` logs the expected and observed balance,
difference, currency, connection epoch and account generation.

The hold is latched for the process lifetime. Later callbacks cannot silently
promote it to trading readiness. Missing/conflicting downloads, disconnected
streams and the external qualification profile do not use this fallback. A new
startup must pass the existing complete reconciliation contract before trading
can resume. An available socket or locally accepted command is never a fill.

### Control availability while awaiting the first quote

A normal user-scoped PAPER runtime may finish startup or reconnect once the
current Broker account, positions, FX cash, active/terminal orders and coherent
recovery barrier are complete, and the current primary market-data request has
actually been sent. The first BID/ASK callback is not a prerequisite for serving
recovery and read queries. A missing primary quote therefore does not, by itself,
restart a fully synchronized control service during a closed session or source
outage. Current-epoch 10197 and other existing fatal admission failures remain
fatal; missing or inconsistent Broker evidence still fails the original barrier.

Control availability does not mark prices ready. `market.get_quote` continues to
report unavailable/stale data, and every new order still passes both policy quote
validation and the final native send check against the current subscription and
original quote expiry. New-epoch recovery invalidates old quote bindings even
while their previous TTL has not expired. Canonical qualification and
observation-only profiles retain their existing quote-required startup behavior.

## Normal commands and model selection

The `heptatrader-user` command provides `start`, `restart`, `stop`, `status` and
`logs` through the user manager. `native` and `session` forward to the existing
CLIs with user paths; original command IDs and outcome semantics are unchanged.
`session owner-audit --token-file PATH` preserves that exact top-level native
mode without prepending a Supervisor socket. It uses its existing Execution
runtime configuration; ordinary session commands retain the Supervisor endpoint.
An immutable operator release may declare `native_clients` in its adjacent
`MANIFEST.json`: exactly `heptactl`, `hepta-sessionctl` and
`hepta-paper-command`, each with an absolute path and SHA-256. `native`,
`session` and the explicit hold-resume health reads then execute the verified
open client image. Their existing socket, owner token, arguments and deadlines
are retained. This does not change the global `current` release or native units.
The manifest also pins the local helper sources and the original configured
`native_startup_guard` path and bytes. A prepared copy fixes its required flags
and helper SHA in the generated wrapper/helper/launcher bytes; missing declared
assets are errors. Its file manifest records those generated hashes and source
parameters. Generic copies without a declaration retain the legacy paths.
An operator-local `hepta-user-mcp` and `hepta_mcp_server.py` similarly keep `ask`
on that release's adapter; a prepared copy refuses missing local assets.
The runtime-directory unit creates an engaged kill switch on a fresh runtime.
A deliberate local operator action is needed to allow new risk; no password or
root identity is required. An ordinary restart must not be presented as a fill,
a terminal reconciliation, or external qualification.

`ask` defaults to `gpt-6-luna`, or the explicit model ID in the user's
`~/.config/heptatrader/model`. It uses a separately installed official Codex CLI
and existing local authentication, not copied keys in the repository. Missing
CLI, model access or authentication is an error, never a silent model fallback.
It starts one prompted interaction, not an unspecified perpetual trading loop.
The model may propose an order only on explicit user instruction; the native
preview, final risk, durable identity and uncertain-send rules still decide it.

The profile is owner-operated PAPER development. Its installation, account
connectivity or model availability is not hosted CI, an external qualification
receipt, a profitable strategy, or LIVE authority.

`pause` engages only the user-owned new-risk guard; it does not cancel existing
orders or assert flatness. Ordinary `paper-on` releases only the CLI-tagged
pause while Execution and Gateway are active. It preserves an independent empty
native startup hold, including when a resident Driver calls it automatically.

After the operator has chosen to resume the existing PAPER loop, use
`heptatrader paper-on --resume-native-hold --check` for a read-only precheck, then
`heptatrader paper-on --resume-native-hold` to release that exact empty hold.
The precheck leaves the marker in place and cannot prove the native branch that
runs only after its removal. The real command therefore re-reads both owners'
health under the same lock after unlink/fsync, requires the original native
instance to observe its guard clear, and restores a hold on any failed readback.
Success reports the observed guard state and each owner's current mutation
readiness; another in-flight request is not mistaken for a stuck hold.
The explicit path reads both existing paper and crypto owners' native health,
orders and positions; it rejects a durable maintenance hold, an independent native
blocker, changed process identity or replaced marker. It does not require flat
positions or confer service-stop permission. Per-order preview, quote, identity
and risk checks remain mandatory.

The native unit's loaded `ExecStartPre` must run
`/usr/bin/python3 -B /absolute/path/to/hepta_maintenance_latch.py native-startup-hold`
from the same versioned directory as the public wrapper's helper, or the exact
original path and SHA pinned by its operator manifest, with `Type=simple` and
`KillMode=control-group`. The configured interpreter, full startup arguments,
single-command boundary and original helper bytes remain checked. A client-only
operator update leaves that startup helper and its loaded unit unchanged.
Startup and explicit recovery share
the existing operator lock. Startup creates an empty `0440` marker when missing
and preserves an existing marker, without interpreting a persistent maintenance
record. Migrating an old uncoordinated startup hook requires manager reload and
verification of unchanged running service identities; it does not require
restarting native Execution. A subsequent restart engages the hold again.
Neither command changes the system profile.

The user tmpfiles configuration likewise creates an empty `0440` independent
startup hold on a fresh runtime. Ordinary `paper-on` cannot remove it, and an
explicit `--resume-native-hold --check` preserves it. Updating or reinstalling
this configuration does not rewrite an existing marker, including an old
unrecognized payload; neither tmpfiles nor startup silently converts or releases
that protection.

The runtime-directory preparation unit has no default dependency on basic.target;
it runs before sockets.target to avoid a socket/oneshot/basic ordering cycle.

## Resident autonomous PAPER driver

The user-owned runtime may enable `heptatrader-paper-driver.service`. It restores
only the behavior loop from the historical local AI PAPER agent: 10-second quote
sampling, 120-second `gpt-6-luna` direction decisions, 0.62 reversal threshold,
EUR.USD MKT entries dynamically sized to at most USD100,000 reference notional, 0.2% adverse-move reduce-only exits, and continuous
restart recovery. The model process has no HeptaTrader MCP server and cannot send
orders; it emits only BUY/SELL/HOLD JSON. All mutations use the existing
`hepta-paper-command` / `NativeStrategyClient` / HSR1 / Tool Gateway / Execution
path.

Before the only send attempt, the driver durably records `POSSIBLY_SENT`. A crash,
timeout or restart after that boundary performs only `execution.get_command_status`
for the original command and authoritative Broker reconciliation; it never obtains
a replacement preview or sends under a new command ID. Active Broker orders block
new decisions. Existing exposure can only be reduced by authoritative flatten.
Session renewal is generational and requires a non-fencing `AuditSessionOwner`
using complete Execution-owned Broker snapshots. When renewal is due, the driver
first blocks new entries, drains exposure through the existing reduce-only path,
and reconciles the original pending command. Routine renewal must neither install
a recovery-only fence nor release an existing owner fence.

### Last actual entry-guard observation

Before an ordinary resident OPEN, the driver records the result of its existing
`paper-on` call as `state.json:last_entry_guard`. The record contains
`checked_at_ms`, `returncode`, bounded `status` and `reason`,
`decision_action`, and `scope=LAST_ACTUAL_ENTRY_GUARD_CHECK`. Structured CLI
reasons are retained; malformed output has a bounded diagnostic fallback, and
a timeout or process-start error is recorded as `CALL_ERROR`.

A later HOLD or SELL decision may clear the ordinary `last_error` field while
retaining this observation and its original timestamp. Only a later actual
entry-guard call replaces it. Interpret it as the result at that timestamp;
current readiness still requires fresh native health, quotes and the original
entry checks. Recording the observation adds no extra `paper-on` call and
keeps the existing return-code gate, send identity, close path and maintenance
holds unchanged.

## Audited owner-fence recovery

`SESSION_OWNER_FENCED` and uncertain renewal persist an independent recovery latch
before any further entry attempt. Health is read before quote sampling and exposes
`execution_authoritative_health.owner_fenced`, `owner_recovery_only`,
`mutation_ready` and `mutation_reason` for the authenticated owner. A successful
socket/read probe is not mutation readiness; the final quote/risk/permit preview
remains required. Transient quote errors cannot erase the persistent blocker.

Recovery uses the existing user-custodian `hepta-sessionctl paper-resume` operation
and the existing session token/generation. It never re-provisions a session or
rotates tokens to evade a fence. The native path holds the existing mutation
serialization boundary, audits complete active/terminal/risk/position evidence,
reconciles original command and order owners, and invokes the existing
`ReleaseSessionOwnerFence` / `AuditAndReleaseSessionOwnerFence`. The OMS release
record is durable before the fence is removed. Incomplete state, exposure, an
unresolved command or journal failure cannot be resumed. A revoked, recovery-only
bearer or externally finalized owner cannot be revived. A still-active user
lease may carry a residual Execution recovery marker from a failed older renewal;
that marker is resumable only under the full audit, bound to its exact ingress
generation. One durable release record covers both owner gates, and replay
retains the consumed ingress high-water mark so an old recovery request cannot
reinstall it. Terminal fences remain irreversible. A positive generation mismatch only identifies the next existing
session generation; it is not proof of recovery.

The driver remains halted after a successful resume reply until a fresh owner
health read and an actual `risk.preview_order` on the same Execution identity
succeed. The verification permit is discarded without order submission. Only
halts owned by this recovery/renewal are cleared; partial-fill safety halts remain.
A lost reply or restart repeats audit/status recovery, never a strategy send.
`--recover-owner` performs this bounded operator check without running the model
or submitting an order. The former direct `--rearm-session-generation` path is
rejected; flags alone cannot establish an Execution release.

The user Execution service backs off for 30 seconds after startup failure so an
upstream market-data/session fault remains fail-closed without hammering IB Gateway.


### Reboot and Execution epoch recovery

A cold user-session boot uses a separate 10-second initial IB API-listener
connect budget. Failure remains fail-closed and returns to the service manager;
the 180-second reconnect budget is retained for a transport that connected and
is waiting for IB upstream/farm recovery or authoritative snapshot rebuilding.
This prevents an unavailable local IB listener from consuming the full long
recovery budget on every service attempt.

Gateway validates the Execution identity on both mutation and event sockets
before every remote operation. When a daemon restart leaves only the mutation
client's cached identity stale, Gateway invalidates that observation and
re-reads the pair once before dispatch. Because this happens before mutation
I/O, it cannot duplicate an order. A persistently split pair still fails
closed. Event cursors keep their existing authoritative-resync boundary: the
new identity publishes system.execution_service_identity_changed and the
owner must reconcile before upstream events continue.

## Reused Broker integers and pending reconciliation

Pending reconciliation inspects only the original HSR1 command. It accepts a
recent fill only when the authoritative response names that command and the row
agrees on the original command, Broker order number, durable venue correlation,
direction and unambiguous execution evidence. In particular, an old BUY with the
same numeric order ID cannot supply the entry price or terminal status of a new
SELL. A terminal event for another command leaves the current pending record
intact; no new preview or resubmission is created.

Opening position adoption and realized-PnL settlement require that matched fill
evidence. Missing or ambiguous evidence remains pending with a diagnostic rather
than being labeled flat, filled, or profitable from the cached driver position.
Physical Broker position/active-order snapshots remain authoritative for exposure.

### Cancelled pending request after reconnect

The original HSR1 request is inspected, never re-submitted to resolve a cancelled
order. A zero-fill terminal row must bind its original command/correlation and
direction and contain no ambiguous or economic execution evidence. Pending may
be cleared only after active orders are complete/empty and the authoritative
position has not changed from the already-accounted position. Entry/exit counts
and realized PnL are unchanged for that cancelled attempt. A nonzero unexpected
position or partial fill must remain under reconciliation, not be called flat.


### Synchronous non-send versus uncertain send

The resident driver distinguishes the process transport from the typed tool
outcome. Before any submit it durably writes `PREPARED` and then `POSSIBLY_SENT`.
A missing response or typed `uncertain` retains that state and the original HSR1
identity indefinitely. A decoded synchronous rejection is instead persisted as
`DEFINITELY_REJECTED` with the service reason. The driver does not immediately
assume flatness: it clears that pending record only after the next authoritative
snapshot proves no active order and an unchanged position relative to
`position_before_send`. Counters, entry basis and realized PnL are not modified.
A later retry, if policy still requires one, begins with a fresh quote and preview;
the rejected HSR1 request is never submitted again.

If the process dies after receiving a rejection but before persisting the local
classification, fail-closed `POSSIBLY_SENT` remains correct. Incident recovery
must verify the append-only Gateway audit chain and the exact command's durable
rejected outcome, confirm that Execution has no command, and recheck authoritative
orders/position before annotating the local state. A missing Execution command by
itself is not proof of non-send.

## Isolated weekend crypto PAPER execution

The same installed native stack can opt into BTC/PAXOS while keeping the existing
EUR/USD CASH campaign, cash baseline, journal and open position. `heptatrader-paper-driver.service`
continues the EUR strategy; `heptatrader-crypto-driver.service` runs the same driver
with `--instrument BTC.USD`. The latter uses `crypto.token` and `crypto-driver/`
state/outbox, not the existing FX token or `paper-driver/`. Cross-instrument state
files are rejected. Both use the existing Execution process, original-command
HSR1 persistence and authoritative Broker snapshots. The global active-order
bound remains one, so concurrent strategies cannot independently bypass it.

The crypto model uses the existing asynchronous gpt-6-luna direction-only flow.
BUY may open a bounded long only from a flat BTC position; SELL can trigger an
existing long's exact reduce-only exit, never a naked short. Each fresh BTC
opening uses MKT/IOC with an exact USD 25,000 cash quantity. A 1 BTC protection
ceiling prevents silent downsizing or excessive base exposure at abnormal prices.
Partial IOC fills are matched against the original command/correlation and exact
position change before adoption or PnL settlement. Partial exits accumulate their
realized portion once; only a fully reconciled flat position clears a partial-fill
halt owned by that cycle. Other recovery or operator halts remain intact.

Crypto state identifies USD `BROKER_FILL_GROSS` PnL. Until an independent complete
fee ledger is available, `fees_complete=false` and `net_pnl=null`; simulated
Broker execution prices are not fee-inclusive profitability evidence. The FX
realized PnL is not carried over into the crypto ledger.

`--instrument BTC.USD --canary-roundtrip` runs a bounded native entry/exit
qualification, without calling the model or sending through another Broker
client. The user PAPER crypto adapter permits only BTC/PAXOS/USD BUY MKT/IOC
cash sizing, so this canary uses that same production envelope with a separate
qualification cap of at most USD 150 and a 0.0005 BTC reference-size ceiling;
post-fill evidence must remain at or below 0.001 BTC / USD 150 and any violation
is flattened before the canary fails. The resident crypto path keeps its exact
USD 25,000 default and is not resized by this qualification limit. The canary
first proves its BTC position flat and global active orders empty, persists the
original request, submits once, reconciles the economic fill, and flattens through
the same native permit path. A bare prequalification halt may be cleared only
after this flat round trip; risk, settlement, owner-fence and uncertain-lease
halts remain non-bypassable. Unknown outcomes remain pending; no fresh command
is manufactured to replace them. An IOC non-fill is not a successful trading
qualification.

A missing weekend FX quote does not stop crypto quote processing or force FX
flattening. Quote-free position checks and held-position lease continuation keep
the original FX state managed until its market reopens. A failed mixed deployment
can switch the version/configuration back only after accounting for all crypto
pending/positions; it must not hide inventory from the old FX-only configuration.


## Partial-fill accounting while FX cash is fenced

A Broker execution ID identifies one execution, not the entire order. The
legacy OMS `qty`/`price` on `execDetails` remain cumulative quantity and
cumulative average price. Multiple distinct IDs on the same original
command/correlation/client binding are not inherently ambiguous. The native
recent-order projection retains a bounded set of IDs, chooses the greatest
coherent cumulative observation and never sums cumulative quantities.
Duplicate IDs are inert; conflicting replays, corrections, pending price
revisions, non-finite values, incompatible terminal totals, binding conflicts
and capacity exhaustion remain non-settleable. Local Accepted and owner
terminalization cannot erase executions or relabel a partial IOC cancellation
as a full fill. Version-2 read-model economic evidence carries completeness,
all execution IDs and the original receipt time.

Individual execution shares/prices, permanent order and contract IDs and
commission reports are also retained additively in native OMS. This reuses the
committed callback economics work from `740136c`; missing legacy fee fields
remain unknown and do not become zero fees or net PnL.

A cash-attestation discrepancy still blocks place/flatten/cancel and cash
checkpoint advancement for the entire process. `portfolio.list_positions`
remains non-authoritative for campaign risk. In user PAPER crypto scope it can
add a separately labelled `settlement_observation`: physical crypto positions
from a complete same-epoch position/open-order/terminal snapshot, with no active
orders, terminal exposure exactly caught up to the current exposure generation,
no callback conflict, exact account binding and `mutation_authorized=false`.
The coherent account-risk refresh may remain deliberately unabsorbed because
the FX cash attestation failed; that lag is not promoted or rewritten by this
read-only path. A risk-absorbed generation ahead of current exposure is rejected.
This is not FX cash exposure, does not advance the recovery barrier and does not
release any trading gate.

Only the explicit driver `--instrument BTC.USD --reconcile-only` operator mode
may consume that observation. The ordinary resident loop cannot. It requires
the original `.hsr` command status, exactly bound native cumulative execution
and terminal evidence, matching current physical position delta, and matching
service/connection/generation/account metadata. It updates entry accounting and
an atomic `last_reconciliation` receipt once, preserves gross realized PnL on
entry, retains all execution IDs, uses the original fill receipt time, and sets
an owned settlement hold. It neither sends nor creates a replacement command.
A still-pending result exits nonzero. A second reconciliation with no pending
cannot silently overwrite a mismatched position. Resident trading requires an
explicit later full-risk reconciliation to remove the owned hold.

The FX cash discrepancy must be explained with actual dated, currency-specific
cash transactions / statement evidence (cash report, interest, fees, transfers,
and fills as applicable). Do not change HFXR1 expected cash, baseline or the
campaign position merely to match a new balance. Accrued interest, a plausible
calendar date, a current account snapshot, or empty historical execution queries
are not evidence of a specific posted adjustment. Trading readiness remains
false until this financial reconciliation is complete.

Regression coverage: cumulative ID/adversarial tests, physical-only observation
barriers, the production recent-order projector linked directly into an offline
replay test (never calls Start or opens Broker connections), and driver tests
for exact original-command one-shot accounting and continued trading fences.
The production replay test can additionally read a private immutable OMS copy:

```sh
build/tests/hepta_ib_recent_order_replay_tests \
  /absolute/private/oms-copy.jsonl ORIGINAL_COMMAND_ID
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/python -p test_paper_driver.py -v
```

The settlement snapshot and the subsequent original-command inspection are
separate IPC reads. Their nonempty service epoch and strictly positive integer
fencing generation must match before accounting can consume the fill. A
restart, missing identity or boolean/invalid generation preserves the original
pending command and reports `ORIGINAL_COMMAND_SNAPSHOT_MISMATCH`; local
acceptance from a newer service cannot validate an older physical snapshot.
This check does not release the account cash hold or authorize any Broker send.

A failed initial FX cash attestation retains a separate, non-authoritative copy
of that completed account download solely to recognize exact duplicate
CashBalance pushes. Such duplicates preserve the rejected-cash reason and the
independently completed physical crypto observation; they do not create cash
exposure or advance the attestation. Any changed balance (including a return
to the old expected value), malformed value, AccountReady=false, disconnect,
new account refresh or other risk invalidation discards this duplicate-only
memory. Delayed old pushes cannot recreate position authority after invalidation.


## Current PnL: explicit-period, read-only reporting

`heptatrader pnl` is the canonical current PnL display. It reads existing native
health, owner-scoped orders, positions and quotes plus the existing durable OMS;
it does not submit, cancel, reconcile or edit a trading driver. `heptatrader
status` shows the observer's cached view, with expiration enforced. The legacy
`state.json: gross_pnl` remains a compatibility **realized-gross-only** counter,
never a current total, net return, account NAV or Broker daily PnL.

The independent producer is `scripts/hepta_pnl_report.py`, sharing pure Decimal
accounting in `scripts/hepta_pnl_accounting.py`. The optional user unit
`heptatrader-pnl.service` writes only `~/.local/state/heptatrader/pnl/`; it cannot
write the trading ledger/driver files, uses only AF_UNIX, and has no Wants or
Requires dependencies capable of starting trading services. Installing the unit
does not enable it. Both scripts are ordinary runtime package helpers.

With a matching newly built `heptactl` under `--prefix`, the reporter's
`--watch --persistent-native-reader` option keeps one read-only CLI process per
owner. Its existing NativeToolClient reuses verified schema discovery while
each read still opens its normal bounded socket request and passes native
authorization and schema checks. The local `heptactl pnl-read-stream` mode accepts
only health, orders, positions, and EURUSD/BTC quote reads; it cannot submit a
mutation. All seven reads per instrument and the before/after snapshot checks
remain in place. A failed response or timeout discards that owned reader without
retrying the read. The option is explicit so updating the Python helper alone
continues to work with older installed CLIs.

Attach this reporter to an **already completed, user-authorized reset**, once:

```sh
python3 -B scripts/hepta_pnl_report.py --init-from-reset /absolute/private/reset-receipts --broker-client-id EXISTING_CLIENT_ID
heptatrader pnl
heptatrader pnl --format json
heptatrader pnl --cached
```

The initializer verifies opening Broker, fresh opening quote and reset receipts,
current owner bindings and matching driver period IDs before exclusively creating
`~/.config/heptatrader/pnl-period.json`. It cannot overwrite an existing period,
reset Broker cash, rewrite the FX baseline, clear pending, restore deleted old
history or restart services. Carried inventory starts at the evidenced reset
midpoint; original purchase and Broker average cost remain separate historical
references. Original send timestamps, not late historical callback receipt
timestamps, assign trades to the period. Manual cash normalization, foreign
clients, prior-period transactions and their fees are excluded. There is no
silent calendar rollover: the explicit reset period can span midnight.

Version-1 JSON uses decimal strings and `null` for unknown amounts. Each
instrument and the aggregate expose `realized_gross_pnl`,
`unrealized_gross_pnl`, `gross_total_pnl`, `fees_paid`, `fees_complete`,
`realized_net_pnl`, `unrealized_net_pnl` and `net_total_pnl`. Longs mark at the
fresh IB bid, shorts at the ask; true authoritative flatness needs no mark.
Missing/stale/future/crossed/wrong-epoch quotes, unproven empty position lists,
position/ledger mismatches and changing observation generations cannot publish
an apparently complete current total. Journal/period changes invalidate cache
immediately, even within quote TTL. Failure replaces, rather than silently
retains, the last good observer snapshot. Aggregate values require every
component; a partial sum never appears as account total.

Executions bind to the original command/correlation/account/client/contract.
Distinct execution IDs are deduplicated, individual shares/prices are preferred,
and legacy cumulative observations are differenced, never summed. Conflicting
replays, corrections, partial gaps and incompatible terminal totals fail closed.
A real executed position can be valued while terminal/driver settlement remains
pending: this is explicitly flagged and **does not clear or replace pending**.
The driver being locally flat cannot hide Broker-confirmed executed exposure.

A commission has no account or order ownership. Fee attribution requires a
unique execution binding and paired native stream epoch. Missing, conflicting
or non-USD/unconverted fee evidence leaves net values null; zero commission and
rebates are valid only with positive evidence. Weighted open-entry fees are
allocated on partial closes, preserving total = realized + unrealized and
net total = gross total - reported execution commissions. The entire bounded
journal is accounted for; only displayed execution details are limited to the
latest 128, with total/truncation flags.

Here **net means after reported execution commissions for this strategy period**,
not all-in account profit. Financing, tax, unreported non-execution charges and
future exit costs remain excluded. `account_nav` and `all_in_account_net_pnl`
remain null until a separate complete account cash-flow/FX/NAV producer exists.
Do not compare this reset-period strategy measure directly with the differently
reset Broker account/portfolio-window PnL series or infer commissions from the
difference between purchase price and Broker average cost.


Account-wide valuation and source-covered returns are separate from strategy
PnL. See [account NAV and net-return operations](account-pnl.md) for read-only
deployment, same-reset evidence, statement import, unknown fields and estimated
liquidation costs.


## BTC-only fee-aware new-entry profile

The opt-in, independently pinned BTC wrapper retains 0.62 and all original exit
semantics while adding explicit expected-move economics and anti-churn controls
for new BTC longs only. It does not alter FX or slow partial risk liquidation.
See [BTC entry economics](btc-entry-economics.md) for exact assumptions,
original-command persistence, deployment conditions and evidence limitations.


### Dedicated operator maintenance hold (Pocket4)

The ordinary \`pause\` is not a safe maintenance permit: a resident Driver
automatically invokes \`paper-on\` before an OPEN and could clear its temporary
native kill-switch. The separate \`maintenance-hold\` command uses a durable
private operator latch and original native kill-switch; an automatic \`paper-on\`
fails until an explicit \`maintenance-release\`. See
[\`paper-maintenance-latch-20261016.md\`](paper-maintenance-latch-20261016.md)
for exact path, limitations and failure handling. **Even a confirmed latch
never grants permission to stop any exposed Driver**; Broker-owner atomic
no-new-entry and verified flat/no-pending/no-active orders are still required.

## Model dependency and maintenance inference

Reconciliation, owner recovery, session rearm and the dedicated canary mode use
their existing native operations without requiring the OpenClaw executable.
The resident loop checks the model executable only when it is ready to start
inference, so missing model software does not prevent quote collection or
original-command reconciliation.

When flat, a driver entry halt, persistent maintenance marker or native kill
switch pauses entry inference. Quote observations, lease handling and pending
command reconciliation continue; open-position safety exits and exit decisions
retain their existing path. The driver records the current pause reason in
`entry_inference_pause`. An answer already running when a hold appears is
cancelled and discarded. The driver retains the original worker until its local
inference process is reaped; a second worker cannot overlap that cleanup. Marker
absence grants no permission: the existing `paper-on` and native checks still
govern every entry.

The inference budget is the remaining lifetime of its original market input,
with the existing 60-second result TTL unchanged. Queueing and process startup
consume that same budget; a monotonic deadline prevents a wall-clock rollback
from extending it. Expiry, a hold, a changed authoritative position or driver
shutdown cancels only the process group created for that inference. A bounded
TERM/KILL cleanup never signals an existing service or unrelated process. This
proves local client cleanup; it is not independent evidence that a remote model
provider has cancelled work already accepted by that provider.

### Optional resident native read transport

After qualifying `current/bin/heptactl` for `pnl-read-stream`, a resident driver
may use `--native-read-stream`. The default remains the existing one-shot CLI
transport for compatibility with older native installations. An attributed
release records the selection in its immutable manifest; prepare it with
`install_decision_attribution.py --instrument EUR.USD --prepare-upgrade
--native-read-stream` and use the existing audited activation procedure. Preparing
the release does not modify a service or grant trading authority. The manifest
pins the helper's source bytes, which the wrapper verifies and executes from
the same buffer as the other pinned runtime modules.

If `current/bin/heptactl` is older, preparation may additionally bind
`--native-read-binary /absolute/qualified/bin/heptactl
--native-read-binary-sha256 SHA256`. The immutable `native_read_binary` manifest
field records both values. Preparation and activation verify that binding, and
every new reader process verifies its owner, permissions, regular-file identity
and content hash. The Linux launcher executes the verified open file descriptor
so a replaced path cannot substitute a different binary between verification
and launch. This binary is available only to the read stream; trade commands,
risk previews and all other native endpoints continue using their original
installed executable. Omitting the binding retains the normal current binary.

The shared driver/PnL reader keeps one read-only CLI per instrument, reusing only
the native client's credential-bound discovery catalog. Health, orders,
positions and quotes remain fresh business calls with the existing token,
owner, schema and freshness checks. Risk previews, permits, mutations and
reconciliation keep their original paths. A failed stream read is not retried
or silently replaced; its CLI is closed and a later read may create a new
reader. This removes repeated CLI startup/discovery without caching a trading
permission or changing the Gateway's global dispatch lock.
