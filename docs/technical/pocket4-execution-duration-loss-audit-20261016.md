# Pocket4 PAPER execution-cost duration audit — 2026-10-16

## What was actually verified

This study reads the resident **Pocket4-only** OMS journal through the original
`scripts/hepta_pnl_accounting.py` decoder and the frozen account-period file.
It creates no orders, subscriptions, model candidates, trading thresholds or
promotions. Evidence is stored only on the local Pocket4 machine:

`~/.local/state/heptatrader/operator-evidence/pnl-duration-clean-main-20261016/pnl-duration.json`

The source records **34,519 committed journal events**. The strategy period and
journal digest match the concurrently captured PnL observer snapshot; execution
counts, broker commissions, positions and realized PnL all reconcile exactly.
The report is not account NAV and excludes financing, personal taxes and
non-execution costs. No new broker data request occurred during this audit.

Latest matching execution-ledger/PnL read at the audit:

| Instrument | Closed roundtrips | Gross USD | Execution commissions USD | Commission-net USD |
| --- | ---: | ---: | ---: | ---: |
| EUR.USD | 357 | -965.741130 | 1,428.097729 | **-2,393.838859** |
| BTC.USD | 4 | +58.6967243475 | 235.861341 | **-177.1646166525** |
| Both | 361 | -907.0444056525 | 1,663.959070 | **-2,571.0034756525** |

The classifications below use **first original OPEN command send timestamp to
last original CLOSE command send timestamp**, not broker-attested fill duration.
They describe only the completed trades; neither the bucket nor the final return
was observable to an entry decision at the time of entry.

## Real EUR/USD source-linked ex-post decomposition

| Original send-to-send duration | Closed episodes | Gross USD | Commissions USD | Net USD |
| --- | ---: | ---: | ---: | ---: |
| <5 minutes | 66 | -1,458.974500 | 264.015712 | **-1,722.990212** |
| 5–15 minutes | 147 | -1,233.839440 | 588.032262 | **-1,821.871702** |
| 15–60 minutes | 127 | +1,860.757870 | 508.035442 | **+1,352.722428** |
| >=60 minutes | 17 | -133.685060 | 68.014313 | **-201.699373** |

This is a causal *accounting* reconstruction, **not causal evidence** that forcing
a minimum holding period would improve returns. Stops, quote context and
model reversals are endogenous to the actual duration. Retrospectively
selecting the 15–60m winners would have hindsight and survivorship bias.
Do not turn these bucket boundaries into production stop/hold rules.

Opening-side breakout and complete strict Decimal arithmetic are preserved in
`pnl-duration.json` under `instruments.EUR.USD.duration_side.groups`.
BTC: 3 longer 15–60m long episodes had +58.6954744725 gross, $235.020333
fees and -$176.3248585275 net; one <5m episode was also net negative.

## Specific next investigation, preserving genuine chronology

1. Preserve resident PAPER decisions as-is. Link each original opening
   `command_id`, its *pre-entry* model request, input quote, risk preview,
   spread and precommitted forecast with its eventual closing `command_id`,
   close trigger reason and broker fees. Keep missing/unknown reasons.
2. Diagnose the original `MODEL_REVERSAL` decision cadence and quote-age
   distribution compared with `MAX_ADVERSE_MOVE` and `HALT_REDUCE_ONLY`.
   A source-bound exit-trigger reason is necessary to attribute the observed
   short-trade losses; do not infer a reason merely from duration.
3. Freeze any alternative **shadow-only** entry-confirmation or reversal
   hysteresis rule *before* the next eligible forward market window. Preserve
   every scheduled opportunity including abstentions, risk-stop overrides and
   missed/late inputs. No hindsight fitting to the 357 completed episodes.
4. Compare actual driver fills and conservative independent counterfactual
   transaction costs, where observable. Unobserved quotes/fills remain
   censored; simulated next-bar fills are not broker executions.
5. Report true forward opportunity count, realized exact commission-net PAPER
   PnL, maximum loss and risk outcomes, and an unchanged no-trade benchmark.
   Do not promote to order authority absent a positive independently observed
   economic outcome. Historic Brier improvements alone are insufficient.

## Implementation and validation

- `research/pnl_cost_decomposition.py`: read-only complete original OMS replay,
  new grouping by opening side and original-send duration; strict reconciliation
  to the closed-trade totals, no action authority.
- `tests/python/test_pnl_cost_decomposition.py`: 8 existing tests.
- `tests/python/test_pnl_duration_side.py`: 7 additional boundary,
  missing-time, arithmetic, invalid-value and side tests.
- **Pocket4 on latest-main-isolated worktree:** 15/15 relevant unit tests
  passed, documentation check passed, read-only actual full-ledger replay passed,
  all six critical IB/Execution/PAPER/crypto/Laya services remained active.
- The source-only result on GitHub is a proposal; **the production trader and
  pinned model are not altered**. The Pocket4 main working tree's pre-existing
  uncommitted changes were not modified.

## Follow-through: original CLOSE reason receipts and real active source version

Follow-up real Pocket4 readback, **2026-10-16 ~07:15 PDT**:

- PR #366 (original command-bound CLOSE reason, with the diagnostic-failure settlement fix) and PR #367 (balanced Laya v2 action research) are already merged into GitHub main. There is no need to reimplement or re-merge them.
- The **active** EUR/USD systemd unit does **not** execute the repository main Python driver directly. It executes pinned `heptatrader_attributed_driver.py` with manifest pointing to the older base driver SHA256 `c1e4339f13a466759186bcf63ece5bff37b10c51157fdbd7bfe53402e0f2943d`. Its source predates the merged CLOSE receipt feature. Its original decision-attribution producer was healthy (2,427 written, zero drops/errors in the inspected window), but captures OPEN-bound model requests rather than driver-proven CLOSE reasons.
- The merged, verified main driver base (SHA256 `8f37afb687d84996f10be9b357186dee8cd435f0b44ea200ced239e01c2edff8`) differs from the old pinned base in the original-CLOSE intent diagnostics and related callsites; a restart of the live owner has **not** occurred.
- The new optional `--fx-driver-state` / `--btc-driver-state` evidence correlation is read-only and strict: actual command identity, all original broker execution IDs, broker order, terminal flat fill, nonpartial close, bounded fill quantity / average price, chronological quote + trigger + original send, supported original driver-issued reason. A mutable `last_exit_reason` is never accepted.
- A Pocket4 run against the original OMS journal and current driver snapshots proved full PnL period/digest/accounting parity, and measured **0/357 EUR/USD** and **0/4 BTC/USD** closed roundtrips with an eligible original CLOSE reason. All **361 remain UNKNOWN** in the captured historical period; old history is never rewritten or guessed. Evidence root:
  `~/.local/state/heptatrader/operator-evidence/pnl-close-reason-next-20261016/close-reason.json`.
- Integration using the real merged driver `freeze_exit_trigger` + `record_correlated_close` proved a synthetic pre-send MODEL_REVERSAL intent can match one unique broker execution. Subsequent mutable state changes cannot change this attached reason. No fake or real Broker order was sent. After adding exact fill size/price crosschecks, **36/36** focused Pocket4 tests passed.
- A **nonactive** FX candidate was staged with byte-pinned original attributed wrapper and observation component, plus the new main base at:
  `~/.local/share/heptatrader/decision-attribution/candidates/fx-close-reason-20261016-v1/EUR.USD/`.
  Candidate manifest SHA256 is `ca5a8b42c2d2b77ed952fb8a99205dc9d8427ce073975ac7dbc45601c163633f`; all three module file hashes agree with the manifest, permissions 0700 directory / 0600 files. No production unit points here and no service was restarted. This is source/materialization evidence, **not a broker-involved rollout**.

The remaining *real* entry-to-exit provenance gate is a controlled future FX driver release cutover under proven flat/no-new-entry owner conditions, correct pre/post service epoch, and rollback. The next genuine future CLOSE should write a bounded exact receipt; only thereafter can independent OMS matching attribute MODEL_REVERSAL vs MAX_ADVERSE_MOVE, and only a forward economic evaluation can justify any change to exit rules. **Do not infer an automatic 15-minute hold from ex-post duration buckets.**

## October 16 08:18 PDT continuation — guarded candidate not cut over

Pocket4 direct SSH was healthy, and resident Gateway / Execution / FX PAPER /
BTC PAPER / Laya trainer services all reported `active`. Exact current FX
user-unit `InvocationID=45b83cd26dd94a0bbe7a4d73b57c1dc8`,
`MainPID=4140608`, `RefuseManualStop=yes`.

- The active attributed-driver bundle still pins old base driver hash
  `c1e4339f13a466759186bcf63ece5bff37b10c51157fdbd7bfe53402e0f2943d`.
- Previously staged isolated FX candidate
  `~/.local/share/heptatrader/decision-attribution/candidates/fx-close-reason-20261016-v1/EUR.USD/`
  pins newer base `8f37afb687d84996f10be9b357186dee8cd435f0b44ea200ced239e01c2edff8`,
  wrapper `be66ae26e55169b7f9d2df733b377ade391e01a8805a25b12932d4846f2d8b77` and
  observer `adfb8bf73d7cacd3305807ca168ed8c8355164bb8a8ac14512f3abbe2605a9fb`.
  The **actual candidate manifest explicitly states `trading_behavior_changed: true`**
  and `STAGED_NOT_ACTIVE_OR_AUTHORIZED_FOR_TRADING`; treat this as a consequential
  change, not simply harmless observability, until exact branch/behavior parity
  is proved.
- The original FX state was `position=0`, `pending=null`, but
  `halt_new_entries=false`. Flatness alone does **not** guarantee a safe
  restart while a resident driver can open a fresh position between checks.
- Current snapshot period strategy commission-net was still
  **-$2,571.0034756525** USD, both asset ledgers flat; all **361 past CLOSE
  reasons still UNKNOWN** and may not be rewritten from `last_exit_reason`.
- After checking the isolated Pocket4 worktree against the current candidate,
  **89/89 tests** passed (`tests.python.test_paper_driver`,
  `tests.python.test_pnl_close_reason`,
  `tests.python.test_pnl_duration_side`,
  `tests.python.test_pnl_cost_decomposition`). Existing main's three dirty
  tracked user files stayed unmodified. No production restart, order send,
  policy change or model deployment was performed.

### Exact remaining release step

Create/verify the owner-controlled **no-new-entry** fence, reconcile the
original FX order/position snapshot *and* pending sends under the same owner
before transition, record existing PID and invocation epoch, prove branch-parity
of all live order actions, atomically point the existing attributed-driver
unit to the pinned candidate, restart through the service's supported owner
maintenance path (never bypass `RefuseManualStop` blindly), verify new PID,
actual frozen CLOSE receipt and durable original broker execution identity;
otherwise restore the pinned original unit before reopening admissions.
Until then, status is `STAGED_AND_TESTED_NOT_PRODUCTION_CUTOVER`.
