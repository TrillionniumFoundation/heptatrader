# Original IB PAPER source health: first 320 of 480 cycles on physical hosts

2026-10-13. **320-cycle prefix evidence PASS / full 480 and production
monitor cutover HOLD.**

## Why this audit exists

The original, independently preregistered **480-cycle / two-hour** source
health experiment is still running. It is a 15-second fixed start-to-start
sandbox monitor, 50% of one CPU core, 384MiB, AF_UNIX only, without restart.
It does NOT replace the installed health monitor, relax the 5-second quote
TTL, modify PAPER FX/BTC trading, create a new signal, or qualify a
native product training pipeline.

The previously frozen EUR/USD price study ended with **four CENSORED
entries and 16 CENSORED maturities** because the original production
health monitor was too old at the four fixed decisions. Three
contemporaneous later health JSON files were captured but past their
30-second source-age bound; the first window's original full monitor
JSON was never captured. No subsequent successful source check repairs
those failed decisions.

The present 320-cycle readback is a new **strict prefix** of the same
original 480-cycle source audit. It does not backfill or launch any
future cycle, and it cannot certify the remaining 160.

## Actual original Pocket4 320-cycle closure

- First **320** consecutive original snapshots and SHA256-chain receipts,
  all recorded under the already frozen 480-cycle schedule.
- **320 READY, 0 HOLD**, original 24/24 probe metadata independently
  recomputed for every cycle, original period and original source hashes
  validated.
- Maximum source-as-of age at publication: **12,607ms**.
- Maximum age immediately BEFORE the next publication: **27,607ms**;
  original hard threshold is 30,000ms.
- Original frozen 480-plan SHA256:
  `490375e6cfc4cea69b0aeb8ece2bd836228d1b3d086793555ed35d920bc8f6be`.
- First320 receipt-chain head:
  `88eb51bab98cb14911acb0eda3b649583a0db505580704a135b7c6fde915aff1`.
- Exact independent pocket4 320-cycle prefix report SHA256:
  `6e420025e52c1e594f4639d48021fa01e3d16471b9bc5fbbdb4f4c28108814d9`.

Original byte custody remains on pocket4 at
`~/.local/state/heptatrader/operator-evidence/fx-original-health-sustained-20261013-v1/`.

## Portable archive and independent physical host replays

**643** strictly named, owner-private original files were archived:
three original plan/worker/sandbox source identities, plus 320 exact
status JSON/receipt pairs. The archive was exported while the 480-cycle
producer kept running. Only immutable first320 snapshots were accessed.
It contains no IB credentials, bearer tokens, private keys, OMS journal,
orders or model weights.

- Original compressed archive SHA256:
  `8ff400bd26fe802a51fd39fba65f480b12fae075f8aca6bb0814480f7b8edf90`.
- Self-contained Python 3.9+ stdlib verifier
  `fx_original_health_portable_320_verify.py` SHA256:
  `942c6e0f19ca540a7b1211c52487578f6e00efa38227390acbd486f2e905d5b9`.
- The verifier's immutable source PINs include the *old* plan,
  worker-start and sandbox hashes, this exact cycle319 chain head and
  the separate Pocket4 audit receipt. No caller can supply a different
  root or self-signed target to claim source acceptance.

All four physical hosts independently verified **the exact same frozen
Pocket4 archive** using their existing strict SSH known-hosts identities:

| Host | Verified receipt SHA256 |
|---|---|
| Pocket4 (original host, separate parser) | `6040f36bd69e75a2a51289359555715e55438f81b0c1af4cd7b573192e5ef624` |
| qian-qi (independent host) | `74a13832950bb3b8ed3d007b644646a4e9e862b551a064b3b190bdf7b1af6a2d` |
| MacBook Pro (Python 3.9) | `464e66c817df73d7ae6d6be7706bdc5b3c4668320f15fedae8a02976b212a6be` |
| ThinkPad X230 (Python 3.12) | `665f2d390f9a744933d09886dd3ea219336b2334f72b46b09c4b44cbe2881b49` |

All agree on the 320-cycle original status, first320 hash chain,
320 READY / 0 HOLD and worst before-next-publication source age.
Different output SHA256s are expected because hosts record their own
inspection timestamp. **These are NOT four independent IB feeds,
broker-signed market snapshots or production signing witnesses.**

Original private bundle/receipts:
`~/.local/state/heptatrader/operator-evidence/fx-original-health-crosshost320-20261013/`

Host private copies are retained at:
`~/.local/state/heptatrader-fx-health-crosshost320-20261013/`

## Adversarial checks and remaining work

**13/13** independent portable verifier adversarial tests passed,
covering plan, source, receipt chain, middle and last cycle original
tampering, fraudulent replacement of first160 report, duplicate
archive members, path symlinks, nonprivate tar, duplicate JSON and NaN,
and false promotion claims. Original 240-cycle and 480-cycle verifier
code remains unmodified.

This is a **partial** cohort by construction: 160 of the original
480 future cycles remain unverified. The full independent
`fx_original_health_sustained_audit.verify_all()` must run **after**
the standalone service naturally reaches its frozen final receipt,
without substituting a prefix for final acceptance.

Even a 480/480 research PASS does not authorize production monitor
cutover: the separately tested **original 25% CPU quota** produced a
33,986ms stale-before-next-publication violation, and the cached
24-cycle candidate retained genuine source HOLDs. A proposed
production configuration still requires cold restart, unclean exit,
CPU contention, source disconnect/recovery, period rollover and
explicit rollback under the exact real quote/paper policy.

**No production monitor, FX/BTC trading, native Train V2 120-minute
FeatureSpec, actual execution net profit, account NAV/Flex or
Owner/LedgerWriter/Witness approval changes were made.**
