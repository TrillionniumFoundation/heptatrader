# Exact future PRICE return bins

`price_return_contract` is an explicit, opt-in contract for future labels. Its
absence preserves the existing floating-point calculations, dataset schema,
receipt fields, prediction identities, qualification rules and saved results.
This change does not activate a release, retrain a model, rewrite a dataset or
place an order.

## The concrete boundary error

Both `10000.0` and `10001.0` are exactly representable in binary64. Their exact
midpoint return is one basis point. The original expression
`(10001.0 / 10000.0 - 1) * 10000` evaluates to
`0.9999999999998899`, so `bisect_right` assigns class 3. The literal
lower-inclusive boundary at one basis point assigns class 4.

The new arithmetic treats the existing stored binary64 midpoint values as exact
rational inputs. It does not reinterpret their decimal spellings. In
particular, the stored float `100.1` is slightly below the exact decimal 100.1;
the return from `100.0` to that float remains below ten basis points.

For positive midpoints `anchor = a/b` and `future = c/d`, the boundary test is

```text
10000 * (c*b - a*d) >= edge_bps * a*d
```

All quantities in this comparison are integers from `float.as_integer_ratio()`.
The denominator `a*d` is positive, so multiplication preserves the inequality.
Counting the passed, ordered boundaries gives exactly these intervals:

| Class | Basis-point interval |
|---|---|
| 0 | `(-infinity, -10)` |
| 1 | `[-10, -3)` |
| 2 | `[-3, -1)` |
| 3 | `[-1, 1)` |
| 4 | `[1, 3)` |
| 5 | `[3, 10)` |
| 6 | `[10, infinity)` |

No tolerance, rounding band or fitted threshold is introduced. The guarantee
covers ratios of the stored finite positive binary64 midpoints. Upstream quote
parsing and midpoint rounding remain separate operations.

## Explicit activation and identity

The v8 release-construction function accepts `exact_price_return_from_ms`.
Omitting it leaves the existing contract in effect. Supplying it constructs
`heptatrader.price-return-contract.v2`, records the current registration time,
requires an activation timestamp strictly later than registration, and pins all
participating source modules. The operator must install the prepared release
before that activation boundary; a delayed installation should prepare a new
future boundary. This source change performs no installation.

For an opted-in collection, only anchors at or after the frozen activation
timestamp are eligible. Histories can contain earlier causal observations.
Legacy seed labels are not converted: each new selected label is rebuilt from
the original source quote stream, under the existing maturity, endpoint,
availability and path-gap rules. The first endpoint at or after the requested
horizon must still fall within the existing five-second tolerance. The source
receipt hash, observed time, available time and horizon stay in the outcome
receipt.

The new receipt also stores the contract, canonical `float.hex()` values for
the anchor and endpoint midpoints, and the integer `return_bin_class`. The
reader recomputes the class, validates the anchor midpoint against its sample,
and checks that the unchanged legacy `target_scaled` regression value matches
the same endpoint. Missing witnesses, inconsistent horizons, altered classes,
mixed versions and pre-activation anchors are rejected.

New datasets use `heptatrader.neuron-dataset.v2`. Legacy readers accept only
v1, so they reject v2 instead of silently ignoring additional receipt fields.
The new reader supports both versions and requires their schema and contract
to agree. Sample identities and raw input quotes are preserved. The additional
receipt bytes change outcome and training-row identities intentionally.

## Consumer bindings

| Stage | Enforcement |
|---|---|
| `data.observed_receipt` / `data.label` | Optional exact receipt from the actual anchor and endpoint midpoints; default receipt is unchanged. |
| `resident_data.mature_anchor_indices` / `collect` | Selection and final labeling use the same receipt construction; activation filtering precedes the cap; legacy seed labels are not relabeled. |
| `dataset.rows_for_training` | Schema/version agreement, exact midpoint witnesses and class validation. |
| Training worker and `training_rows_for_job` | Frozen job and dataset contracts must match before any fitting. |
| v8 candidate verification | Job, candidate and training episode bind the same contract. |
| Supervisor and plan admission | Plan and reservation use the frozen job/candidate contract, never a newer controller configuration. Required source pins are checked. |
| Prediction and committed input | The contract is copied from the frozen plan; activation is checked before encoding. |
| Future `measure` | Plan, prediction and committed input agree; exact classification uses the actual source endpoint. |
| `promotion` | Every outcome, including censored records, must match the frozen contract and plan identity. Observed outcomes also revalidate endpoint witnesses and time bounds before assessment. |
| Additional forward attempt | The expected rule is rebuilt from the original job contract; the prior trial is not upgraded. |
| Closed parent training source | The original plan version and endpoint witnesses are checked; an exact label is not reconstructed from its rounded display value. |

The qualification rule includes the same versioned contract. Its original
thresholds, slots, horizon and alpha spending stay fixed. Older strict rule
validators reject the additional contract field. A v2 prediction also uses an
input identity over `(identity schema, raw state, return contract)`, while the
model still receives the original raw state. Older direct `measure` callers
expect `digest(raw state)` and therefore reject that identity before computing
a legacy label. The existing neural-input digests continue to describe the
unchanged raw model input.

The existing `return_bps` field remains a floating-point display value. Exact
outcomes mark it `ROUNDED_DISPLAY_ONLY_EXACT_RATIO_CLASS` and carry the midpoint
witnesses used for classification. A separate endpoint receipt binds the original
anchor and outcome source hashes, midpoint bits, observation and availability
times, horizon, slot, candidate and plan. Persisted observed results are checked
against the frozen input window, due time, five-second endpoint tolerance and
causal availability ordering. Censored results require their contract and plan
binding without inventing an unobserved endpoint. All models in a future
comparison use the same validated target class. Rechecking a stored receipt is
not an independent reread or authentication of its original source stream.

## Historical compatibility and scope

Previously frozen jobs and datasets retain the original contract. Loading a
new controller configuration cannot upgrade a pending legacy job or plan.
Existing retained-runtime handling continues to replay older source pins.
There is no conversion routine for old receipts: `target_scaled` alone does
not recover the original endpoint binary64 value. The original source endpoint
is required to construct a new exact receipt.

The change fixes boundary classification and makes its interpretation
traceable. It does not establish improved prediction accuracy, calibration,
trading profit, state representation, NDU control or any advantage from editing
model weights. The auxiliary historical ACT source keeps its original frozen
outcomes; versioned new closed outcomes are validated under their own plans.

## Verification

The new contract tests use synthetic quotes and temporary local files. They
exercise exact boundaries and adjacent representable values against an
independent `Fraction` reference; legacy defaults; producer/reader identity;
activation filtering; mixed-version rejection; source receipt retention;
job and plan binding; committed input identity; future measurement; promotion;
and reuse of closed outcomes. They construct no neural model and run no fit.

```bash
cd research/neuron_learning
python -m unittest laya_cell.tests.test_resident_price_return \
  laya_cell.tests.test_resident_price_input_v8 \
  laya_cell.tests.test_resident_plan_admission -v
```
