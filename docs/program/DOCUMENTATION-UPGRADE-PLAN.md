# Documentation Control Plane Continuous Upgrade Plan

Status: current normative plan
Applies to: documentation, registries, schemas, generators, validators, CI, build graph and install tree
Verification: M0/M1/M2 gaps and exact-revision gates
Authority: documentation-upgrade implementation sequence

## Objective

The current tree keeps one discoverable development-document authority. Normative facts are singular, structural facts are machine-readable, generated views are reproducible, and completion state is derived from exact-revision evidence. Git history—not checked-in aliases, archived prose or dormant build entrypoints—is the historical record.

## Current audit closure sequence

1. **Physical historical cleanup**  
   Remove compatibility aliases, old PLAN/status files, legacy Markdown/text/media, and dormant CMake/Visual Studio entrypoints that can be indexed or opened as an alternative project.
2. **Repository-wide document inventory**  
   Register every Markdown surface outside `docs/` as an entrypoint-only document linked to one canonical target. No package README may create independent architecture, capability or roadmap authority.
3. **Formal manifest validation**  
   Apply the checked-in Draft 2020-12 ModuleManifest schema to every manifest. Reject unknown fields, wrong versions/types, unsafe paths, duplicate arrays and invalid migration states before semantic validation.
4. **Physical source ownership**  
   Map every active C/C++ file to exactly one physical owner. Permit overlap only for an exact participant set attached to one open gap, one physical owner and one exit milestone.
5. **Configured build-graph binding**  
   Query the CMake File API after configure and validate actual target ownership, compiled sources, direct production-source test inclusion and inter-module dependencies. Static target-name regexes are not sufficient evidence.
6. **Contract and capability traceability**  
   Resolve capability → module → contract/schema → source/target owner → verification → gap/workstream/milestone → exact evidence. Generated matrices display registry facts but never create completion state.
7. **Exact-revision evidence**  
   Run read-only documentation, core and canonical-full workflows on the unchanged head and merge candidate. External PAPER qualification remains a separate protected lane and never grants LIVE.

## Implementation-level continuation

The 22 registered guides establish module coverage, not complete implementation of every target responsibility. This plan requires the following work products; it does not declare them complete merely by listing them. The module registry's implemented and excluded scopes remain authoritative, and no future capability is silently added to the supported product scope.

| Work product | Acceptance requirement |
|---|---|
| Per-guide current capability projection | Every generated technical guide opens with that module's implementation state, exact implemented/excluded scope, source/test references and external gates. Target semantics are explicitly separate. |
| Interface specifications | Native, MCP, tool catalog and execution-wire entries resolve to actual field/operation specifications, not merely their own index. Examples are exercised by contract tests. |
| State and persistence details | Execution/OMS, Session, Management and Strategy distinguish state transitions, durable commit points, crash windows, restart behavior and checkpoint metadata from checkpoint payload storage. |
| Requirement-to-assertion traceability | Each material capability claim identifies a production symbol and a direct positive/negative test assertion. Existing file presence and headings alone do not establish behavioral coverage. |
| Receipt integrity and exact binding | Reject stale source identity, wrong PR/merge group, mismatched required contexts, unsafe links, file substitutions, malformed evidence, duplicate JSON keys and non-finite JSON constants. |
| Detached qualification identity | Protected verifier evidence remains separate from the immutable source candidate. Recording evidence must not require changing the candidate that it qualifies. |
| Completion state semantics | Distinguish repository implementation, simulator verification, external qualification, release eligibility and deployment. Explain milestone dependency/state discrepancies rather than copying a single closed flag. |
| Bounded product closure | Select and document the supported venue/instrument scope, then specify instrument metadata, cash/fee/position accounting, risk inputs and reconciliation invariants. Do not infer multi-asset completeness from order-control tests. |
| Real runtime and research mechanisms | Implement and verify any promised artifact execution/isolation, checkpoint recovery, feature parity and rollout executor before promoting those capabilities beyond their registered bounded scopes. |

Priority is receipt/authority correctness and truthful specifications first, then supported-scope financial and runtime behavior. Real broker, platform and independent-review evidence cannot be manufactured by adding source files, changing state strings or extending a timeout.

## Receipt validation boundary

`check_gap_closure.py` is a repository integrity and envelope-binding checker. Its PASS does **not** authenticate the receipt issuer, prove broker callbacks were observed, prove organization controls are currently installed, grant trading capability, or replace the protected live verifier. The JSON summary explicitly reports `grants_qualification=false`.

When an external gate is represented as closed, the checker requires an independently selected exact source identity. A Git checkout must be clean and rooted at the actual top level; inherited `GIT_*` redirections are ignored. It uses its actual HEAD; a supplied `--expected-source-sha` must match that HEAD. An archive without Git metadata requires an explicit source identity from the controlled source/artifact workflow, never from the receipt being checked. Governance additionally requires the exact PR number, merge-group SHA and required contexts from the candidate's canonical policy.

Receipts are read through retained directory descriptors. Every path component is no-follow, the leaf must be a bounded regular file with one hard link and no world-write permission, and file/directory bindings are rechecked after reading. Unsupported secure-read primitives fail closed. Digest integrity is not a digital signature and never proves issuer identity.

## Validated closure reporting and source re-admission

Documentation Control module 1.2.1 makes the closure checker validate and report
one private registry snapshot. `evaluate(...)` in `scripts/check_gap_closure.py`
returns `(errors, report)`: a nonempty error list always accompanies `report=None`;
a successful report is derived only from the same parsed gap and module objects
that passed validation. The CLI uses this combined operation once. It does not
validate one file revision and then reopen changed registry paths for its JSON or
text summary. A path replaced or deleted after capture cannot introduce unverified
closed states into that report. The report describes the captured observation,
not a continuous promise about later filesystem contents.

`validate(...)` retains its list-of-errors interface. Public `summary(...)` now
performs its own complete evaluation and raises ValueError on rejection; it is
not an unchecked rendering shortcut. It accepts the same repository/receipt roots
and independently selected source, PR and merge-group identity arguments as
validate. A prior validate call conveys no cached approval to a later summary.
Callers requesting both errors and a report should use evaluate to avoid two
independent observations. Existing open-external-gate calls need no new arguments;
closed-gate archive callers must explicitly supply their source identity to
summary as well as to any separate validation. The summary schema remains V2 and
`grants_qualification` remains false in every successful result.

For a closed external gate, `_source_identity` now rejects any tracked index entry
that is not an ordinary complete `H` record in the NUL-delimited `git ls-files
--cached -v -z` listing. In particular, assume-unchanged and skip-worktree can hide
real tracked-file changes from ordinary porcelain status; neither is admitted,
even when the underlying file happens to be unchanged. The checker never clears
these flags, repairs a sparse checkout or refreshes the index to make it pass.
Git commands disable fsmonitor and optional locks, and still ignore inherited
GIT_* overrides. Broken Git metadata cannot be treated as an archive. After receipt
and canonical-context checks, the original source SHA and clean-checkout conditions
are checked again; a changed HEAD, dirty source or removed Git metadata rejects
instead of silently rebinding the old receipt to the new source.

These checks require a trusted Git executable/configuration and a quiescent
candidate/evidence workspace supplied by the supervising workflow. They are not a
filesystem transaction, a byte-by-byte attestation of ignored build outputs, a
hostile-Git sandbox, an anti-rollback service or a lease against mutations after
return. Archives continue to rely on the caller's independently verified source
binding; this checker does not reconstruct a Git commit from archive bytes.
Git's index flag definitions are specified in `https://git-scm.com/docs/git-ls-files`
and `https://git-scm.com/docs/git-update-index`.

`receipt_file_boundary.decode_object` rejects non-finite values from both special
NaN/Infinity literals and numeric tokens that overflow Python's float parser, such
as 1e999. Its parse_float handler tests finiteness at every nesting level, including
otherwise unused metadata. Finite JSON numbers retain their existing Python types;
this does not add arbitrary decimal precision or relax later integer/type checks.
The same decoder applies to the gap/module registries, canonical required contexts
and securely read receipt envelopes. File-read protections, receipt schema/context
requirements and the independent issuer-verification boundary remain unchanged.

Direct regressions in `tests/python/test_gap_closure_snapshot.py` cover replacing
registry paths between capture and reporting, public-summary revalidation, no
success projection on errors, no cached/mutable approval, nested numeric overflow,
finite-number compatibility, real Git assume-unchanged/skip-worktree counterexamples,
NUL-delimited names, disabled fsmonitor hooks, unchanged index bytes, broken or
removed Git metadata, and source/HEAD movement during actual detached receipt reads.
The existing tests in `test_gap_closure.py` retain their assertions; the closed
fixture's direct summary call now supplies its independent source identity.
Synthetic test envelopes are never uploaded as real qualification receipts.
Run both suites with `python3 -m unittest discover -s tests/python -p
"test_gap_closure*.py"` from the repository root.

This repair changes no gap state or external permission. `external_closed_with_receipt`
means the listed supplied envelope passed this structural/binding evaluation, not
that a live organization, Broker or independent reviewer approved it. Counts cover
the registered gaps only and do not erase the product exclusions or remaining work
products above. Complete current-head CI, independent review and protected live
verifier evidence are separate requirements.

## End-to-end receipt path and cleanup boundary

Documentation Control module 1.2.2 tightens the existing
`receipt_file_boundary.read_receipt(root, relative)` operation. Its returned
object remains a historical, structurally decoded envelope, not issuer identity,
external qualification, a filesystem lock or a deployment capability.

The reader no longer calls `root.resolve()`: resolving first would erase a
symlink in the selected root or its ancestors before no-follow opening could
reject it. Instead, an absolute path is traversed one component at a time from
`/`, retaining every directory descriptor through parsing and final validation.
A relative root is anchored once to the supervising process's working directory;
this does not follow root symlinks or reinterpret the selection after a later
chdir. A Path may already have normalized dot/repeated-separator spelling before
this function sees it; preserved parent components and double-root anchors are
rejected. The receipt's relative string still requires canonical spelling.

The selected root and every descendant directory must not be world-writable,
even with the sticky bit. An ancestor outside that selected root may be shared
only when sticky, allowing ordinary private temporary directories under `/tmp`.
This exception never permits `/tmp` itself as a world-writable evidence root.
Group ownership/permissions remain a trusted deployment-policy concern, not an
issuer-identity check. The code does not create directories, adjust permissions,
remove files or silently repair an unsafe layout.

The complete path has a 4,096-byte ceiling, each encoded component at most 255
bytes, and at most 64 directory components excluding filesystem `/`. These
preflight checks precede descriptor acquisition. A read retains at most 66 owned
descriptors (filesystem root, components, receipt). Missing directory-relative
open/stat or no-follow metadata support rejects without a permissive fallback.
The selected receipt remains a nonempty regular single-link file of at most
4 MiB with no world-write bit; symlink/hardlink/FIFO and oversized files retain
their previous rejection behavior.

Directory ownership slots are allocated before any file is opened. Payload
storage is preallocated to the captured size and filled with bounded short reads;
interrupted reads retry. A one-byte probe detects growth beyond the captured
size. Short reads cannot accumulate millions of retained per-read byte objects.
This bounds transport buffers and descriptor count, not the decoded JSON object's
full memory cost, total process RSS, I/O duration or the supervisor's other work.

File metadata and all retained directory/name bindings are validated both before
and after JSON decoding. File device/inode/mode/link count/UID/GID/size/mtime/ctime
must be unchanged. Directory device/inode/mode/UID/GID must remain the same;
changing an unrelated sibling is not falsely treated as a changed selected file.
Replacing a selected root/ancestor or an identical-byte receipt during decoding
rejects instead of returning the already decoded value. The returned value can
still become historical immediately afterward: a trusted quiescent workspace,
independent receipt authentication and live verifier remain necessary. Mount
namespace manipulation, hostile same-identity processes, atomic multi-file
snapshots and network-filesystem qualification are not established by this check.

Cleanup attempts each successfully acquired descriptor exactly once in reverse
order, including after open/stat/read/decode exceptions. A close I/O error does
not stop cleanup of other descriptors and suppresses a successful return. No
close is retried: on Linux, a retry after an error can close an unrelated reused
file descriptor. The fault tests call the real close before injecting the I/O
error, rather than claiming that a failed operation always leaves its descriptor
open. Process termination and asynchronous interpreter failure are not converted
into transactional cleanup guarantees.

`tests/python/test_receipt_file_boundary.py` adds 28 regressions covering root
and ancestor symlinks; writable root/descendant and sticky-ancestor distinctions;
path/depth/payload endpoints; short/EINTR reads; growing/truncated input; file,
root, ancestor and permission changes during parsing; acquisition/metadata/decode
and every close-failure position; descriptor inheritance/cleanup; unchanged
read-only inputs; and both external-gate envelope paths rejecting aliased roots.
The existing 57 gap-closure tests are retained unchanged. Run the new suite with
`python3 -m unittest discover -s tests/python -p "test_receipt_file_boundary.py"`
and the existing suite with the gap-closure command above. Synthetic local
fixtures never constitute live governance or Broker receipts.

Interface references: `https://docs.python.org/3/library/os.html`,
`https://man7.org/linux/man-pages/man2/open.2.html` and
`https://man7.org/linux/man-pages/man2/close.2.html`. These describe APIs, not
qualification of the selected source or the deployment environment.

## Detached evidence consumption

Keep source, qualification evidence and release binding as separate immutable objects:

```text
source candidate S -> built artifact B -> protected qualification evidence E(S, B)
                                     -> separate release record binds S, B and E
```

Do not commit E into S and then claim that a new source SHA was qualified. A protected qualification consumer may supply a separate gap-state projection and receipt root while preserving the candidate checkout:

```bash
python3 scripts/check_gap_closure.py \
  --repository-root "$CANDIDATE_CHECKOUT" \
  --module-registry "$CANDIDATE_CHECKOUT/docs/modules/module-registry-v2.json" \
  --gap-registry "$VERIFIED_EVIDENCE_ROOT/gaps.json" \
  --receipt-root "$VERIFIED_EVIDENCE_ROOT" \
  --expected-source-sha "$VERIFIED_CANDIDATE_SHA" \
  --expected-pull-number "$VERIFIED_PR_NUMBER" \
  --expected-merge-group-sha "$VERIFIED_MERGE_GROUP_SHA" \
  --json
```

These variables must come from the protected workflow's independently checked identity and verifier outputs. This command is the final structural cross-check, not the source of qualification. `gaps.json` is a detached result projection, not a second normative plan or a hand-authored success receipt. Ordinary source validation continues to accept explicitly open external gates and must not require broker credentials. A complete authenticated release-binding consumer and its deployment policy still require their own implementation/review; this path option alone does not create them.

## Exit contract

The documentation-control-plane milestone can close only when all of the following hold on one unchanged revision:

- `docs/` contains one registered current graph and no alias, old PLAN or manual exact-head file;
- all Markdown outside `docs/` is explicitly registered as entrypoint-only and links to a canonical `docs/` target;
- `legacy/` contains no development prose, media or build-system entrypoint;
- every ModuleManifest passes Draft 2020-12 validation;
- every registered module has exactly one generated technical guide whose required engineering topics, ownership, contracts, resource budget and verification IDs resolve to canonical authorities;
- every active C/C++ file has exactly one physical owner or one exact same-gap overlap exception;
- the configured CMake target/source/dependency graph matches module ownership;
- every direct production-source compilation is an exact, open-gap migration exception;
- generated views, documentation, repository, module, CMake graph, install, test and reliability gates pass on the same head;
- M0/M1/M2 state is closed only by evidence, never by editing prose or a status field.

Execution remains the sole venue-mutation authority throughout this work. CTP, XT/MiniQMT and LIVE remain unsupported/fail-closed; IB PAPER remains conditional on external exact-artifact qualification.
