# Full 480-cycle IB PAPER source-health independent physical-host reparse

Date: 2026-10-13 (Asia/Shanghai, UTC+8).
Status: **original-source custody/timing research PASS; production monitor
cutover, native training, trading and model promotion remain HOLD**.

## New work versus existing evidence

The source-health producer had already completed its frozen 480 cycles and
natural termination. The independent Pocket4 verifier's final report was
written once, then the same frozen source health experiment was transported
without rerunning the producer or adjusting any previous forward windows.

This branch is **stacked on**
`audit/fx-original-health-full480-crosshost-20261013` (the already
verified, pinned first 320 cycles). It adds an exact original-byte
archive export and a third independent, standard-library-only
full-480 portable verifier for *completed evidence*. It deliberately
copies source files into a NEW isolated worktree rather than changing
an uncommitted in-use sibling.

Original fixed plan:
`490375e6cfc4cea69b0aeb8ece2bd836228d1b3d086793555ed35d920bc8f6be`

Original worker start:
`3b880a8bd59b515b128a5566e713767ac822862d695f1f8c4237bb018fbc0d59`

Original sandbox receipt:
`b18abaf07255b4183f96dcdad8ed5059cbbd8e2f82616c81cc5597fef513acaa`

Previously verified first-320 report:
`6e420025e52c1e594f4639d48021fa01e3d16471b9bc5fbbdb4f4c28108814d9`

Natural original final:
`eb4b0f4347c034c3d4e2ab4acb885772bc61fcdce1c54a2f8170140fc6e8cc6d`

Full independent Pocket4 source report (existing final source audit):
`545af6fbee36436770d456a84ecd1bf813065f6b4e46a6e69e12b9e202408890`

Original exported read-only archive:
`5e6cecd95f676c88ab90f8a7651a41545174114c7f5a81accef52a62ac2d0d83`

The archive contains precisely **964** private source files: 480
original health status JSONs, 480 SHA256-chained cycle receipts, original
plan, worker-start, sandbox and natural final. Does not contain original
broker account credentials, trading OMS journal, private keys, order
tokens or a new simulated price or execution reward.

## Actual four-way reparse

A single, byte-identical archive was independently read and parsed with
Python standard library only on:

| Host | Source result | SHA256 of its unique write-once report |
| --- | --- | --- |
| Pocket4 | PASS; custody/timing only | `76634ba32d63867d4cfb08021bba649de2c236c2dc3b23bde40c532ab8222a58` |
| qian-qi | PASS; custody/timing only | `6c5190837415f94aba92aa38bc7ad2e85f3332e4501c0183789da55703595a50` |
| MacBook-Pro.local | PASS; custody/timing only | `c91e511d1ec5b3146bd5321d5cb8450fd264cc891c3f95a810e26d550ffa1445` |
| qian-ThinkPad-X230 | PASS; custody/timing only | `030f12995a834440e0b1b7d0a1e2df0e8dc78e0281b87b1f3700521fe29ac9b6` |

Mac's Python **3.9.6** and X230's Python **3.12.3** used
`ssh -o StrictHostKeyChecking=yes` with existing host keys and no
interactive trust-on-first-use. Physical hosts had separate output
timestamps, so receipt hashes differ, but every observed original byte,
source receipt chain, timing and no-trade conclusion agree.

### Verified counts and high-water marks

- **480/480** original private quote-health status JSONs and complete
  chained per-cycle receipts; zero missing or censored original blobs.
- **475** actual READY, **5** genuine HOLD. No false 480/480 READY claim.
- Maximum original source age when its own status was published:
  **12,607 milliseconds**.
- Maximum original source as-of age **immediately before next publication:
  27,607 milliseconds**, below 30s threshold on this 2-hour source study.
- Original 24 source probes, original PAPER period, quote-age bound,
  original source-prefix SHA and monitor rule all independently verified.
- Confirmed frozen 320-cycle prefix SHA and prior independent 320 report
  agree, and final 480-cycle chain head matches original final.

Every physical-host result carries `production_monitor_cutover_allowed:
false`, `actual_broker_IB_quote_signature_verified:false`,
`independent_product_clock_or_witness_enrolled:false`,
`automatic_training_or_model_promotion_authorized:false`,
`trade_authority:false`, and zero broker requests/orders.

## Negative cases and limitations

The portable code fails closed if:
- The original 480 final file or original Pocket4 independent report
  differs by a single byte from hardcoded SHA256 pins.
- A SHA-chained receipt or original source status JSON is modified,
  missing, duplicated, symlinked, unsafe or nonfinite.
- Original 24-probe count, original quote age, known-at timestamp,
  readiness state, source hash, identity or no-trading contract changes.
- A later source is substituted to retroactively turn an old HOLD into
  READY, or a self-signed replacement cross-host report is provided.
- Max age before next publication exceeds the original 30,000 ms gate.

These independent replays are from **one originally captured pocket4
IB PAPER quote source**, *not* multiple independent subscriptions to
IB or broker-signed quotes. Filesystem and host clocks do not provide
an independently administered timestamp trust root. The 480 cycles
are overlapping operational probe snapshots, not 480 strategy
predictions, fills or independent efficacy observations.

One early manual Mac invocation with relative input paths failed the
existing `PRIVATE_SOURCE_INVALID` guard. It was retained as a
rejected attempt; the subsequent invocation passed **using absolute
paths** without relaxing the file/path verification.

## Reproduction

On Pocket4 private evidence:
`~/.local/state/heptatrader/operator-evidence/fx-original-health-full480-physical-20261013/`

The exact private tarball and Pocket4, qian-qi, Mac and X230 JSON
receipts are kept there; matching local private source and report
copies remain on each physical host.

From the new isolated worktree:

```sh
export PYTHONPATH=research:research/neuron_learning:scripts
P=~/.local/state/heptatrader/operator-evidence/fx-original-health-full480-physical-20261013
S=~/.local/state/heptatrader/operator-evidence/fx-original-health-sustained-20261013-v1
A=~/.local/state/heptatrader/operator-evidence/fx-original-health-crosshost320-20261013
python3 -B -m train_v2.fx_original_health_full480_portable \
  --bundle "$P/full480-original-byte-exact-v1.tar.gz" \
  --prefix-report "$A/independent-first320-pocket4-v1.json" \
  --full-report "$S/independent-full480-after-natural-exit-v1.json" \
  --output "$P/RECHECK-NEW-UNUSED-FILENAME.json"
```

The output path must be new. Original 480 source records and previous
failures are never modified. No service/timer/driver restart occurred.

## Remaining admission blockers

Full two-hour local source **timing and custody** is now qualified,
including physical cross-host independent reparse. It is NOT a
production monitor lifetime qualification. The correct next isolated
experiment is a controlled cold-start/restart/rollback and 30s age SLA
under real CPU contention and simulated upstream source failure,
followed by a bounded **monitor-only** cutover with explicit rollback
and proof that original PAPER requests, orders, model state and PnL
are unchanged. Do not cut over based solely on this two-hour test.

For model promotion, independent blockers also remain: original
behavior-policy propensity, actual after-cost executable IB trades,
Flex/Activity account NAV, native Root/Owner/LedgerWriter/Witness and
positive independent forward economics. The completed price-only
four-window research remains **0/4 eligible and 16/16 CENSORED**,
regardless of later monitor health.
