# Round38 consolidation contract

Round38 is an engineering-convergence release. It does not authorize PAPER or
LIVE trading and it does not treat an offline test result as production
certification.

## Product boundary

The Agent-facing path remains:

```
Codex/OpenClaw -> MCP, heptactl, or Native SDK
               -> AF_UNIX Tool Gateway
               -> session and trust-domain policy
               -> AF_UNIX Execution client
               -> privileged Execution Service
               -> broker adapter
```

Broker credentials, preview permits, decision leases, risk enforcement,
kill-switch state, order mutation authority, and broker API calls remain outside
the Agent and Tool Gateway trust boundary. There must be exactly one mutation
path. Legacy monoliths, historical OpenClaw units, and direct-broker forensic
material are not Agent-OS product runtime inputs.

The Agent source policy uses an explicit systemd allowlist. It must reject the
legacy `hepta-trader.service` and all historical `hepta-openclaw-*` units.
Discovery is schema version 2 and clients fail closed on a version, descriptor,
catalog, or hash mismatch. Production session policy keeps every LIVE label
unreachable.

Multi-Agent Agent-process isolation is concrete: root-owned per-domain JSON
binds one domain ID to one Agent UID/GID, Tool socket, supervisor socket, and
token directory, while instance-templated systemd units bind those same paths.
UID-2004 is available only behind the explicit single-domain compatibility
gate. The packaged MCP manifest enables no compatibility environment; its
launcher resolves `/etc/heptatrader/trust-domains/uid-<effective-uid>.json`
and fails closed when that root-owned record is absent or mismatched. Each
record now also binds a distinct `hepta-gw-<domain>` Gateway UID/GID, Agent
identity, Execution identity, Gateway state, lease credential, and Tool,
supervisor and Execution socket paths. The only shared object is the
`hepta-gateway` supplementary socket-connect group; it is not accepted as a
domain identity. Execution verifies the Gateway UID and Agent ID from the same
domain record. One Execution authority binds one domain, so multiple untrusted
domains use separate authority processes and sockets. A future central
authority would need an Execution-verified domain/account/UID MAC. All shipped
examples remain WATCH-only.

## Execution trusted-computing-base split

Execution code is split into:

1. protocol contracts;
2. Unix client transport;
3. Agent-side read/support code;
4. privileged server and permit authority;
5. coordinator, journal, risk, and broker composition.

`hepta-tool-gatewayd` may link only the first three layers. A build-time symbol
gate rejects server, permit-authority, coordinator, journal, risk, and broker
implementation symbols in the Gateway. Compatibility umbrella headers remain
for existing privileged code and tests; Agent production code includes the
client-only headers.

This split is an authority boundary, not a second order path. Only the
Execution Service may call the broker adapter.

## Git and recovery topology

`round38-consolidation` is the product candidate. `master` is its ancestor and
may be fast-forwarded only after review and off-host recovery verification; a
merge commit is unnecessary.

The `ai-native-os-*` tags form a separate OOS/research lineage. That lineage
must remain on a private archive branch and must not be merged into the product
trunk. Untracked files matching the tagged research tree are duplicate
worktree material, not product source. Only the independently hashed delta is a
new research increment.

Before any cleanup, pruning, tag removal, or branch movement, recovery requires:

- a complete `git bundle --all`;
- a canonical `show-ref` inventory;
- all release and archive refs;
- an independently hashed research-delta manifest and payload;
- a fresh restore, ref-by-ref comparison, and `git fsck`;
- a private off-host copy and restore exercise.

The same-host bundle and restore are useful local evidence but do not satisfy
the off-host requirement. Until that external step is complete, `git clean`,
`git gc`, pruning, and source deletion remain prohibited.

Recovery evidence preserves two distinct permission views. `git_mode` is the
normalized executable/non-executable mode used to compare a worktree with the
archived research lineage; `recovery_mode` is the exact safe mode restored for
the local delta payload. Exact-content files whose worktree mode differs from
the tagged lineage are represented by a separately hashed mode overlay. This
prevents private `0600` evidence from being widened during recovery while
avoiding redundant copies of content already present in Git.

Delta v3 also carries the canonical inventory for all 1,120 untracked paths,
not only the 36 payload members or the exact-mode overrides. The verifier pins
the audited OOS tag, tree, and inventory digest; rejects file-prefix collisions
with the OOS and final release trees; bounds blob sizes before reading them;
and materializes the complete inventory in a new private root before accepting
the closure. This local exercise proves that the inputs are reconstructible,
but it still does not replace the required private off-host restore and signed
retention receipt.

## Clean CI contract

Product CI is a four-cell matrix:

| Source | IB disabled | IB enabled |
|---|---:|---:|
| repository checkout | required | required |
| extracted Agent source without `.git` | required | required |

The IB-enabled cells compile against the pinned real SDK but do not connect to
a broker. Every workflow-local input must exist in `git archive HEAD`; the
historical untracked OpenClaw script forest belongs to the research workflow,
not product CI. Legacy monolith and bridge options remain disabled.

Nightly sanitizer and coverage evidence is acceptable only when raw logs,
CTest inventory, command metadata, compiler and runner identity, CMake cache,
and coverage XML are bound into the checksum closure. A terminal percentage
without those inputs is not certified evidence.

The two Agent no-Git lanes are attested against the Agent source manifest.
ASAN, UBSAN, and TSAN are attested against the strict source manifest. Each of
those five lanes uses canonical, non-overlapping source and build directories,
rejects extra files, symbolic links, and content drift, and snapshots the
manifest into its sealed runner evidence. Repository lanes do not claim a
no-Git source identity.

`scripts/check_heptatrader_ctest_inventory.py` derives the inventory profile
from the build's `CMAKE_HOME_DIRECTORY`: a checkout containing `.git` uses the
repository inventory, while a source-only tree must carry the fail-closed
Agent OS marker and uses the no-Git inventory. CI and local operators therefore
invoke the checker with only `--build-dir`; hand-maintained profile/path pairs
are unnecessary and a mismatched explicit override is rejected.

## Quality and source budgets

LOC budgets remain no-growth fences. Round38 additionally enforces maximum
function length, lexical cyclomatic/cognitive complexity, local include graph,
dependency fan-in/fan-out, CMake source references, and Gateway forbidden
symbols. These budgets expose extraction priorities; they do not by themselves
prove that a large module is well factored.

Product-side extraction order remains:

1. IB connection/order lifecycle and callback translation;
2. coordinator permit/idempotency/state/reconciliation;
3. event-feed persistence/subscription/replay/cursor;
4. IB runtime configuration/composition/lifecycle;
5. Unix server/session/dispatch.

Each extraction starts with characterization tests and must preserve the single
Execution Service order path. The legacy 6,523-line monolith stays frozen and
outside the Agent bundle.

The first IB extraction moves order-lifecycle projection into a pure tracker.
Terminal states are sticky, an explicit new local generation clears prior
broker acknowledgement, and connection-epoch invalidation prevents delayed
events from an old connection from restoring cancel authority. The adapter
still owns the only broker `PlaceOrder` and `CancelOrder` calls.

## Wrapper and workspace retirement

Legacy root wrappers are inventory inputs, not deletion candidates. Retirement
requires a complete inventory of repository references, deployed system and
user units, cron, live process command lines, and operator runbooks. A wrapper
may be replaced only after it maps to a declarative `hepta_ops` job, all
references migrate, restart drills pass, telemetry covers a compatibility
window, and deletion is separately authorized.

Version 2 of `hepta.legacy-wrapper-retirement-inventory.v2` embeds the
`hepta.host-script-reference-inventory.v1` host view. The host view is
read-only and records only a normalized repository script path plus its source
identity. It never copies a systemd command, command arguments, environment, or
cron line into evidence. It covers system and user unit definitions,
`ExecCondition` and all execution lifecycle directives, unit-file and runtime
state, unresolved `%i` templates and observed instances, system/current-user
cron, and readable process command lines. Direct `scripts/` executables such as
the IB Gateway supervisor are reported separately from root wrappers. Relative
commands are bound to the unit `WorkingDirectory` or process cwd; references
executed from an OOS/research worktree are labelled `external-worktree` instead
of being attributed to the audited product checkout. Any unavailable inventory
surface keeps `complete=false`; it is not treated as an empty result.

The CTP forwarding-header matrix and generated `hepta_ops` compatibility shims
are intentional compatibility layers. They are not duplicate-code cleanup
targets.

Runtime evidence, build trees, nested `x64` outputs, Python caches, and tool
logs are measured separately. Evidence may move only after content-addressing,
checksum verification, immutable retention/legal-hold receipt, and restore
verification. Build and vendor caches should be out of the source worktree.
Budget overruns remain explicit internal open items; they must not be hidden
behind a generic local PASS.

## Same-host recovery materialization

`scripts/verify_heptatrader_recovery_materialization.py` is the standalone
read-only-input verifier for a Round38 Git rescue bundle, ref manifest, OOS
delta manifest, and delta payload. It requires explicit `--verify`, four
independently supplied SHA-256 values, and a caller-selected path that does not
already exist below a trusted parent. It creates a private `0700` root
containing an independent mirror repository and a separate exact untracked
overlay; it never runs Git clean, garbage collection, or prune, and never
deletes or rewrites recovery inputs. The root device/inode and ownership are
bound when the directory is created and must remain identical through clone,
overlay restoration, final verification, and receipt publication.

The verifier requires an exact ref set and symbolic Round38 `HEAD`, a strict
full Git fsck with no unreachable objects, and an independent mirror whose
complete metadata tree contains no alternates, hardlinks, symlinks, special
files, foreign owners, or group/world-writable entries. It verifies the exact
directory set and all 1,120 regular single-link recovery files by UID, mode,
size, and SHA-256 after materialization and again immediately before receipt
publication; both passes must produce the same canonical tree digest. The
running verifier, engineering closure, and delivery closure plus the evidence
generator are bound to product-source Git blobs, and the three running modules
must remain descriptor-stable. On success it atomically creates a canonical
`0600` `hepta.round38-recovery-materialization-receipt.v1` receipt binding the
dual source heads, all input checksums, tool blobs, materialized tree digest,
and the local kernel/Python/Git identity. Its temporary receipt descriptor
remains open across the final repository and overlay scan; the complete bytes
and metadata are re-read immediately before linking, then verified again
through the published inode. A post-link publication failure removes only the
receipt link created by that invocation. This is a same-host offline receipt
only. It does not certify off-host restoration, immutable retention, PAPER,
LIVE, or a release.

## Evidence semantics

The Round38 engineering closure is versioned and reproducible. It must:

- bind a single source lineage and release version across source baseline,
  strict source, Agent source, runtime package, and native-VM bundle;
- reconstruct matrix, sanitizer, coverage, and runner reports from bound raw
  inputs rather than trusting `passed=true`;
- bind recovery refs, bundle, research-delta manifest, exact mode overlay,
  payload, and restore checks;
- reject symlinks, path escapes, hard-link ambiguity, unstable reads, and
  cross-release artifact mixing;
- state `production_passed=false` and `release_authorized=false`;
- keep broker connection, order submission, PAPER, and LIVE authorization
  false.

A valid local closure is named
`local-engineering-pass-pending-external`. It is not a production release
receipt.

## External gates

The following remain external and cannot be synthesized by repository tests:

1. private off-host Git and artifact recovery with branch protection;
2. three independent disposable VMs and four fixed service identities running
   real rootful systemd WATCH gates;
3. immutable/WORM retention with indefinite legal hold and a trusted,
   root-owned signature receipt;
4. a separately approved capped PAPER session followed by real IB connection,
   bounded order/cancel, kill-switch, restart recovery, and audit receipt.

LIVE remains isolated and unreachable. No Round38 source, build, bundle, or
engineering closure grants PAPER or LIVE authority.
