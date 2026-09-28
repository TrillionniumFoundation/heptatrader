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
