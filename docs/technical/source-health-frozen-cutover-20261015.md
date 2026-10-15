# Pocket4 Source Health: finite frozen-cohort-safe cutover

Status: **STAGED ONLY** (2026-10-15). This is a one-service maintenance procedure, **not** an order gate, model selection or trade authorization.

## Exact scope and evidence

- Original user service: `heptatrader-learning-source-health.service`; original module SHA256 `63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320`, 25% CPU, 384 MiB, AF_UNIX only.
- Already-existing immutable candidate: `~/.local/share/heptatrader/laya-resident/releases/fx-cache-suffix-a125a887-20261015`; module SHA256 `8cbedb6a5f792891f0141f7b2ae0da91a7738080cd14c45995436ed55473c309`, verified sealed-cache SHA256 `a125a88765f3b212b709ee817fce07d8dded504d93139b0ab6da4db3833f0bc7`. Every other file is byte-identical to the original release.
- Private Pocket4 staging: `~/.local/state/heptatrader/operator-evidence/source-health-frozen-cutover-20261015/stage.json`, mode 0600 in a mode-0700 directory. This **does not** install a systemd override, restart the service or change the input of any frozen decision.
- Actual 24-cycle isolated 50%-CPU cold-to-warm proof predates this cutover: [source-health capacity and SLA evidence](source-health-cache-capacity-20261015.md). This was finite evidence, not production approval.

## Hard barrier before any activation

The original frozen campaign is scheduled through **2026-10-15 17:38 UTC**. A new monitor cannot be activated merely because the historical 24 windows can no longer reach their promotion threshold. The program demands, simultaneously:

1. The real UTC clock is past the original final sweep time;
2. The **original** `complete.json` and `status.json` both exist, the same precommitted `campaign_id` appears in the original `plan.json`, and all 24 original slots are immutable OBSERVED/CENSORED terminal results;
3. The campaign status remains `COMPLETE_PENDING_REVIEW`, with `finalized=24`, `observed+censored=24`, and no automatic promotion;
4. The active service, period/config, exact producer path, source/cache hashes, complete candidate file inventory and old systemd unit hash still match the write-once stage;
5. All execution, FX, BTC and Laya trainer service PIDs are active immediately before changing **only** the source-health unit.

If any condition fails, the command exits **before** writing a systemd override or restarting a service. The original PAPER risk and kill switch never depend on this readiness status.

## Single guarded action and rollback

The stdlib-only, pinned operator in `research/train_v2/fx_health_monitor_reversible_canary.py` has modes `inspect`, `stage`, `cutover`, `restore`. Stage was performed once against the already-installed candidate; do **not** rerun it against the same evidence directory. The explicit `cutover` action is for use **only after** the original campaign has actually reached terminal status.

`cutover` writes one known systemd drop-in for `--sandbox --verified-sealed-cache --refresh-seconds 15` and CPUQuota=50%, preserving the original 5-second quote TTL, 20/24 source probe rule, 30-second publication-age bound, output path, AF_UNIX and 384 MiB memory cap. Only the source-health user service is restarted. It checks actual four protected PIDs remain unchanged, then requires **24 consecutive real service journal cycles** with strict original source age at publication and immediately before the next publication <30 seconds. It rejects fake READY even if 24 probe records are present; genuine HOLD stays HOLD.

On full success, `acceptance.json` is created once and the new monitor continues running. On failed service identity, wrong quote readiness, missing cadence evidence or SLA breach, `cutover` invokes rollback of **only** the exact override bytes it created, reloads systemd, and restarts the original source-health service. An unrelated/altered override is never removed. `restore` is also available for an operator-initiated later rollback using the same private stage record.

The script does not contact IBKR, submit/cancel trades, request new broker history, change native Owner/Execution/FX/BTC services, rewrite old labels, change thresholds, or authorize model promotion. No claim of continuous 24-hour uptime follows from one finite acceptance window. Preserve the original operator evidence, failures and terminal receipts.
