# Pocket4 operator maintenance hold (entry-only)

The existing Pocket4 resident FX/BTC Driver can invoke \`heptatrader paper-on\`
automatically. The legacy \`heptatrader pause\` is an ephemeral native
kill-switch toggle, NOT a persistent no-new-entry fence: the Driver can erase
that marker on a later prospective OPEN. Do not treat the old pause command as
a safe maintenance unlock or as a reason to stop an exposed Driver.

This patch adds \`heptatrader maintenance-hold\`,
\`heptatrader maintenance-status\`, and \`heptatrader maintenance-release\`.
The existing pause and paper-on remain compatible when no hold is installed.
The operator hold is a distinct private **persistent** marker under
\`~/.local/state/heptatrader/operator-maintenance/\`. It is written and fsynced
**before** the original /run user kill-switch is engaged. All verbs share the
original \`operator-maintenance.lock\` flock; a resident \`paper-on\` cannot
remove a valid hold. Unknown marker/symlink/owner/mode/JSON state refuses.
Explicit release verifies that both native switch and durable hold still exist;
the user must request the distinct release command. Service-readiness checks
also run before release. Both markers have private owner identity requirements.

**Not a maintenance-stop permit.** No new Broker order, owner fence, account
snapshot, cancellation, restart or service stop is performed by these commands.
While the native entry marker blocks normal new PAPER risks, it has no
independent Broker-owned stop, and there may be an in-flight original command.
The /run marker is ephemeral on user-manager restart. A persistent operator
latch by itself does not guarantee the native kill-switch was restored after
a reboot, and does not replace a Broker-global active-order inventory.
The helper always reports \`may_stop_driver=false\`, even when both markers exist.

An actual controlled Driver rollout still requires existing Execution-owner
atomic no-new-entry admission, authoritative same-account reconciled flat
positions and no active/pending/unknown orders, and verified re-arm/rollback
before any stop shield is changed. Never change \`RefuseManualStop\`, use SIGKILL,
or claim driver-death protection based on a passing marker test.

The helper's local unit tests use only disposable private fixtures; no
accounts, live services, or runtime control directory are mutated by tests.


## Native Execution recovery marker compatibility (2026-10-16)

A real Pocket4 read-only check found a valid owner-owned 0440 **empty**
\`kill-switch\` created at the same time as Execution's automatic service
restart. The C++ native guard treats presence and safe inode metadata as
**Engaged** independently of marker contents. The initial Python CLI
incorrectly required the old CLI's tagged \`engaged\\n\` bytes even for
read-only status, so it returned HOLD_BLOCKED instead of describing an
already protective native hold.

The corrected helper distinguishes \`CLI_TAGGED_PAUSE\`,
\`INDEPENDENT_EMPTY_NATIVE\`, \`UNRECOGNIZED_INDEPENDENT_NATIVE\` and
\`ABSENT\`. Read-only status works for all safe native marker files.
\`paper-on\` may clear only the old tagged CLI pause, and never the empty
Execution-owned native recovery marker. Explicit maintenance release may
retire its **own** persistent hold while keeping that independent native
marker unchanged. Unrecognized nonempty payloads still refuse changes.

The helper never assumes an empty marker is an operator-owned pause.
No code clears/replaces the actual existing marker during this repair.
