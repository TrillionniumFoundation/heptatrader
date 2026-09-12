# Installed process and exact-revision-pair acceptance

Status: CURRENT  
Implementation: `tests/python/test_installed_runtime_processes.py`, `.github/workflows/core-ci.yml`  
Scope: disposable Linux, real installed executables, simulator only

## What is exercised

The process lane independently preflights two digest-pinned core archives, copies each through a stable no-follow descriptor and extracts each into a separate root-owned slot. Both package digests and `manifest.source_sha` must differ. Packages advertising compiled IB API/PAPER/LIVE or containing the broker-enabled daemon are rejected. The test checks the actual executable behind each daemon PID, not just a mocked success message.

The fixture starts installed `hepta-executiond` and `hepta-tool-gatewayd` as separate OS UIDs, supplies their production environment parsers and two named socket-activation descriptors, provisions a session with installed `hepta-sessionctl`, and drives installed `heptactl` plus the installed Python MCP bridge. Listeners are created by each service UID, preserving `SO_PEERCRED`. Credentials and state live in private fixture directories; the operator, Gateway, Execution and Agent roles are distinct. No broker process or PAPER campaign runs; the historical `paper` session-template name here is paired exclusively with `SIMULATOR` mode and account `SIM`.

The lifecycle scenario verifies native/MCP quote agreement, rejection of a copied token used by another UID, preview-bound placement, typed duplicate without another journal send, MCP cancellation settling to no active order, clean SIGTERM, persisted lease/journal recovery without re-provision, authoritative command lookup and final flatness. Settlement uses bounded authoritative read polling: an accepted RPC is not assumed to be a fill.

The exact-pair scenario runs reference → candidate → reference → candidate against the **same** persisted state. The reference writes a filled 100-unit position; the candidate recovers it and writes a reduction to 75; the reference recovers that candidate-written state and closes to zero; the candidate recovers final flatness. Exactly three `place_sent` journal entries are required. These are distinct executables/artifacts, not two copies of the candidate.

## Reference policy and boundaries

CI pins reference source `d003f54c7c6c2bd19002627f2bcd9081228b01cd`, builds it without broker SDKs or credentials, packages it with its exact source SHA/time, and preflights it alongside the candidate. Updating the reference is a reviewed change to the tested compatibility pair. Both may have the same release label: this is **source-revision-pair** evidence, not a manufactured claim that two different semantic release versions were certified. Evidence records include both source SHAs, labels, artifact digests and daemon executable digests.

The existing `run_release_simulator_smoke.py` continues to own component integration and same-artifact atomic slot-pointer mechanics. The new process test owns independent processes and persisted-state compatibility for one exact pair. Neither test exercises the real systemd manager, nftables installation, production-host restart policy, every schema migration, active-unknown-order rollback or a real broker. Deployment still needs the actual previous artifact and environment-specific acceptance. Do not use this reference pair as authorization to roll back an unrelated production release.

## Invocation and failure behavior

The process lane is opt-in and requires root **only on a disposable Linux test host**. It refuses any existing `/run/hepta-agent`; it creates the fixed production cleanup interlock exclusively and deletes only its own recorded inode. It does not adopt, overwrite or clean an existing host installation. Daemons and the Agent run without root. Test process shutdown has a deadline and fails if SIGKILL is needed. Do not run this lane on a trading host.

```sh
# Supply independently reviewed, SHA-256-pinned, broker-disabled archives.
HEPTA_ISOLATED_PROCESS_TESTS=1 \
HEPTA_PROCESS_CANDIDATE_ARTIFACT=/absolute/candidate.tar.gz \
HEPTA_PROCESS_CANDIDATE_SHA256="$CANDIDATE_SHA256" \
HEPTA_PROCESS_PREVIOUS_ARTIFACT=/absolute/reference.tar.gz \
HEPTA_PROCESS_PREVIOUS_SHA256="$REFERENCE_SHA256" \
HEPTA_PROCESS_EVIDENCE_DIR=/absolute/new-evidence-directory \
python3 scripts/run_python_tests.py --lane process
```

Without the opt-in, an explicit process-lane invocation fails rather than reporting skipped scenarios as a successful acceptance. `--lane all` remains a local development convenience and reports these tests as skipped when the isolated host is not supplied. In CI the process lane is enabled, so missing artifacts, invalid digests, unsafe host state, startup errors, incorrect state, unexpected duplicates or shutdown failures fail the job. Per-scenario PASS records are written only after successful assertions and clean shutdown; failure logs are retained separately and cannot stand in for PASS evidence.
