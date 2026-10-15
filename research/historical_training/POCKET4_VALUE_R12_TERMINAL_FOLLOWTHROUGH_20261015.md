# Pocket4 R12 — original Value deadline terminal followthrough (2026-10-15)

## Real fault and original witness

R11 had **11 mature qualified flat ORIGINAL requests** after the real
2026-10-15 13:16:58 UTC+8 evaluator cutover. The independent source/witness
audit verified 8 predeadline commits, 2 immutable deadline failures and
1 expired request with no terminal receipt. This was not a prediction result.

The last unresolved original request began at 2026-10-15 15:20:31 local.
Its four actual original prediction start attempts (0,14,28,42 seconds
after request) were held by `OTHER_RESEARCH_WORKER_ACTIVE`.
The original R5 watcher forgot its retry state after 55 seconds. If no
subsequent fresh start ran the frozen original worker, its already expired
request remained without an original terminal receipt.

At 15:29:44, after checking that the exact frozen original worker source was
SHA256 `a4dd4ce5b770722b35b6df924154892a38c71daf798f45c53e59f9ec82015f0b`,
the original research-only service was started once, using the existing
unit, unchanged model, plan and user scope. It wrote a legitimate
`failure.json` with `REQUEST_ALREADY_OLD_NO_BACKDATING`,
`prediction_backdated=false`, actual observation time and
`broker_orders_sent=0`. No `commit.json` appeared for that request.
**That cohort's verified breakdown became 8 commits, 3 failures, zero
unresolved.** Original failure receipts are never overwritten.

## R12 change versus the already deployed R5 watcher

Original watcher's bytes were imported byte-for-byte from the installed
R5 release SHA256 `8cd03e17189b6dbc14c4706c5f91c6bd59ab3b4fa6f8479ddd5dcd50848e57aa`;
the independent research worktree had never included that watcher in main.

* Preserve original four predictions start attempts and 14-second retry
  cadence only while the request is within the original 55-second
  practical startup interval.
* Retain observed original flat REQUEST identities for terminal
  followthrough after the unchanged ORIGINAL 60-second limit. No
  signing/backdating occurs. Do not run at the exactly-equal boundary;
  wait until request age is **strictly greater** than 60,000ms, because
  the frozen worker uses a strict-expiry comparison.
* An expired and truly unterminated request may request at most **two**
  further starts of the same frozen worker, at least 15 seconds apart
  and only up to **180 seconds** after original start. If a worker is
  active, postpone without consuming attempt budget.
* Always prioritize a still-valid fresh original request over a stale
  terminalization when both are offered simultaneously; the original
  worker scans all real requests and remains the only terminal writer.
* After watcher restart, recover only verified owner-private original
  flat REQUESTs aged **over 55 seconds and up to 180 seconds** as
  terminal candidates. They cannot be offered to the normal
  prediction-start lane, and older historical requests are NEVER
  replayed for signing.
* Do not start the worker for a request that already has its authentic
  original `commit.json` or `failure.json`. The source watcher
  publishes only a start receipt labeled
  `EXPIRED_ORIGINAL_TERMINAL_FOLLOWTHROUGH`, not a trade, signature or
  changed plan.
* If the worker remains unavailable for the full bounded horizon,
  leave the request genuinely `UNRESOLVED_EXPIRED` for audit. Never
  fabricate a worker receipt or mark an unobserved systemd start
  successful.

This closure does not change Value admission thresholds, memory pressure
guards, the original 60-second deadline, original role keys/weights,
frozen model code, strategy, OMS, or the existing single-owner flock.
The trigger service has no Broker API. Frozen worker remains responsible
for verifying original requests and signing only genuinely timely work.

## Regressions and deployment boundaries

R5 old tests plus R12 adversarial cases cover exactly 60,000ms boundary,
55–60-second delayed ingestion, 3-minute recovery, signed/failure
terminal preservation, active original worker deferral, strict maximum
number of late starts, fresh request priority and same-process single start.
The Pocket4 original historical suite ran **709 passing tests**. The
R12 code is deployed only via a SHA-pinned, separately rolled release
of the original **trigger** service. Production Execution, FX/BTC PAPER,
Laya trainer and evaluator, and the frozen Value predictor unit are not
replaced. Deployment/reversion receipts stay in
`~/.local/state/heptatrader/operator-evidence/pnl-r12-original-deadline-closure-20261015`.

A signed forward-coverage improvement requires real new mature eligible
flat requests, not these synthetic tests or postdeadline failure closures.
