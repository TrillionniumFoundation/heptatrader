# Frozen PRICE feature snapshots

An admitted v8 training job already computes the frozen inputs to the live PRICE
scorer. Those inputs used to disappear at the end of its PRICE phase. Repeating
the encoder later just to investigate a readout or compare bounded weight edits
wastes computation and may require another scarce inference window.

The optional `FROZEN_PRICE_SINGLETON_FEATURES_V1` profile saves those existing
singleton, eval, no-grad FP32 inputs. It performs **zero extra encoder forwards
and zero extra optimizer steps**. It is disabled by default. The original PRICE
and ACT computation, model selection and resource admission still apply.

## Enable in a new pinned release

Use the existing v8 release builder with `price_feature_snapshot=True`. It adds
the declaration and SHA-256 pins for the snapshot module, training worker,
scheduler and v8 policy. A missing pin, unknown profile or non-v8 objective is
rejected. This change does not modify an existing installation or running job.

```python
config = resident_training_v8.configuration_for_release(
    config, runtime_directory,
    closed_action_source=closed_action_source,
    price_feature_snapshot=True,
)
```

The scheduler copies this declared configuration into the immutable job. At the
end of PRICE, the worker copies only the already-cached no-grad singleton inputs
to CPU-owned storage. A two-row training batch or grad-enabled singleton cannot
fill a missing eval singleton. Source parameter metadata, storage, dtype, shape,
strides, modes and row state/question identities must still match the cache.

## Published artifact

After normal candidate verification, the task output can contain:

| File | Contents |
| --- | --- |
| `price-features.safetensors` | `features` in float32 and `marker_mask` in bool |
| `price-feature-snapshot.json` | Base, parent, input, job, runtime and numerical provenance |
| `candidate.json` | Hash-bound `price_feature_snapshot` receipt |

The archive is limited to 64 rows, 8 MiB of serialized tensors and a 64 KiB
manifest. It stores input hashes, not another copy of raw quotes or target
labels. The binding includes ordered row IDs, state/question hashes, original
PRICE input IDs, dataset SHA, parent adapter/manifest identities, base identity,
full runtime inventory digest and immutable job digest.

Both files are created exclusively, made read-only and fsynced before the
candidate manifest becomes the commit record. Existing files are never
overwritten. A storage failure records `NOT_STORED` without discarding the
trained candidate. Partial files are not valid snapshots.

## Offline use and boundaries

Call `binding(job, verified_parent, base_id, original_rows)` using independently
verified source identities, then `load_snapshot(directory, receipt, binding)`.
The loader checks bounded, non-symlink regular files from the same open file
descriptor, file identity stability, hashes, strict JSON, schema, shapes,
dtypes, finite values, nonempty masks and the source binding. Safetensors is
decoded only from the verified bytes.

Replay each row through the scorer separately to preserve the original batch
shape. Numerical equality must be independently checked after changing Torch,
device, precision or other execution settings. This archive is not a substitute
for encoder inference after an upstream weight edit, a new input or a changed
input profile. It does not supply ACT's PRICE-dependent features.

These are the original training rows. Reusing them does not create a holdout or
prove future trading value. The snapshot is derived research data and conveys
no training, evaluation, model-selection or trading authority.

## Verification

CPU tests compare original full singleton forwards to exported/reloaded scorer
replay before and after a scorer edit. A full synthetic v8 episode with the
profile enabled preserves every gradient receipt, all PRICE and ACT optimizer
steps, the final adapter bytes, calibration and the number of encoder calls.
Additional checks reject changed sources, input substitutions, missing singleton
features, malformed manifests, corrupted tensors and partial archives. The
existing explicit HIP-window test remains separate; no live GPU job was started
by these tests.

From the repository root:

```bash
PYTHONPATH=research/neuron_learning:research/neuron_learning/laya_cell/tests:tests/python \
  python -m unittest test_resident_price_feature_snapshot test_resident_price_cache \
  test_resident_price_parent_cache test_resident_training_v8 test_resident_owner_packaging
```
