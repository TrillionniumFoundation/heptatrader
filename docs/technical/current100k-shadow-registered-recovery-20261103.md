# Current100k registered-window recovery: 2026-11-03

This is an operational recovery record for an existing prospective shadow study.
It does not report a model improvement, a completed future evaluation, a production
selection, or authority to place orders. Market inputs and monetary records remain
on the private host.

## Preserved study

The operator handoff identifies the registered plan by ID prefix `c6a85906` and
plan-file SHA256 prefix `f759b457`. These are explicitly abbreviated identifiers;
the complete identities remain in the private registration and activation records.
The existing `plan.json`, `registration.json`, and `activation.json` retain their
original file hashes. Recovery must not register another window or replace them.

| Boundary | Unix milliseconds | UTC |
|---|---:|---|
| Input start | 1793705453494 | 2026-11-03 11:30:53.494 |
| Input end | 1794915053494 | 2026-11-17 11:30:53.494 |
| Outcome end | 1795001453494 | 2026-11-18 11:30:53.494 |

The 14-day input interval and one-day settlement grace are unchanged. Missed
requests remain missed; recovery does not manufacture historical predictions or
renew original request deadlines.

## Operational checkpoint

The original native CLI precheck returned `PASS` at `1793705149072`
(2026-11-03 11:25:49.072 UTC). The operator's subsequent checkpoint reported provider
HTTP 429 responses on the original 120-second cycle and no new original OPEN.
Native precheck success therefore does not demonstrate sustained input production,
pre-OPEN prediction coverage, or future model performance.

During this repair the research unit was intentionally stopped for maintenance.
Its HIP allocated bytes had returned to zero and its heavy-worker lease had been
released. The three registered-study artifact hashes were unchanged. These are
the operator's handoff observations, not a claim that a later deployment or
upstream recovery has already completed.

## Repair boundaries

The source reader retries only the two recognized append/capture races, within
the existing request deadline. A busy snapshot cannot supply stale inputs for
inference; real source, guard, and identity failures keep their existing behavior.
See [the pre-open reader contract](current100k-preopen-research-20261103.md).

A durable prediction alone no longer earns an observed sample. The evaluator
requires the original successful terminal, original attempt deadline, and exact
pre-OPEN binding. Interrupted, failed, or late attempts remain in the denominator
without receiving positive credit. A separate strict review writes its own
assessment and failure receipt while preserving the original reports.

For a future unregistered study, the registration gate requires an original,
validated successful RESPONSE and DECISION, rather than a pending request or a
provider error. This gate is skipped for the already registered plan above.

A registered-study continuation must validate its original activation and derive
a separate predictor configuration. Only the approved runtime root and runtime
file inventory may differ from the activated predictor configuration. The original
configuration and activation are preserved; encoder arithmetic, heads, source,
request controls, plan, and statistical rules are unchanged.

At the fixed outcome end, an unreadable or incomplete source cannot overwrite
the last live assessment with a zero denominator. The live evaluator publishes a separate
immutable `FINAL_UNAVAILABLE_NO_SELECTION` receipt, records the actual exception
and check time, and retains a hashed copy of the previous complete assessment.
Unknown command counts and coverage remain null. Restarting consumes that receipt
without rerunning or re-dating it.

If no final report is published, the supervisor has a 120-second report-computation
budget after the fixed outcome end, then terminates in the same unavailable state.
That budget is not another input or settlement window. A successfully computed
assessment keeps the original support and comparison rules. Every recovery and
failure path retains `financial_candidate_selected = false`.

Supervisor exit status records that the supervisor stopped and attempts cleanup
of both children even if one cleanup fails. An unconfirmed child exit is retained
as unknown or still running; it is not reported as a successful release. The
explicit final-unavailable state survives this cleanup path.



## Primary research context

The [MIT CSAIL authors' weight-space programming report](https://rikiyat.github.io/blog/weight-space-programming/)
(November 2, 2026) reports direct Qwen3.5-0.8B weight edits that reduced reasoning
loops and improved benchmark results under the stated thinking and sampling
settings. The agent used bounded practice forward passes without gradient
training. Their Gemma 3 270M experiment did not establish a capability improvement.
These findings motivate mechanism-based candidate edits and independent outcome
checks. A change in representation separation in this study remains a development
diagnostic and does not establish better predictions.

## Separate development-layer diagnostic

The operator independently reviewed diagnostic commit `5dd5961` and the private
summary with SHA256 prefix `9619027a`. It used the same 54 already viewed
development rows and examined 22 model blocks plus embeddings and final
normalization; the final-layer parity check reported zero difference. Block 12's
MLP common direction was the most conspicuous finding. Halving that direction
reported only a 0.22% change in final-representation separation and did not
establish a prediction-quality improvement. This is diagnostic evidence, not an
additional frozen candidate or a result from the prospective window. Its extra
diagnostic scripts are preserved in the published source tree, outside the
443-file runtime inventory for this already registered window.

## Verification scope

Integration verification covers the existing shadow, pre-open reader, launcher,
supervisor, mocked HIP-encoder, and attribution-cache suites, plus the new
capture-retry, continuation, and finalization fault regressions. It does not rerun
the full historical attribution suite or a GPU/model benchmark. Passing these
tests establishes implementation behavior, not prospective financial results.


## Concurrent-history integration

The combined source retains both concurrent committed histories:

- `371320c44d0f961c92eb8ea8dfdd8c42b9d75a10`: independent strict-review source-loss
  closeout, the original terminal checks, and the layer diagnostic artifacts.
- `faddaa87848c551aa166976e44846735402c9432`: explicit continuation consumption and
  the narrow runner deadline-adapter check.
- `859b75639a83d689d239f44c8829481a580a33e4`: live fixed-deadline failure closeout,
  CPU settlement reconciliation, fixed financial-event cutoff, and the stricter
  continuation scope and numerical/statistical protections.

An independent strict review retains a copy of the existing registration before
opening source-dependent readers. Source loss withdraws current verification
credit while preserving known command identities and a clearly marked count
lower bound. Actual unavailable source counts and coverage stay null in
`current_source_totals`; zero credit does not assert that the real population is
empty. Its prior complete reports remain archived without timestamp changes.
The original live assessment and all three original registration files remain
outside this independent review's output directory.

Continuation parses and hashes the same bounded file bytes, retaining duplicate
key and nonfinite-JSON rejection. The runner allowance admits only the reviewed
request-deadline forwarding adapter; all other runner semantics are protected.
The original six statistical functions, the evaluator's remaining methods, the
whole readout/score/plan AST outside the exact existing output-directory adapter,
and the exact financial-cutoff file pair remain protected. The 12-path allowlist
is not expanded to package the new diagnostic scripts or independent test file.
Existing continuation receipts are not rewritten to change their purpose.

The merged shadow, strict-source-failure, continuation, and supervisor suites
passed **116 tests in 35.924 seconds**. This includes all 10 incoming source-loss
cases, fixed-cutoff and settlement cases, continuation negative cases, and both
child cleanup paths. A source comparison additionally passed the actual runner
and readout AST guards against the original `6c0e18137` source; the six statistics
and remaining evaluator methods matched exactly. The layer summary remains
byte-identical to SHA256
`9619027a704a7dd552c85d3045a97803a14d7094af95cd56ed456a8ad5952b2b`.
No model loading, diagnostic rerun, GPU benchmark, private service write, or
prospective efficacy claim was part of this integration verification.
