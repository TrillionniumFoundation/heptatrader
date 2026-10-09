# Neuron256 seven-bin real-future bridge — fail-closed operations

Status: **source and finite-control implementation**. Never confuse unit
installation, a synthetic transport smoke, or regression with a live observed
future market outcome. No broker order or historical request is added.

## Data, calendar and provenance

- Original parent is the already signed 24-slot three-class IB PAPER price
  campaign `da06786816f632ba53c6bd2de822f786408135cef93195a02c7c863e9aa4de16`.
  It alone queries original MIDPOINT/BID/ASK and the eventual original outcome.
- Exactly **18** parent slots survive the Laya29 terminal time:
  2026-10-09 **15:10 and 17:10 UTC**, and 11:10, 13:10, 15:10, 17:10
  UTC on October 12, 13, 14 and 15. Do not invent, extend, move, replay,
  replace or re-request these windows. The first two are October 9 at
  23:10 and October 10 at 01:10 in Beijing time.
- Fresh source input must be complete, causal and verified against the
  original signed parent's *actual* 9101/9102/9103 wire receipts and input
  file. Copy input **byte for byte** from the existing parent collector;
  never use the old three-class probabilities, labels or Brier.
- Only **after** the original Laya29 fixed terminal,
  2026-10-09 21:41:49.913 UTC+8, and after the original evaluator
  has durably written its matching REJECTED qualification and all 86
  terminal outcomes, may the new registry issue a signed trial-1
  seven-class plan. The frozen candidate's rho=0.25 was selected on an
  **already inspected** development population. This is not unseen
  validation, and the graph, experts and source training are unchanged.
- Original checkpoint, CPU encoder assets, qian-qi CPU code inventory,
  model/feature serializer, pocket4 GPU runtime, parent plan, source period,
  CPU Ed25519 public key and role-witness trust are pinned before trial.
  No regenerated signature may supersede a previously failed request.

## Bounded independent execution

The qian-qi user-only CPU service polls through existing authenticated SSH
and **pulls** the original parent proof. The source host independently
reconstructs the original 12 completed MID/BID/ASK inputs and rejects
partial/tampered/late/foreign proof. A qian-qi-only **canonical**
PyTorch `2.11.0+cpu` job, in the previously verified offline Landlock
sandbox, computes one (1,768) raw feature and signs an Ed25519 receipt
binding the exact immutable original bytes, source text, model, runtime,
anchor, plan and tensor hash. The CPU signing private key never leaves
qian-qi. The CPU worker cannot call IB and never sends broker orders.

**Critical numeric preflight:** canonical CPU singleton batching did **not**
reproduce the original checkpoint readout under 1e-6: one inspected row had
raw error 1.4528632164e-7 and graph output error 2.5629997253e-6.
Its failed receipt is preserved. The corrected CPU encoder restores the
original two-input compute shape: a real fresh source input is paired only
for CPU computation with a frozen, already-inspected, **unlabeled** 25-feature
companion. The companion string is SHA256-pinned
`34fb4b19fda875c297fc12e497cffb714a5c4de8e308cb129866249917f02865`,
never predicted, scored, transmitted as an observation, or counted toward the
18 prospective slots. On four previously inspected development inputs this
restores bitwise equality to the original archived vectors; independent GPU
readout must also pass 1e-6 before deploying any corrected release.

The independent pocket4 user-only predictor verifies this receipt,
original local input, source provenance and signed plan **before**
taking the same existing heavy-worker lease. It re-verifies inside its
no-network sandbox, uses the unchanged gfx1150 ROCm 7-class graph
readout, seals a separate predictor-role signature before target entry
(anchor+300 seconds), and releases its lease. Absent or delayed source,
CPU, SSH or GPU entry yields an *original* CENSORED slot. No replay,
late sealing, fallback ROCm-wheel CPU encoder, refit or additional broker
request is allowed. The evaluator scores only the original parent
future 9104 raw outcome, after anchor+1500 seconds, via the signed
separate seven-class protocol.

The 18-window prospective screen requires >=16 real OBSERVED, <=2
CENSORED, >=4 UTC days, >=2 true target bins, mean seven-class Brier
improvement >0.001 against **each** frozen prior/numeric/equal-mix/graph
baseline, and the predeclared one-sided conditional sign screen at
alpha=0.025/4 per baseline. This is a conditional, research-only
price screen, not demonstrated IID behavior, execution PnL, or an
automatic trading or promotion authority. No window may be judged final
before its fixed original outcome clock.

## Operations and recovery

- Configs distinguish registration/predictor/evaluator roles. Only the
  registrar may sign the new plan; only the predictor may sign the
  prediction, and only the evaluator may sign original outcomes.
- Timers are finite and absolute. Registration is retried only during a
  bounded interval after Laya29 completion; if terminal Laya proof,
  provenance or signatures are missing, there is *no* registered trial.
  CPU/predictor services never start a new source window outside the
  18 original future anchors. Timers are nonpersistent: missed execution
  is not caught up after reboot, and the original final denominator
  retains the miss.
- CPU emission has one immutable claim. After a crash or timeout
  following the claim, do not rerun encoding; a completed signed tensor
  may be recopied with the **same hash**, not regenerated. On pocket4,
  an already claimed but unsealed slot stays a failure.
- A complete, independently signed real-future plan needs verified
  *live* origination and transport evidence. The earlier synthetic
  cross-host smoke was explicitly **not** real market data and is never
  counted toward the 18 outcomes.
- Existing Value trial1/2/3, Laya29, the parent's first six windows and
  all their failures and consumed statistical budgets remain untouched.
  No production Execution restart, owner takeover, kill-switch change
  or forced trade is authorized by this protocol.
