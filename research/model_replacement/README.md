# Model replacement research

This package is separate from resident registration, promotion and trading. It
rebuilds selected PRICE rows from completed, hash-pinned jobs. Historical replay
results are never represented as predictions sealed before historical trades.

## Inputs and immutable outputs

Create a job snapshot from **all COMPLETED jobs at a stated snapshot time**, with
no score filtering. The input format is:

```json
{"schema":"heptatrader.model-replacement-job-snapshot.v1","created_at_ms":1793601743808,"period_sha256":"<64 hex>","jobs":[{"path":"/absolute/control/jobs/<id>.json","sha256":"<64 hex>"}]}
```

`dataset` verifies each job and its original dataset hash; it requires each
selected row ID to be reproducible. Its main population is the union of selected
job rows, not proof that every row received an applied optimizer update. Both
300s and 900s IDs in older jobs are verified; only the requested horizon is
included. Duplicate presentations do not increase the unique row count.

Archive pages are bound to the resident archive registry. Original files are
read by explicit pins, once per path to the longest requested prefix, checking
the hash at every shorter prefix boundary. All original prefix hashes, dataset
hashes and job hashes are retained. Run as the owner of the private source files.

```bash
python -m research.model_replacement dataset --jobs-manifest /research/jobs.json --output /research/dataset.json
python -m research.model_replacement split --dataset /research/dataset.json --output /research/split.json
python -m research.model_replacement run --dataset /research/dataset.json --split /research/split.json --model prior --output /research/prior
python -m research.model_replacement run --dataset /research/dataset.json --split /research/split.json --model linear --threads 2 --output /research/linear
python -m research.model_replacement run --dataset /research/dataset.json --split /research/split.json --model catboost --threads 2 --output /research/catboost
```

Every output file/directory must be new. The default split is 55% training, 15%
validation, 15% calibration and the remaining 15% test, grouped by anchor time.
Rows whose actual label observation or availability overlaps the next partition
are purged. Optional `--embargo-ms` adds a buffer; original label horizons and
seven fixed bin boundaries (-10,-3,-1,1,3,10 bps) are unchanged. No shuffled split
or test-selected threshold is used. Validation selects the linear checkpoint or
CatBoost iteration. Calibration fits only one bounded temperature. The test
partition is reported once, with class support and actual time coverage.

## Forecasters

Use the same `run` command with `--model chronos2_small` or `--model timestral_5m`,
plus `--model-path /local/checkpoint --revision <immutable-40-hex>`. Model loading
is local-only. `--mode lora` is supported only for Chronos and records actual
trainable/frozen parameter hashes, optimizer steps and the exported adapter.
Dependencies belong in a separate research environment, never the live venv.

Original contexts contain 6–24 timestamped quotes. The common derived sequence
uses a 10s last-observation grid, bounded to 30s quote age, without interpolation.
This is explicitly a model feature representation; repeated values are not new
market observations. Forecast features augment the same ten existing numeric
features and use the same train-only scaler and seven-class linear readout.

LoRA currently joins only contiguous **training-partition input histories**.
Series shorter than the actual 30-step horizon plus six past points are reported
and excluded, with an explicit failure if none remain. A mature final label is
never expanded into thirty invented future prices. The resulting forecasting
objective is documented separately from the seven-class readout objective.

## Restore and fresh observation

`model-state.json` persists the head/model, training-only scaler, class order,
calibration, inference-code hashes, data/split identities and cutoff times.
CatBoost also saves its hash-pinned native `.cbm`. Forecasters save exact base
file hashes and a complete LoRA receipt when applicable.

```python
from research.model_replacement.experiment import SavedPredictor, prediction_row
predictor = SavedPredictor('/research/linear/model-state.json', expected_sha256)
row = prediction_row(sealed_sample, instrument='EUR.USD', horizon_sec=300)
prediction = predictor.predict([row])
```

`sealed_sample` has the existing `neuron_learning.data.make_sample` shape. The
warm predictor refuses anchors predating model sealing or calibration maturity.
Output includes raw/calibrated seven-class probabilities, model-state identity,
sample IDs and actual creation time. It confers no trade authority. A new forward
plan must separately seal inputs, deadlines, attempts and outcomes.

Run contracts with `python -m unittest discover -s research/model_replacement/tests -q`.
Fixtures are synthetic and never count as market efficacy evidence. Local tests
exercise real NumPy training and restoration; actual CatBoost and model training
must also be exercised in the isolated target environment. Metrics describe
PRICE probabilities, not USD execution profit or a promotion decision.
