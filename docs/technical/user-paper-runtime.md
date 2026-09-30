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

## Normal commands and model selection

The `heptatrader-user` command provides `start`, `restart`, `stop`, `status` and
`logs` through the user manager. `native` and `session` forward to the existing
CLIs with user paths; original command IDs and outcome semantics are unchanged.
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
orders or assert flatness. `paper-on` explicitly releases that guard only while
both user services are active. Native quote/risk/identity checks remain mandatory.
A target restart re-creates an engaged guard, so deliberate re-enablement is
explicit and password-free. Neither command changes the system profile.
The runtime-directory preparation unit has no default dependency on basic.target;
it runs before sockets.target to avoid a socket/oneshot/basic ordering cycle.

## Resident autonomous PAPER driver

The user-owned runtime may enable `heptatrader-paper-driver.service`. It restores
only the behavior loop from the historical local AI PAPER agent: 10-second quote
sampling, 120-second `gpt-6-luna` direction decisions, 0.62 reversal threshold,
25,000-unit EUR.USD MKT entries, 0.2% adverse-move reduce-only exits, and continuous
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
