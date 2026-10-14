# FX source health: 160 exact original cycles and three physical-host readbacks

2026-10-13 UTC+08. Release status: RESEARCH PREFIX PASS ONLY; full 480-cycle evidence and production monitor cutover remain HOLD.

## Original source and outcome

The old EUR/USD PAPER study ended with four CENSORED entry attempts and 16 CENSORED dependent outcomes. Original future quote-health JSON was saved for the last three entries: all reported READY/24, but was 38–86 seconds old at the frozen decisions, so the original 30-second age gate correctly refused all. Window 0 health bytes were not preserved and cannot be recreated.

A separate previously frozen 480-cycle health producer continues unchanged, recording original IB PAPER quote-status, 24-probe counts, as-of timestamps, and one SHA256-chained receipt per 15-second target. This follow-up audited exactly its first 160 cycles without restarting the producer, changing source or period IDs, or reading future cycles.

Pocket4 independent first-160 result:
- 160/160 original source snapshots, 160/160 SHA-chained receipts;
- READY 160, HOLD 0; 24-probe metadata recomputed every time;
- worst at-publication source age 12,607 ms;
- worst source age just before the next publication 27,607 ms;
- worst start-delay 3 ms;
- original first-160 receipt chain head: 63a80cf12bc40a8b51ebce3b0a27855f6809bdb6fcacef71f00fedaaca4ea142;
- independent Pocket4 report SHA256: 1f50439f25bed14dd85ab99b6d31b7bf8753914059ab11ad7c6dfabf95d2a751.

The original pre-study immutable hashes are:
- plan.json: 490375e6cfc4cea69b0aeb8ece2bd836228d1b3d086793555ed35d920bc8f6be
- worker-start.json: 3b880a8bd59b515b128a5566e713767ac822862d695f1f8c4237bb018fbc0d59
- sandbox.json: b18abaf07255b4183f96dcdad8ed5059cbbd8e2f82616c81cc5597fef513acaa

## Independent physical readbacks

Pocket4 exported precisely 323 owner-private files: original plan, worker start and sandbox, plus 160 original receipt/full-status JSON pairs. No brokerage credentials, OMS journals, private keys or actual trading commands were included. This original capture is locally SHA-chained but NOT independently IB-signed.

The exact compressed archive SHA256 is e587fac6ca754df3f623b909dad5cd54f41aaacee21ed30f7a2a82c4a188080b.
The pinned, standalone Python stdlib source verifier SHA256 is 8a8e7614d5fa4d9429f56437542ad982ab39248bd7d43efe9c5d11d49edd2520.

With strict previously trusted SSH host keys, qian-qi, MacBook Pro and ThinkPad X230 received matching archive, first160 report and verifier bytes, then independently reparsed all 160 original quote-health source records, exact immutable prefix SHA chain, timestamp ordering, all 24 probes and the age before every next publication.

Independent, owner-private receipt SHA256s:
- qian-qi: 8771563bb7f01a3ab8f619aa5e384b86982ab8b692d5e0b47474fc609ab26ef1
- MacBook Pro: 5c942d72344d3da16860e9b66bd500047dc81507bdbc7c129c1c6a424f9b8611
- ThinkPad X230: 6372e0c16668a064ce8d6390cde7826f13b8413ded446c46b6d43ff7c1816dbd

The physical hosts agree on the measured source READY counts and 27.607-second maximum before-next-publication age. Their different report digests reflect local check time. This is three physical hosts independently verifying ONE Pocket4 original source; they are not three independent IB market-data sources, trusted hardware clocks, or native product Witnesses.

Original archived receipts and Mac/X230 results are preserved on Pocket4 in:
~/.local/state/heptatrader/operator-evidence/fx-original-health-crosshost160-20261013/
Other hosts retain separate private original readbacks under:
~/.local/state/heptatrader-fx-sustained-crosshost160-20261013/

## Verification code and release gates

The new research/train_v2/fx_original_health_portable_160_verify.py hard-pins original plan/worker/sandbox bytes, original SHA chain head, the first160 Pocket4 audit digest, the immutable 480-calendar, original 24-probe and 30-second age rules. It parses tar without extraction, refuses duplicate/traversing/symlink/public files, recomputes every source hash, and never accepts caller-supplied replacement trust roots. Its new synthetic regression suite has 10 adversarial tests; the original pinned first32 suite remains unchanged.

No trade authority, native Owner/Root/LedgerWriter/Witness, real IB execution-cost PnL, model-fit or automatic promotion has been granted. The remaining 320/480 future cycles were NOT independently audited by this receipt. The installed production monitor still runs its old exact implementation. Do not promote a prefix into a full production monitor cutover.

Next hard gate: original two-hour study must naturally produce all 480 statuses and final receipt chain; independently reparse 480 raw source files, require all original timing and 30-second age gates, verify cold-start/CPU contention and rollback, and only then consider a separate monitor-only cutover. Keep PAPER strategies, older CENSORED experiments, quote TTL, cash/Flex NAV and model admission unchanged.
