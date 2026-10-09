# Neuron256 canonical CPU custody — 2026-10-09

## Observed numerical boundary

The original archived 1,024 feature rows came from qian-qi PyTorch 2.11.0+cpu.
On four already-inspected development rows, recomputing with the original
qian-qi model, tokenizer and original batch width 2 was **byte-identical** to
the archive: maximum raw-vector error 0.0.

On pocket4, PyTorch 2.11.0+rocm7.13.0 running its CPU kernels gave maximum
vector error 8.05128365755e-7, unaffected by CPU threads 1/2/4,
MKLDNN ON/OFF or single/pair batch width. This propagated to a
6.19888305664e-6 graph difference in the previous full path and remains
**FAILED** under the original 1e-6 requirement.

The original-CPU four-row tensor SHA256 is
d7d488f42ec93233244d4e5f83c005193de3479522d480132e94ab8d48373716.
The original Neuron256 checkpoint SHA256 remains
a2b3a9d6ccc2143399d488198799a7b4472dfdefc78234044d233f7a8e6795a7.
After source readiness recovered and the existing shared heavy-worker Guard
admitted a one-shot on original pocket4 gfx1150 ROCm, the baseline
graph/numeric/equal-mix/prior and one/two/four-width output differences
were ALL 0.0 for the canonical CPU four-row tensor. Original Landlock/
seccomp deny network and trading-bearer access. Actual read-only receipt:
~/.local/state/heptatrader/operator-evidence/effect-milestones-20261009T1410/
neuron-canonical-original-rocm-parity-20261009/result.json

This is a real engineering parity proof on four inspected DEVELOPMENT rows,
not market efficacy or a new held-out prospective result.

## Separate signed CPU producer and fail-closed GPU consumer

The research-only Neuron256 CPU producer requires exactly PyTorch
2.11.0+cpu without HIP/CUDA. The new custody contract binds the immutable
study/candidate/anchor, original source input file bytes and numeric row
digest, canonical frozen feature-text hash, source emitter/model/tokenizer
SHA256, generated_at before the original target, and the raw float32
(1,768) safetensors hash. A plan-pinned Ed25519 public key authenticates
the receipt; its private key must remain on the approved independent
CPU source host. Signing/verification reuse the already-installed system
Python role-witness helper. The GPU-side research worker now refuses
to load another local CPU encoder. It waits for the signed canonical
feature, validates before claiming a fixed slot, then verifies again
inside its no-network sandbox. Absent, corrupted or late features are
never replaced by local CUDA/ROCm-wheel CPU inference.

## Not-yet-closed activation dependencies

The separate cross-host finite pipeline still requires an independently
tested trusted transport. After the parent frozen campaign has published
original MID/BID/ASK input bytes, copy EXACT input to the canonical
source host, run its bounded offline encoder and sign, carry only the
signed receipt and tensor to the pocket4 canonical inbox, and bind
the independent GPU seven-bin prediction before the anchor+300s
decision-entry limit. Demonstrate missing-source, transport delay,
GPU shared-lease contention, signature substitution and restart cases
without replay or backdating.

Do not register or activate the 18 fixed post-Laya29 seven-bin future
anchors until independent CPU-producer source pinning, interhost
custody, role keys, registered future calendar, negative tests and
exact-HEAD regression all close. Only raw price bars from the
existing separate three-class campaign may be reused; never reuse
three-class forecasts, labels, efficacy results, broker requests
or expired input windows.

The selected rho=0.25 comes exclusively from the ALREADY-INSPECTED
256-row development check. It is a design choice, not independent
future data. Even a fully passed seven-bin price screen would not
qualify monetary returns, model promotion or trade authority.

**As of this source revision these are code/test drafts, not installed
services or a signed future plan.** Original Neuron256 weights, Laya29
alpha and outcomes, Value candidate, Execution owner and product
execution paths remain unchanged.
