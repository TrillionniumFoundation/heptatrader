# Broker network isolation

HeptaTrader treats broker TCP reachability as an Execution-only OS
capability. Typed Tool Gateway checks, schema hashes, preview permits, leases,
fences, OMS/risk checks, and audit journals are not a complete boundary if an
Agent that can generate code can also open an IB API socket itself.

## Versioned boundary

`hepta-broker-egress-policy.service` installs an nftables `inet/output` rule
from `hepta-broker-network-policy-v1.json`. The rule first requires the
destination to resolve as `fib daddr type local`, then rejects locally
generated TCP traffic whose destination port is any of:

* `4001`
* `4002`
* `7496`
* `7497`

The local-destination predicate is part of the fail-closed contract. It
protects local IB Gateway/TWS API listeners reached through loopback or any
other host-local address without intercepting IB Gateway's own upstream
session when a remote broker endpoint happens to use the same numbered port
(notably remote TCP 4001).

The fixed compatibility authority is the exact numeric UID for
`hepta-ib-exec` in `hepta-service-identities-v1.json`. The policy JSON binds
the SHA-256 digest of that identity manifest. Per-domain PAPER is a separate,
explicit and mutually exclusive mode:
`/etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json` may select
at most one dedicated `hepta-ib-exec-<domain>` identity per host. Selecting a
domain replaces the fixed compatibility UID; it never extends the allowlist.
The manifest is optional, and its reviewed default has
`paper_authorized=false` with an empty identity list. Simulator identities
such as `hepta-exec-<domain>` can never enter the network allowlist. A second
templated PAPER identity is rejected before nftables changes because
account-level aggregate risk, globally unique client-id allocation, and a
cross-domain kill switch do not yet exist. Other domains may continue to run
WATCH.

The PAPER identity manifest is bound to the exact source-policy SHA-256. It
must be a root-owned, single-link regular file at mode `0600`; its record is a
strict five-field object: `domain_id`, `identity`, `uid`, `gid`, and `role`.
The record uses a matching UID/GID and names an existing non-login OS account
with no aliases or supplementary groups. Control paths or kill-switch
metadata are forbidden in this network manifest.

PAPER service authority is a separate authority manifest,
`/etc/heptatrader/hepta-ib-paper-domain-authorizations-v1.json`. It is bound to
the SHA-256 of the exact network identity manifest, also permits at most one
record, and defaults to `paper_authorized=false`. The read-only
`hepta-ib-paper-domain-authority` helper renders a reviewed tmpfiles fragment
whose domain control directory is `root:hepta-ib-exec-<domain>` mode `0750`
and whose kill-switch marker is `root:hepta-ib-exec-<domain>` mode `0440`
with the default-engaged content `engaged`. Applying that fragment remains a
separate privileged provisioning action. The
`hepta-ib-paper-domain-preflight@.service` is a `Type=notify` lifetime guard,
not a start-only check. It validates the two digest-bound manifests, unique
NSS identity, exact paths/modes and default-engaged marker. It then holds a
non-blocking exclusive flock on a root-only `0700` host authority directory
for its whole lifetime, creates a root-only single-link `owner.v1` crash
tombstone, atomically activates only its manifest-bound domain UID, and
verifies that exact live nft state before reporting ready. That host lease
serializes two separately valid A/B manifests as well as rejecting an
overfull single manifest. The guard continuously rechecks both manifest
path/inode/digests, runtime metadata, and live network authority. It neither
creates nor disarms the domain control state.

The templated Execution daemon and its symmetric preflight dependency
explicitly set `StartLimitIntervalSec=1800s` and `StartLimitBurst=5`. The
interval covers five complete 240-second starts, 35-second stops and 2-second
restart delays, so a slow authoritative snapshot failure cannot age out before
the sixth start and turn the nominal five-start limit into an unbounded loop.
Execution exit status 9 is excluded from automatic restart because it denotes
ambiguous runtime authority and requires root-custodian reconciliation;
transient startup exit 6 remains inside the bounded retry budget. These
production values bound repeated daemon/composition activation and the
resulting authority/listener churn.
Exhaustion leaves the network at deny-all; an operator may use
`systemctl reset-failed` only after reconciliation and before a reviewed
restart.

A manifest, NSS, authority, or kill-switch mismatch fails before a templated
PAPER service starts. The generated network guard returns authorized traffic
to its narrow base chain and has no `accept` verdict; traffic continues
through the host's other firewall base chains and can still be denied by
them. Every other socket owner receives an explicit TCP reset at a protected
destination port.

The nftables chain has an accept default for every other destination port.
In particular, the Agent drop-in retains `AF_INET` and `AF_INET6`, so HTTPS,
DNS, and other reviewed model-provider egress are not disabled. The Agent
drop-in removes capabilities and namespace creation so arbitrary Agent code
cannot reconfigure or escape the host network policy. Gateway services remain
`PrivateNetwork=yes` and `AF_UNIX`-only independently of this policy.

The shipped SHADOW policy unit loads the helper through a systemd credential
and runs `--supervise-deny-all`. It atomically replaces only its own table,
then remains resident as a `Type=notify` guard while authorizing no connector
and no UID. The explicit PAPER modes implemented by the helper are not used by
this unit and require a separately reviewed PAPER activation boundary.

Before reporting ready and on every bounded poll, the broker guard runs
`nft --json list table` and requires exactly one reviewed table, the two
reviewed chains, the exact two-rule deny-all or three-rule active-domain
shape, the exact port and UID set, and SHA-256 comments binding the effective
policy, service identities, and optional PAPER identity manifest. It also
pins and monitors the three source paths by device, inode, metadata, and
digest.
The unit makes their parent directories read-only inside the service
namespace. It deliberately does not bind-mount each watched file: an
individual file bind mount would pin the old inode and conceal an operator's
atomic path replacement from the drift monitor.

A deleted or modified table, or any source-path/inode/digest drift, makes the
guard atomically install deny-all and exit failed. A normal broker-guard stop
also installs deny-all before returning. Its independent credential-loaded
`ExecStopPost` runs `/usr/bin/python3.12 -I -S` against the loaded helper with
`--tighten-deny-all`, covering `SIGKILL`, interpreter failure, and other paths
that cannot run Python cleanup without reopening the installed helper path.

Every clean or detected-drift exit of a per-domain authority revokes the
domain UID to guarded deny-all before clearing `owner.v1` and releasing the
host flock. Its independent
`ExecStopPost=/usr/libexec/hepta-ib-paper-domain-authority --finalize-stop
--domain %i` blocks on the same flock, repeats deny-all, and clears only the
matching tombstone. After `SIGKILL` the flock may be released before
`ExecStopPost` begins, but the persistent tombstone makes a competing domain
fail closed during that interval. A later domain can activate only after the
finalizer owns the lock, installs deny-all and clears the prior exact owner.
The broker guard remains resident while the domain is revoked, so WATCH policy
continues without retaining a PAPER UID.

Agent, fixed and templated Gateway, and fixed and templated PAPER services use
`BindsTo=` plus `After=` for the broker guard. Templated PAPER additionally
binds to its per-domain authority guard. The guard is symmetrically
`BindsTo=` and `PartOf=` the PAPER daemon, has `StopWhenUnneeded=yes`, and
rejects an independent manual start. Therefore daemon startup failure,
unexpected exit, start-limit exhaustion, or an explicit stop tears down the
guard and runs its deny-all/tombstone finalizer; a later automatic or manual
restart must reacquire and revalidate the host authority.

Both templated command/event sockets bind to and start after that same guard.
They also reject independent manual starts, stop when no daemon needs them,
and remain `PartOf=` the PAPER daemon. A competing domain must acquire the
single host guard before either of its socket paths can listen. If that
preflight loses the host flock, the socket jobs fail closed; if the running
daemon or guard stops, both paths are removed. Fixed PAPER sockets remain
`PartOf=` their fixed PAPER service. These contracts prevent a passive socket
or failed daemon from retaining a second PAPER composition. The finalizers
start no unit and introduce no systemd ordering cycle.

## Installation and activation

Install the policy helper as `/usr/libexec/hepta-broker-egress-policy`, the
two JSON inputs under `/usr/share/heptatrader`, and the unit under
`/usr/lib/systemd/system`. Install the versioned vendor drop-ins for Gateway
and IB Execution. Apply both reviewed Agent examples as drop-ins to the actual
Codex/OpenClaw host service.

Before an Agent host or PAPER authority is activated:

1. Ensure `/usr/sbin/nft` (or the canonical `/sbin/nft`) is a root-owned,
   non-writable regular executable.
2. Install the default-false PAPER identity manifest as a regular
   `root:root` file at exact mode `0600`. Do not reuse a
   `hepta-exec-<domain>` Simulator UID for PAPER.
3. If templated PAPER is explicitly approved, install a matching default-false
   or one-domain authority manifest at exact `root:root` mode `0600`, render
   its tmpfiles fragment, review it, and apply it while PAPER remains stopped.
   Confirm the marker is still engaged. Never authorize two templated PAPER
   domains on the same host.
4. Run the static broker policy checker against the installed source/package.
5. Reload systemd, then enable and start
   `hepta-broker-egress-policy.service`. Its
   `network-pre.target` ordering applies the boundary before normal networking.
6. On a disposable rootful host, bind IPv4 `127.0.0.1` and IPv6 `::1`
   loopback sentinels on all four protected ports. In fixed mode prove success
   only for the fixed authority. In
   per-domain mode prove failure for that fixed UID and for both staged
   trust-domain Agent, Gateway, and Simulator UIDs; prove success only for at
   most one explicitly authorized templated IB Execution UID. Prove the
   second-domain manifest is rejected without changing the active policy,
   prove revocation closes that UID again, and prove every Agent UID can
   connect to a non-protected sentinel port.
7. Only after those gates pass may the separately reviewed capped PAPER
   service be considered for activation.

The distributable passive runtime remains `/usr`-only and carries the canonical
identity only as a verified documentation source. The dedicated SHADOW host
projection copies that source to the fixed network-policy path as its sole
host-state member: a root-owned mode-`0600`, 257-byte
`paper_authorized=false`, `live_authorized=false`, empty-identity manifest with
SHA-256 `4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435`.
The projection rejects every other `/etc` member. The offline round86 profile
deployer requires those exact bytes and refuses the former PAPER-authorized
manifest. This replacement only removes broker reachability; it does not
install a PAPER authority credential or start a service.

The passive installer serializes the transaction with the persistent root-owned
lock `/var/lib/hepta/.shadow-runtime-install.lock`. It samples all nine PAPER
units plus all Gateway/activation/reconcile/WATCH authority units, the engaged
kill switch, absent campaign policy, and deny-all broker
boundary before and after publication, and rejects any difference between the
two samples. Its v4 receipt records both point-in-time observations and exact
lock inode evidence while explicitly making no continuity claim. A normal
failure rolls back under the still-bound lock and keeps the identity deny-all.
After a detected lock replacement it performs no unlocked `/usr` restore or
cleanup, withdraws only a provable receipt into retained quarantine, reasserts
the canonical deny-all identity, and fails. Under the same lock, success
atomically updates the root-owned mode-`0600`
`/var/lib/hepta/shadow-runtime-install-state/current-install-v1.json` marker.
Its monotonic generation binds the current manifest/receipt file digests,
lineage, backup root, 95-file path digest, and all-false authority flags. A
caller must freeze the exact predecessor generation and pointer-file digest
(`0` plus `absent` for genesis); the installer compares both under the lock
before its first mutation and also forbids any later 95-path-set drift. Thus a
stale frozen writer cannot auto-rebase itself, and a later supported install
makes an older receipt non-current even if all 95 files remain byte-identical.
Receipt v4 retains the predecessor generation and exact predecessor pointer
file digest observed by that CAS. Current pointer v1 binds the new receipt, so
consumers can independently verify one gap-free supported installation edge;
this is still not long-running broker or trading continuity.

All supported privileged writers must honor that lock and the strict root-owned
ancestor contract. A root or `CAP_DAC_OVERRIDE` writer that deliberately
bypasses it is a host-root compromise outside this userspace installer's threat
model. The integrated profile, activation and admission consumers first verify
the externally pinned canonical manifest and installer member before executing
the frozen installer consumer. They then hold the same lock while binding
receipt v4, the named lock inode, the fixed current generation, the complete
95-file projection and the deny-all identity, producing exact
consumption-evidence v3 with the predecessor edge. That evidence is still point-in-time/current-install
evidence only and grants neither continuity nor PAPER/LIVE authority.

The fixed `hepta-p1-watch-activation.service` has no `[Install]` section. It
loads the activation transaction, profile validator, broker-policy helper, and
shadow installer consumer as four systemd credentials. Its first journaled mutation persistently arms the
fixed reconcile timer; only then may it quarantine the exact failed historical
P1 bundles, restart the credential-loaded broker guard at exact deny-all, clear
both persistent and runtime Gateway masks, and start the
SIMULATOR/ALLOW_TRADE=0 Gateway. The activation receipt binds the timer, broker
and Gateway epochs, the final nine-unit PAPER-inactive sample, the exact
mutation sequence, and a fresh-activation flag. A failed receipt always
overrides an older active receipt.

The reconcile timer is a crash/reboot backstop, not admission evidence. Before
commit it requires an empty WATCH authority boundary. After commit it permits
the separately owned WATCH custodian to hold its bounded observation lease,
while continuously reattesting the activation receipt, profile, timer,
credential-loaded broker source, exact deny-all policy, Gateway epoch, engaged
kill switch, absent PAPER campaign policy, and all nine PAPER units inactive.
The activation unit does not pre-start the timer: the transaction arms it as
its first journaled mutation, and the timer's first deadline is relative to
that activation rather than host boot time. Reconcile jobs are ordered after
an in-flight activation, so a late-boot start cannot race the transaction lock.
Neither activation nor reconciliation provisions WATCH authority, launches a
campaign, authorizes PAPER/LIVE, or turns an activation receipt into continuity
evidence.

For a templated domain, start only
`hepta-execution-ib-paper@<domain>.service`. Never start its preflight or
socket units directly: those units intentionally reject manual activation and
exist only inside the daemon's guarded dependency transaction. Starting a
second domain while one is active must fail before either second-domain socket
listens.

To add or revoke a per-domain PAPER identity, first stop its PAPER service.
That stop revokes the domain to deny-all while leaving the WATCH broker guard
active. Atomically install newly reviewed exact mode `0600` network and
authority manifests, ensure both staged files bind the same reviewed
configuration, restart the broker guard, and repeat the disposable-host gate
before starting PAPER. Replacing either manifest while the guards are active
is treated as drift: both guards fail closed and the dependent PAPER stack
stops. Default WATCH staging installs only the empty default-deny network
identity manifest. It does not install a PAPER authority manifest, create a
dedicated PAPER identity or control directory, apply a PAPER tmpfiles fragment,
or activate any PAPER unit.

The independent `run_hepta_broker_network_rootful_gate.py --run` rehearsal is
network-only. Its digest-pinned, build-network-disabled, loopback-only
container stages the two policy/authority helpers, inert IPv4 and IPv6 TCP
sentinels, and
two root-owned default-engaged kill-switch fixtures, but no IB binary, PAPER
unit, credential, broker listener, or broker protocol. It proves fixed-only
default policy, exact live nft JSON, mutually exclusive one-domain opt-in,
rejection of an overfull manifest, table-flush detection, atomic
manifest-revocation detection, clean-stop deny-all, independent
`ExecStopPost` revocation after `SIGKILL`, persistent-owner rejection of a
competing B start before A finalization, side-effect-free immediate
`ExecStopPost` for that rejected foreign domain, clean domain revocation while the
WATCH broker guard remains active, and preserved model-provider egress. The
templated effective-systemd dependency contract is also statically verified.
The separate, default-off
`run_hepta_paper_domain_rootful_systemd_gate.py --run` rehearsal instantiates
the real PAPER service, preflight, request-socket and event-socket templates
under disposable systemd PID 1 with `--network=none`, a read-only root
filesystem, no bind mounts or devices, inert credentials and an inert
Execution stub. It proves both domain templates independently, one-domain
host authority under a concurrent A/B start, daemon `SIGKILL` restart,
composition start-limit cleanup, the real broker-guard `ExecStopPost`
deny-all finalizer, two-socket cleanup and stopped-socket no-reactivation.
Dirty-tree runs are explicitly non-final rehearsals. Only a fresh receipt
bound to a clean frozen source SHA, exact runner SHA, digest-pinned base image
and reviewed environment may enter release evidence, and that receipt still
does not authorize capped PAPER.

Do not validate this boundary against a real TWS/IB Gateway listener. The
rootful gate uses inert local TCP sentinels, performs no broker protocol, and
cannot place an order.

## Operational limits

This v1 policy protects locally generated TCP connections by socket-owner UID.
Processes with root or `CAP_NET_ADMIN` can modify host firewall state and are
outside the Agent threat domain; the Agent host drop-in grants neither. A
host-level HTTP/SOCKS forwarder that is intentionally reachable by the Agent
could reintroduce a broker path and must be separately denied or placed in a
network namespace.

The guard uses a `15s` service watchdog, `2s` per-`nft` command deadline,
heartbeats before and after synchronous boundary validation, a `30s` stop
budget, exact nft inspection, and `0.25s` bounded polling. At most three
guarded candidates are inspected between heartbeats, so the `6s` aggregate
command deadline remains below half the watchdog. These deadlines
materially tighten host lifecycle behavior without treating a transient
two-second host scheduling delay as proof that the boundary process is dead.
They remain static release contracts and must be exercised under CPU/I/O
pressure; polling is not a network namespace and
does not eliminate the interval between destructive kernel-state change and
detection. It also cannot make `SIGKILL`, systemd failure, kernel failure, or
root compromise impossible; `ExecStopPost` is a finalizer, not a kernel
isolation primitive. Therefore a dedicated broker netns or an equivalent
cgroup-BPF enforcement boundary remains a hard capped-PAPER certification
gate. Certification must prove Agent and Gateway cgroups/UIDs fail on all four
ports while only the authorized Execution cgroup/UID succeeds, including
restart, firewall flush/reload, and revocation drills. The nft lifetime guard
is defense in depth and a deployment gate; it is not by itself authorization
for real PAPER.

Round95 closes that requirement with the separate
`hepta.broker-network-hard-isolation-gate.v1` native nft/netns/cgroup report.
Fake executors and rootless fixtures are permanently `REHEARSAL_ONLY`; a
certifying run requires real root, `--run`, the reviewed kernel primitive,
complete cleanup and four independently reviewed environment provenance
records. It still uses inert sentinels, never a real TWS/IB Gateway endpoint,
and all PAPER/LIVE/mutation/direct-broker/order authority remains false.

The rootful and hard-network gates remain optional engineering diagnostics.
Local PAPER activation does not consume reviewer identities, signatures,
provenance closures, or promotion receipts. `hepta-local-paper-control`
derives the single allowed PAPER UID from the root-owned local authorization
file and keeps LIVE ports denied.

PAPER approval also requires a host inventory showing no reachable forwarder
and a disposable-host positive/negative drill.
The fixed compatibility PAPER units and all three templated PAPER units
(service, request socket, and event socket) declare mutual conflicts. Only one
PAPER composition may be active on a host; WATCH domains remain independent.
