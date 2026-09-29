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
Session renewal is generational and a renewal uncertainty permanently disables new
entries while retaining reduce-only exit behavior. Renewal itself requires the
Execution owner to pass an exact-zero audit. Therefore a due renewal never runs
while exposure, active orders, or a pending mutation exist: the resident loop first
latches new entries off, drives the existing position through the strict reduce-only
flatten path, reconciles the original command, and only then renews the lease. A
successful renewal clears only the halt that the renewal drain itself introduced.
LIVE is never selected.

If a renewal outcome is already uncertain, recovery is an explicit operator path,
not an Agent capability. Keep the new-risk guard engaged and stop the resident
driver; provision the same PAPER owner through `hepta-sessionctl`; run
`heptatrader_paper_driver.py --reconcile-only` so any stored HSR1 command is
inspected under its original command ID and Broker state is projected locally; then
run `--rearm-session-generation <fresh-generation>`. Rearm is rejected unless the
Broker snapshot is authoritative, flat, order-free, and the local pending mutation is
empty. Only after that may the operator release the PAPER guard and restart the
resident service. No state file deletion, fresh mutation ID, or LIVE transition is part
of recovery.
The user Execution service backs off for 30 seconds after startup failure so an
upstream market-data/session fault remains fail-closed without hammering IB Gateway.
