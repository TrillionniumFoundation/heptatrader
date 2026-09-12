# Real systemd simulator acceptance

Status: CURRENT
Implementation: `tests/systemd_simulator_smoke.py`, `.github/workflows/core-ci.yml`
Tests: `tests/python/test_systemd_smoke_admission.py`, `tests/python/test_systemd_units.py`, `tests/python/test_cmake_install_integration.py`

## What this adds

The installed-process test deliberately creates sockets in child processes. It
cannot prove that systemd unit executable paths, credential delivery, socket
activation, service sandboxing or manager-driven shutdown work. This acceptance
uses actual systemd PID 1 on a disposable CI VM and the same already-built,
SHA-256-pinned core archive. It does not rebuild or patch installed programs.

All five daemon unit variants use `/usr/bin`, matching CMake's canonical `/usr`
install. The IB network-policy unit's credential-delivered code comes from the
packaged `/usr/libexec/heptatrader/hepta_broker_egress_policy.py`. Core packages
exclude IB service/socket units, the broker-policy unit and the IB tmpfiles rule;
non-secret IB examples remain inert documentation. No broker units are started
by this test, and no firewall policy or broker account is accessed.

The install integration test traverses actual installed service commands,
credential-delivered code and required socket/service associations. An absolute
path alone is no longer accepted as proof of a correctly installed executable.
It also rejects links and missing execute bits. This is a structural check;
only the manager acceptance supplies dynamic systemd evidence.

## Behavior exercised

The fixture requires root, explicit `HEPTA_DISPOSABLE_SYSTEMD_TEST=1`, Linux and
`/proc/1/comm=systemd`. Existing HeptaTrader accounts, units, installed files or
state cause refusal before installation. It creates disposable non-login Agent,
Gateway, Execution and outsider identities; it does not adopt a production host.

The exact artifact is independently preflighted, extracted and installed to
`/usr` without replacing existing files. Non-secret configuration is derived
from the installed examples and bound to the fixture UIDs. Fixture-only keys
are delivered through the unchanged units' `LoadCredential` declarations.
`systemd-tmpfiles` and `systemd-analyze verify` run against installed assets.

Real service startup must reach non-degraded readiness. Evidence checks the
executable behind each PID, actual UID, zero effective capabilities and separate
network namespaces. The installed CLI and MCP exercise quote parity, copied-token
wrong-UID denial, preview-bound simulated fill, duplicate without a second send,
MCP cancellation, clean stop/start, encrypted-lease and journal replay without
re-provision, preserved command status, final flatness and socket removal.
Exactly three `place_sent` records are expected; all economic effects are local
simulator records. No timeout is interpreted as order rejection or safe resend.

A PASS record is published only after successful assertions, clean service stop
and fixture cleanup. Failure retains bounded unit journals, not a fabricated
success receipt. Static files and state roots are removed only if they still
have the fixture's recorded inode identity. Fixture tokens are redacted.

## Running the acceptance

Only on an empty, disposable systemd VM; never on a trading or shared host:

```sh
sudo env HEPTA_DISPOSABLE_SYSTEMD_TEST=1 \
  python3 tests/systemd_simulator_smoke.py \
  --artifact /absolute/candidate-core.tar.gz \
  --expected-sha256 "$CANDIDATE_SHA256" \
  --evidence-dir /absolute/new-systemd-evidence
```

The result binds package/source and installed unit/executable digests and reports
`systemd_manager_exercised=true`, `broker_mutation=false`,
`authorization_effect=NONE`, `paper_authorized=false`, `live_authorized=false`.
Missing PID 1 is an error, not a skipped success or simulated substitute.

## Limits

This proves one candidate on the tested disposable distribution, not the actual
trading host, an IB network policy, every templated multi-domain configuration,
or the target host's previous persistent schema. Distinct-artifact persisted-state
compatibility remains the separate [exact-pair process test](installed-process-acceptance.md).
`HOST-ROLLBACK-001` remains external work until actual deployment-specific evidence
exists. Neither form of CI evidence grants PAPER or LIVE authority.
