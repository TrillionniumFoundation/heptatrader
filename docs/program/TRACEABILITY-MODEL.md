# Development Traceability Model

Status: current normative
Applies to: capability, module, contract, test, gap, milestone and evidence registries
Verification: documentation-control-plane cross-reference checks and live qualification receipts
Authority: end-to-end development traceability

完整追踪链为：

```text
product capability
  -> providing/consuming modules
  -> versioned contracts and schemas
  -> source/build/deployment ownership
  -> module-specific generated technical guide and engineering coverage
  -> verification check IDs and fault/performance budgets
  -> gap/workstream/milestone repository implementation state
  -> exact source-head and merge-group integration evidence
  -> release artifact/SBOM/provenance identity
  -> protected external qualification
  -> deployment/runtime observation
```

## State scopes are not interchangeable

Registry 中的 `gap.state=closed` 或 milestone `state=closed` 只表示当前 repository tree 声称已经具备该实现、negative tests、canonical docs 和同树 evidence mapping。它不自动表示该 tree：

- 已合并到 default `main`；
- 已通过当前 exact source head 和 exact merge-group checks；
- 已取得 fresh non-author review；
- 已由 live no-bypass ruleset/merge queue 强制；
- 已生成或发布可重现 artifact；
- 已通过 IB PAPER 或其他真实环境资格；
- 已部署或正在安全运行；
- 获得 LIVE authority。

`milestone-registry-v1.json.policy.state_scope` 和每个 milestone 的 `integration_gate` 明确记录这一边界。生成的 roadmap 展示 repository implementation 声明、依赖诊断和未观测的 integration gate；它不是 GitHub、release、qualification 或 deployment 状态数据库。

## Evidence ladder

一个功能从代码存在到可运行能力，必须依次经过以下不降级阶段：

| Stage | Required evidence | What it may claim |
|---|---|---|
| `repository-implemented` | source + negative tests + canonical docs + registry links | implementation exists on that tree |
| `exact-head-verified` | required checks terminal-success on unchanged PR head | candidate head is internally verified |
| `independently-reviewed` | fresh non-author, domain-qualified review on same head | review acceptance for that exact head |
| `merge-group-verified` | same required contexts on exact merge queue revision | integration candidate verified |
| `merged-main` | protected merge receipt/no bypass | default-branch source truth |
| `artifact-reproducible` | dual clean build, install identity, SBOM/provenance | one exact distributable core candidate |
| `externally-qualified` | protected environment + real system evidence | exact artifact/config/environment qualification only |
| `deployed-observed` | startup/readiness/reconcile/monitoring evidence | that deployment is currently observed ready |

高阶段证据必须包含低阶段 exact identity；不能用历史结果、不同 SHA、不同 config、不同 binary、不同 SDK/harness、截图或手写 JSON补链。

## Capability derivation

任何 capability 如果缺少 module、contract、verification 或 maturity/qualification 映射，只能是 `planned` 或 `unsupported`。Capability 的有效状态取以下最小值：

```text
registered declared state
∩ exact revision implementation evidence
∩ release artifact capability ceiling
∩ environment qualification
∩ current deployment/readiness observation
```

例如：

- Simulator core 即使 repository implementation 为 `implemented`，在未合并/未发布时仍只是 candidate；
- IB PAPER 即使代码、workflow、mock tests 完整，也保持 `conditional`，直到同一 exact artifact/config/official SDK/harness/host/session 的受保护 receipt；
- PAPER receipt 永不推导 LIVE；
- CTP/XT negative stub 永不推导 venue availability。

## Module and document traceability

任何 current module 如果没有 owner、backup、state/concurrency/failure/resource contract，或者没有可追踪到真实源文件、构建目标和验证 ID 的完整技术指南，不得作为独立团队交付面。Guide 的章节存在不是充分条件；semantic documentation tests 还验证 manifest/profile 的关键工程内容确实进入生成文档，且没有 TODO/TBD 占位。

Source-size、ownership、build-target 和 migration exception 各自有独立生命周期。Functional gap 关闭后不能继续被滥用为永久豁免；accepted no-growth debt 使用唯一 `TD-SIZE-*`、owner、exit 和 review date，并在增长或低于阈值后自动失败。

## Live truth and immutable evidence

以下对象不得由 checked-in prose或 PR body伪造：

- current head/base/merge-group SHA；
- workflow check conclusion；
- review author/state/time；
- organization team member/maintainer/permission；
- ruleset bypass actors；
- CODEOWNERS parse/coverage；
- release/tag/attestation；
- protected environment approval；
- real Broker/PAPER callback、account/session和host identity。

这些状态由 GitHub API、release evidence、qualification verifier 和 runtime evidence读取。Repository JSON 可以定义期望政策和 verifier，但不能把期望描述为已安装事实。

## Naming and references

生成视图只展示注册表结果，不创建新状态。PR 描述、issue、dashboard、incident、release note 和 qualification receipt 必须引用同一 module/contract/capability/gap/check/reason code ID，不能发明平行命名。Mutable head/check结果不写死在长期 normative 文档；receipt 必须绑定 exact identity 和原始 API/evidence digest。

## Executable declaration progress projection

Documentation Control module 1.3.0 implements `program_progress(milestones, gaps)`
and `load_program_progress()` in `scripts/generate_documentation_views.py`.
`roadmap()` renders the same projection into the existing `MASTER-ROADMAP.md`;
no second roadmap, gap authority or live-status database is introduced.
A machine-readable observation is available without modifying repository files:

```bash
python3 scripts/generate_documentation_views.py --progress-json
```

This mode is mutually exclusive with `--check` and `--write`. It reads only the
canonical milestone and gap registries, once each. The normal generation modes
still validate/regenerate their existing output set; they do not contact GitHub,
run a Broker campaign or consume qualification credentials.

### Data contract and dependency interpretation

The JSON identifier is `heptatrader.program-progress.v1`. `scope` is always
`registry-declarations-only`, and `grants_qualification` is always false.
`registered_gap_counts` counts the four original declaration states separately;
`registered_open_gaps` lists every registered non-closed gap, with ID, title,
state and owning milestone. These counts are not a product completion percentage.

Each `milestones` row retains `declared_state`, `depends_on`, `exit` and
`integration_gate`, and adds `open_gap_ids`, `unresolved_prerequisites` and
`declaration_diagnostics`. An unresolved prerequisite is any transitive ancestor
whose recorded state is not closed, or which owns a non-closed registered gap.
The dependency graph is checked before any report is returned. A closed node
with its own open gaps receives `closed-with-open-gaps`; a closed node with an
unresolved ancestor receives `closed-with-unmet-prerequisites`. Both diagnostics
can apply. Neither rewrites the input declaration or erases historical work.

For example, in a synthetic chain A -> B -> C, a non-closed A remains an
unresolved prerequisite for C even when B and C are both recorded closed.
A diamond dependency reports A once. This is conservative declaration analysis,
not a claim that every prerequisite represents an already executed acceptance
stage. Changing dependency meaning requires a reviewed registry/contract change,
not silently suppressing the diagnostic.

Zero open gaps and zero diagnostics never establish success. The ten separate
`observations` dimensions -- exact-head checks, independent review, merge-group
checks, main integration, artifact reproducibility, external qualification,
release eligibility, deployment readiness, PAPER and LIVE authority -- always
remain `not-evaluated`. This means unknown to this generator, not that an external
system failed or that a deployment is absent. Caller-supplied success flags or
receipt-like extra metadata cannot promote these dimensions. The existing receipt
checker and its protected independent verifier boundary are unchanged.

Module `excluded_scope` remains authoritative independently of the gap counts.
An excluded capability neither disappears when registered gaps close nor becomes
a promised product requirement merely by appearing in the registry. The current
upgrade plan determines which bounded capabilities enter the next work package.
The generated view links to both sources and displays each milestone's existing
integration gate instead of dropping that field.

### Validation, consistency and resource boundary

The parsed-input projection admits 1..256 milestones and 0..4,096 gaps. IDs are
1..64 ASCII characters matching `[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*`. Dependency lists
are bounded to 256 entries; duplicate, unknown and self dependencies, cycles,
duplicate milestone/gap IDs, orphan gaps and unknown states reject. Titles and
integration gates contain 1..4,096 characters of trimmed single-line text; exit
lists contain 1..64 unique such strings. Controls, Unicode line separators and
unpaired surrogates reject. Markdown table metacharacters are escaped for display.
Milestone/gap/dependency iteration order does not change the canonical report;
exit conditions retain their explicitly declared order.

The file loader caps each registry at 1 MiB before JSON decoding, checks the
original registry schema identifier, and rejects duplicate JSON keys, invalid
UTF-8, non-finite constants and overflowing float tokens, including nested unused
metadata. Parser recursion failure is a rejection. A missing, malformed or
oversized file yields a nonzero CLI exit, a diagnostic on stderr and no JSON
report on stdout. Accepted declaration input yields exit zero, which means only
that this projection was produced, not that its gaps or external gates closed.

Validation and report construction use private selected values; result mutations
do not modify input records or confer cached approval on later calls. No valid
prefix report is returned after a rejected suffix. Inputs must not be mutated
concurrently. Reading two files once is not an atomic multi-file filesystem
snapshot, Git attestation, secure receipt-path traversal or lease against later
changes. Use a trusted quiescent checkout. Count/byte limits bound this input
surface, not total process RSS, a hard latency SLA or every generator operation.
The existing multi-output `--write` driver is not a transactional filesystem
publisher; failure must never be interpreted as a fully regenerated tree.

### Requirement-to-assertion map

Direct tests are in `tests/python/test_program_progress.py` and are discovered by
the existing Python test lane; existing tests and workflow gates are not weakened.

| Requirement | Direct regression |
|---|---|
| Preserve unresolved ancestors through recorded-closed nodes | `test_closed_chain_preserves_unresolved_ancestor` |
| Surface local open gaps and both diagnostics | `test_closed_with_own_open_gap_is_visible_to_descendants`, `test_both_diagnostics_are_retained` |
| Independent graph closure and input-order invariance | `test_seeded_graphs_match_independent_oracle_and_permutations` |
| No authority from zero gaps or caller success claims | `test_zero_open_gaps_never_creates_verification`, `test_caller_claims_cannot_promote_observations` |
| Strict graph/identity/state validation and exact bounds | `test_cycle_in_disconnected_component_rejects`, `test_exact_graph_and_gap_capacity` |
| Read-once observation without cached or mutable approval | `test_each_registry_read_once_and_projection_does_not_cache`, `test_mutation_of_result_does_not_modify_or_cache_inputs` |
| Invalid JSON/numbers/Unicode/size never publish a report | `test_duplicate_keys_and_nonfinite_nested_metadata_reject`, `test_exact_byte_limit_and_over_limit`, `test_cli_unpaired_surrogate_rejects_without_output` |
| Actual CLI, generated-view drift and current registry binding | `test_read_only_cli_and_no_other_registry_dependency`, `test_generator_check_write_and_drift_paths_use_projection`, `test_current_registries_and_checked_in_roadmap` |

The seeded test compares 256 finite acyclic fixtures against a separately written
repeated-traversal oracle. This is behavioral regression evidence, not independent
source approval or proof of every possible graph. Complete current-head CI,
review, protected governance and exact-artifact PAPER qualification remain
separate requirements. No runtime/C++/OMS implementation, external gap state,
trading permission or deployment is changed by this reporting mechanism.
