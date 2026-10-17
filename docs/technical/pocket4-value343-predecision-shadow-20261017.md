# Pocket4 Value343 prospective original-request shadow

Research-only. This does not authorize trades, model promotion, or policy gradients.

## Genuine historical training and known limitation

The original Value343 chronological study (docs/technical/pocket4-value343-chronological-20261016.md) verified 343 input-bound financial rewards and isolated 335 from one genuine original EUR/USD USD100k behavior. Its original OMS close/fee timestamps made a 217 train / 51 embargo / 67 historical holdout split causal. The 17-dimensional Huber Value model completed 20 CPU updates but failed to beat a train-only median on MAE; this model is not qualified for trading.

Fixed original private report at Pocket4:
  ~/.local/state/heptatrader/operator-evidence/value343-chronological-oos-20261016/study-v4.json

SHA256: dc0e463e6acc2a11a095afcc7623498959327f00d2835b9b2a0bdc8cf659f060

Fixed original fitter code SHA256:
  73354f5d1fdaa0ace5c60211597c9c2d4d2f5f74ecb2f5e9ecef63cb206f246c

No original prompts, credentials, journal contents or per-trade amounts are sent to GitHub.

## Predecision prospective observer

Implementation: research/neuron_learning/laya_cell/value343_prospective.py

The observer reads only the original, owner-private, hash-chained attribution journal of that same registered behavior manifest and period, validating each record and the producer previous/sequence chain. It never calls the Broker, an LLM, the market, a trader, or a remote host.

The model and observer code are pinned once in an immutable local freeze receipt. It refuses all requests earlier than the activation instant. Each new, flat-position original REQUEST must be observed before any original RESPONSE, DECISION or COMMAND_BINDING and within 90 seconds of its original request timestamp. Both hypothetical BUY and SELL net-value predictions are sealed at once using only the request-time quote snapshot. The eventual chosen side is not consulted and historical results are not used to pick the output.

Every predecision receipt is O_EXCL, SHA-bound to the original event and source prefix, and carries original capture, request and local seal clocks. Requests seen after a RESPONSE, too old or nonflat generate immutable missed/censored records rather than synthetic or backdated predictions.

## Future settlement scoring

Once a NEW actual EUR/USD reward is mature and available, the scorer independently verifies one matching original request, one original decision and exactly bound prepared command. It recomputes the ORIGINAL USD100k flat-to-flat economics with actual observed broker executions and USD commissions from the OMS. Prediction seal must precede the original DECISION and last original fee-inclusive outcome availability. Only the actually executed side receives a score; the opposite presealed direction is never claimed as an observed counterfactual.

The scorer compares frozen 17D Value predictions with the frozen train-only median; all scores and rejected timing cases are immutable research receipts. No model retraining or strategy selection occurs.

## Pocket4-only execution

Run from its frozen research release using the existing local Python 3.14 venv; the only writable path is a dedicated owner-private evidence folder. Bounded user service has no network, broker rights or GPU: 512MiB memory, zero swap, 40% CPU quota. A source-only user timer may run periodic checks independently of existing Trainer, Evaluator, Execution, FX/BTC drivers and IB Gateway.

While the forex market is closed, there may be zero new original REQUESTs. Zero prospective captures is not a pass or a defect. No retrospective requests are relabeled as future examples.

Even a later prospective MAE improvement is insufficient by itself to prove net profitability or authorize trades. The value model remains RESEARCH ONLY until independently justified by new future outcomes and actual cost/risk evidence.

## Actual on-host Pocket4 deployment and independent recheck

The source-only implementation was committed to the dedicated research
branch as c241c52b7aae5a803e63e1eb683dd4d1664d7cb1 and copied to
an immutable-versioned local release:
  ~/.local/share/heptatrader/value343-prospective/releases/c241c52b7aae5a8

The installed program SHA256 is
  e7777fd5762d4147540bcd4b98934b3d6d1c80e2c860f6a4ea0243707f08cb2d

The once-only private model/observer freeze is
  ~/.local/state/heptatrader/operator-evidence/value343-predecision-shadow-20261017/freeze.json
SHA256:
  16617ae2ee614dc70e444ef4abd97db65fd9b0ea4e458034eaa98aa0b2e4184c

The real Pocket4 per-user timer is enabled and active:
  heptatrader-value343-predecision-shadow.timer
The associated source-only oneshot service has CPUQuota 40%, MemoryMax
512 MiB, MemorySwapMax 0, no network, original source read-only and
only its private evidence root writable. User linger was already enabled.
The first real runs finished successfully, around 1.3s wall time and
128 MiB peak memory. The original source included 4,432 historical
requests that all preceded activation and were correctly ignored;
there were ZERO future predictions or scores during this closed-market
bootstrap. Do not claim future scoring success from an empty cohort.

User systemd initially rejected an overly strict capability sandbox with
218/CAPABILITIES before Python started. Only this new research service
was revised to omit incompatible CapabilityBoundingSet/PrivateDevices
options, retaining NoNewPrivileges, source read-only, no network and
resource limits. The earlier failed invocations remain in the service
journal. No other service was changed.

Independent, create-only on-host deployment qualification:
  ~/.local/state/heptatrader/operator-evidence/value343-predecision-deployment-20261017/runtime-qualification-v1.json
SHA256:
  0fa1157af3a1eec4679b95daa274fa44608e2154d09dcc38b7c0b8a35ff97312

User-space operational rollback is disabling only the new timer:
  systemctl --user disable --now heptatrader-value343-predecision-shadow.timer
It does not affect FX/BTC PAPER Drivers, Execution, Gateway, Laya,
IB Gateway, telephone or other existing systemd units.

Actual Pocket4 fixed-source regression before release: 24 focused,
116 Source and 705 Historical Training tests passed, as did local
CI/documentation and whitespace checks. Hosted CI / full Core success
and any genuine post-freeze future outcome are NOT claimed.
