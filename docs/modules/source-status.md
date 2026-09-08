# Exact-SHA source status derivation

Status: CURRENT  
Applies to: repository HEAD and CI event revisions  
Implementation: `scripts/derive_source_status.py`, `.github/workflows/source-status.yml`, `docs/SOURCE-STATUS.md`  
Tests: `tests/python/test_source_status.py`

## Responsibilities

The source-status module converts repository-controlled validation predicates into one machine-generated result bound to the exact Git revision that was checked. It prevents `docs/gap-register.json` from acting as self-authenticating evidence merely because a committed field says `CLOSED_SOURCE`. It does not query organization teams, protected environments, runner assignments or a broker and therefore cannot close any external authorization gap.

## Public receipt contract

`python3 scripts/derive_source_status.py --output <path>` emits schema `heptatrader.source-status.v1`. The receipt records the forty-character source SHA, UTC generation time, overall source result, every validator command, exit code, output SHA-256 and bounded diagnostic tail, the derived state of each registered gap, and an authorization projection that keeps PAPER and LIVE false.

The registered source predicates cover documentation identity and depth, fresh CMake target ownership, gap-policy validity, the canonical IB PAPER profile, and the integrated source-gap closure verifier. A repository gap is `VERIFIED_CLOSED` only when every predicate succeeds in the same clean checkout. Any failure derives `OPEN_SOURCE`. External entries always derive `OPEN_EXTERNAL` regardless of source success.

The receipt ends with a canonical SHA-256 digest over its complete body before the digest field is added. Consumers verify schema, exact source identity, per-check results and digest. The CI workflow prints and uploads the receipt for the exact pull-request, push or merge-group subject; the generated file is not committed as reusable evidence for a later revision.

## State and persistence

Derivation is stateless. Before checks begin, Git `HEAD` and a clean worktree are captured. After all validators run, both are checked again. Validator output is captured in memory and represented by a digest plus bounded tail. The only persistent output is the caller-selected receipt file, normally under the runner temporary directory and retained as a workflow artifact.

`docs/gap-register.json` remains the reviewed policy and evidence inventory. It describes which gaps exist and which external receipts are required, while the derived source receipt says whether repository-controlled predicates passed now. An issue label, hand-edited JSON value or prior successful receipt cannot override a failing exact revision.

## Security and trust boundary

The module executes repository validators as unprivileged source checks and requires no broker credential. It treats the checked source as untrusted input and refuses a dirty or moving checkout. It never changes `production_authorized`, disarms a kill switch, provisions a session, writes a broker receipt or claims that the runner itself is trusted.

Source correctness is only one trust domain. The workflow artifact must be joined with authenticated governance, protected-runner and broker qualification receipts before any external mutation authority can change. A malicious or compromised ordinary runner can at most produce an untrusted source-status artifact; protected admission must verify workflow provenance and candidate identity independently.

## Failure semantics

Missing Git, malformed `HEAD`, dirty source, command startup failure, timeout, validator nonzero status, malformed gap policy, revision movement or validator-created files fails derivation. A failed run may still contain diagnostics, but its repository gaps are open and it cannot be interpreted as partial authorization. Unknown gap domains are rejected rather than assigned a permissive state.

## Observability

The workflow exposes the exact SHA, overall result, receipt digest, command identities, exit codes and output digests. Diagnostic tails are bounded to avoid unbounded logs and must not contain secrets because all source validators run without broker credentials. Artifact retention permits later comparison with the commit and workflow run.

## Test expectations

Unit tests create isolated Git repositories and prove that successful predicates close only repository-controlled gaps, a failed predicate reopens them, external gaps remain authorization blockers, receipt digests are present and a dirty worktree is refused. The live workflow additionally exercises the real repository validators against the event subject.

## Known limitations

The current JSON receipt is digest-bound but not cryptographically signed by a protected identity. Runner and workflow provenance are evaluated by the separate governance/qualification modules. The command list is source-controlled and intentionally narrow; adding a new critical validator requires updating this module, its tests and required-check policy in the same reviewed revision.
