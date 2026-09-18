# Core artifact acceptance and tagged release

Status: CURRENT
Applies to: broker-disabled core packages on disposable Linux CI VMs

## Producer and consumer identity

The core workflow keeps the `core-runtime-exact-head` check context. It builds
and runs native tests, then invokes `scripts/accept_core_release.py`. The tag
workflow checks tag/VERSION equality, builds native tests and invokes the same
driver. Rebuilding a tag is permitted, but its newly built package must pass
installed acceptance; another run's source-only green status is not substituted.

The driver verifies the source checkout, runs the install/core Python partitions,
and requires source-bound structured generation I/O/cost evidence before packaging
the existing build once and computing its SHA-256. The retained core evidence
proves exact selected-range index reads plus the 4/8/16-generation sampled
seal/verify/storage curve. That same package path/digest is supplied to simulator
lifecycle smoke, installed multi-UID process tests and real PID 1 systemd
acceptance. The process partition emits a second exact-source/package-bound
generation curve for recovery, simulator recovery, startup readiness, VmHWM,
place p99 and retained disk; the driver rejects missing or identity-drifted
evidence before PASS. The process partition also exercises the pinned prior-source
package through upgrade, rollback and re-promotion using shared persisted state.
There is only one orchestration implementation for these steps.

The fixed previous source remains `d003f54c7c6c2bd19002627f2bcd9081228b01cd`.
This is an explicit regression pair, not universal historical compatibility.
The reference is built without credentials. A fresh CMake File API codemodel
selects its actually installed executable targets and their build dependencies.
It does not recompile unrelated old test programs merely to assemble the prior
payload, nor maintain a second hardcoded target list. Missing, empty, foreign
or ambiguous build models fail before reference acceptance. Current native and
Python suites still run in full, and the actual prior package still installs
and participates in the same shared-state rollback test. Updating it still requires the
[persistence support decision](persistence-support-window.md).

## Success, failure and publication

`dist/core-acceptance.json` is created with no replacement only after all phases
succeed and a final package rehash matches. It records exact current/previous
source and package identities, checks exercised and non-authorizing scope. No
receipt is emitted for failed install, Python, smoke, process or systemd phases.
Diagnostic logs are retained separately, including on failure.

The tag manifest producer rehashes the package and requires the package receipt
and acceptance receipt to agree on source, version, profile and digest. It also
requires acceptance PASS and `authorization_effect=NONE`, with PAPER/LIVE false.
Only then may the release evidence upload publish the package and manifest.
There is no automatic GitHub Release publication, signature, Broker activation
or host installation implied by this workflow.

## Development and testing

`test_release_workflow.py` parses workflow structure and executes real tag and
manifest shell blocks with private fixture files. Commented-out tag checks,
swallowed mismatch failures, absent acceptance and substituted bytes are tested.
`test_core_release_acceptance.py` executes the production orchestrator with
inert process seams, verifies argument/digest continuity, failure propagation
and cleanup. Additional real CMake fixtures build/install/run the selected
executable, discover a newly installed executable without a list edit, and leave
an intentionally uncompilable uninstalled test outside the reference build.
These unit seams do not replace the real installed fixtures invoked
by the actual workflow.

The privileged driver must run only on a disposable CI VM. The existing fixtures
refuse pre-existing HeptaTrader users, state and units. Do not invoke it on a
trading host, and do not use a fake source SHA to make an exported local tree
appear identical to a remote commit.
