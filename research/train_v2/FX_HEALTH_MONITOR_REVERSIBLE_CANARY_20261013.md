# Controlled source-readiness monitor canary and verified rollback — 2026-10-13

**Environment:** Pocket4 / Ubuntu, PAPER only. **Release: production trade
and model admission HOLD.** This record documents a *temporary* deployment
of a read-only source-health observer, **followed by an actual rollback to
the original running monitor**. It does not authorize model promotion or
rewrite the failed 4-window original price trial.

## Incident and exact hard gate

The fixed October 13 EUR/USD prospective price study completed with
**0/4 eligible preactions, 16/16 CENSORED horizons**. It correctly refused
the contemporaneous source health as `ORIGINAL_READINESS_HOLD_OR_STALE`;
three later health-witness snapshots independently retained the original
HOLD and outdated status, and all fixed observation results were preserved.
No later source freshness can turn those four old windows into a trade.

The installed original `heptatrader-learning-source-health.service`
was a read-only reporter, at **CPUQuota=25%**, which always slept
**60 seconds after each completed assessment**. Typical full 24-probe
assessment at 25% was 18.7–20.3s; hence status published every
roughly 79–80s, incompatible with the separate strictly enforced
30s maximum status age at future decision time. The original period,
source parser, 24 probe/20 usable/4 missing requirements, broker 5s quote
TTL, and 30s path continuity remained unchanged.

An already committed opt-in code version
(`4f232bdd95f8a37cd2f6217ed9ec3825b5c2187f`) supports
`--sandbox --refresh-seconds 15` with start-to-start scheduling.
Its original non-opt-in 60s behavior remains unchanged. Prior Pocket4
480-cycle, 2-hour sandbox study observed **475 READY / 5 HOLD**,
maximum as-of age at publication **12,607ms**, and maximal inferred
age before the next publication **27,607ms**. Pocket4, qian-qi, Mac and
X230 re-parsed those exact original private bytes and agreed.
This is operational source timing, **not production-lifetime or signed
IB market data qualification**.

The separate cold-start drill verified original CPU25 percent behavior,
and CPU50 percent health-source monitor under an isolated SIGKILL and
recover, without disrupting the original user service.

## New reversible monitor-only work

Branch: `ops/fx-health-monitor-reversible-canary-20261013`.
The deployment tool in `research/train_v2/fx_health_monitor_reversible_canary.py`
hard-codes and checks:

- Original source SHA256:
  `63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320`.
- New opt-in source SHA256:
  `0172c3470e7e0b821c39aac00eb077d18d436b1711c32d46f4a99bfd32199f12`.
- Original PAPER period SHA256:
  `61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f`.
- Exact running service config and status **paths in the current ExecStart**,
  source ownership/mode, original CPU budget, AF_UNIX-only socket family,
  `NoNewPrivileges=yes` and `Restart=on-failure`. Refuses any unrelated
  user service, model or modified program identity.
- Independent new release directory under
  `~/.local/share/heptatrader/laya-resident/releases/health-monitor-15s-canary-20261013`,
  cloning the original 1.5MiB read-only release and replacing only the
  exact pinned source-health monitor module. The original release is
  not modified.
- ONE known temporary systemd override:
  `~/.config/systemd/user/heptatrader-learning-source-health.service.d/zzzz-health-monitor-canary-20261013.conf`;
  only ExecStart (adding 15s), PYTHONPATH, WorkingDirectory and CPUQuota
  (25% -> 50%) differ. The original PAPER Execution, FX/BTC driver,
  PnL, Laya trainer/evaluator and unrelated systemd units are not restarted.

## Actual Pocket4 trial, observations and rollback

Private evidence root:
`~/.local/state/heptatrader/operator-evidence/fx-health-monitor-reversible-canary-20261013-r2/`.

An initial preflight was rejected because the candidate deploy helper
had an incorrectly shortened status-directory hash, which made its
status file unavailable. **No user unit was changed in that failed
preflight.** The status path was corrected and **an adversarial test**
was added that compares the candidate's exact config and output paths
to the running service's ExecStart *before* any restart. The empty
failed-preflight directory and failure terminal receipt are not
misrepresented as successful deployment.

After preflight v2:

1. Staged a new immutable candidate release, preserved the original
   module SHA, and separately recorded the exact original effective
   service properties and status bytes.
2. Temporarily applied the single, SHA-pinned override and performed
   a **real user-service restart**. Effective production read-only
   observer showed `MainPID=335112`, `CPUQuota=50%`,
   `RestrictAddressFamilies=AF_UNIX`, `NoNewPrivileges=yes`,
   with the exact opt-in module SHA.
3. Observed **11 consecutive real monitor cycles**, all legitimate
   `READY_FOR_REVIEW_NOT_AUTHORIZATION` with 24/24 source probes,
   **0 broker orders, 0 trading authority, 0 automatic trial
   registrations**.
4. Worst actual status source age at publication: **10,516ms**.
   Worst previous source age immediately before next publication:
   **25,517ms**, below the original 30s hard bound.
   The original PAPER execution, FX/BTC driver and trainer service
   PIDs remained unchanged across activation:
   `4111860`, `724712`, `724713`, `3755315`.
5. Immediately **removed only the exact known SHA-matching canary override**,
   performed `systemctl --user daemon-reload` and restarted
   the **read-only health monitor** again. Actual subsequent service:
   `MainPID=338408`, `CPUQuota=25%`, original release
   `8dd7ddd-proof-c17f338`, original source SHA256 and `AF_UNIX`.
   The override was absent; the candidate release remained for
   forensic reproduction and rollback source. The original trading
   and trainer PIDs remained unchanged.

Key private machine receipts (write-once, 0600 under 0700):

| Receipt | SHA-256 |
| --- | --- |
| staged original/candidate identity | `d78ed724986bb206217f9bc834bacf505f6a71808ee77e5f3ab71ad80da166ee` |
| actual temporary activation | `057e1653b2db25781228ee53b4834a798180b84b6eef89a80d994909d557e6af` |
| 11-cycle live source/period/PID audit | `afb70b281e7ec1ae2ab2a6953505df0618a0f6bfbe32516355f47d1af137fb07` |
| actual restoration of original monitor | `fa817b109f7576c22432073f79d555429cecf0d2e649b1d0cb069243bf11139b` |

The temporary switch is **not left active**. An ordinary successful
live canary never reclassifies the earlier research cohort or
proves a separate user's future trade opportunity.

## Tests and remaining admission

`test_fx_health_monitor_reversible_canary.py` checks exact release,
runtime restrictions, candidate CPU budget, no trader executable,
unknown-dropin refusal, output path identity and write-once receipts.
`test_fx_health_canary_live_capture.py` rejects missing,
out-of-order or stale journal cycles, unexpected trader restarts,
forged quote-prefix digests, automatic registration and evidence overwrite.

These controls prove one reversible monitor-only deployment under the
observed load, not automatic permission to make this the permanent
source-health service. This trial deliberately records
`cutover_qualified_for_future_permanent_deployment=false`.
Longer production-like CPU contention, independent original source
integrity, nontrading and rollback alarms and an explicitly accepted
operator release receipt remain separate approval gates.

**Trade/admission HOLD:** no new FX Ridge model, no 120-minute
Train V2 native FeatureSpec, no original behavior-policy propensity,
no independent production Root/Owner/LedgerWriter/Witness, no real
after-cost broker alpha, no IB Activity/Flex/reset NAV statement
completeness. All previous failures and source data are preserved.
