# EUR/USD original source-health cache — second real 25%-CPU 24-cycle audit

Date: **2026-10-13 (pocket4 UTC+8)**. Outcome:
**operational research-candidate PASS, production monitor replacement HOLD**.
No model training, trading, original source normalization, account reconciliation
or experiment retroactive relabeling has been performed.

## Why this was necessary

The pre-registered 00:20 / 03:35 / 06:50 / 10:05 EUR.USD
original-quote price study recorded four immutable
`CENSORED / ORIGINAL_READINESS_HOLD_OR_STALE` entries. The three
original health JSONs independently saved at later decisions all
reported 24/24 USABLE probes, but their producer `at_ms` ages were
37,938ms / 86,233ms / 85,031ms, exceeding the frozen **30,000ms**
limit. The original monitor was and remains configured for **25% CPU**
and a **60-second sleep after a complete original source assessment**,
which leaves most wall time with an outdated status.

A *different* 10:05 pre-registered original-vs-cache comparison already
showed **86.092 seconds** for the product monitor vs **18.000 seconds**
for a separately provisioned verified-cache monitor, while both
reported 24/24 USABLE. The original decision was correctly censored,
not accepted using the shadow READY.

The original monitor's source code, active service identity, health rule,
quote decoder, source-PAPER period, risk and trading drivers, and old
four-window plans remain **unchanged**.

## Second actual bounded operational run

Candidate code already reviewed in branch
`research/fx-verified-cache-rollover-proof-20261013`:
SHA-256 verified-sealed parse cache, exact full source archive
inventory re-hashed on EVERY assessment, repeat hash before
cached-hit use, unsealed/active segments strictly reparsed,
bounded 320 file and 180,000 quote-record cache.

This second independent run precommitted its source code **before**
launch at **2026-10-13 10:16:25 UTC+8**:

`~/.local/state/heptatrader/operator-evidence/fx-cache-real-ops-20261013-v1/plan.json`

Plan SHA256:
`45a651215410ec9ee25a6c280db3975c291459674190b5f06a61ba2677d63dd9`.

Actual transient unit:
`heptatrader-health-cache25-ops-20261013.service`

- Actual original IB PAPER quote/readiness source; output in new,
  owner-private sandbox; original monitor output path never changed.
- 25% CPU, 384 MiB memory, NoNewPrivileges, AF_UNIX-only socket,
  `Restart=no`, 15-second start-to-start schedule, exactly 24
  status publications; no unattended restart.
- It exited naturally with status 0 after 5m52.824s wall time,
  consuming 22.850s CPU; systemd recorded **128.5 MiB peak**.
- All 24 source statuses were READY with **24/24 original probes
  USABLE**, no broker order/query or model/trade authority.
- First original cold assessment **18.314s**, warm maximum
  **4.099s**. Worst independently recomputed original status age
  **immediately before its next publication: 21.907s** (strictly below
  the original 30s freshness bound).
- 24 complete original archived source inventory SHA-256 checks;
  exactly 256 initially strict decoded archived segments;
  **5,888** warm archived decode cache hits, all rehashed before use;
  **504** unsealed/extra source strict re-parses;
  116,289 cached records, safely below 180,000 ceiling.

The independent **pocket4** replay
`research/train_v2/fx_health_24cycle_ops_audit.py`
pins both exact source program SHA-256s, the original plan bytes,
the original systemd stdout/stderr journal, last original health
status, all 24 original timestamp/age counters, every source-probe
status and original source-prefix digest. It refuses incomplete,
 forged, stale, or overwritten input. The output is O_EXCL + fsync,
owner-only `0600` under `0700` directory.

Raw Pocket4 journal SHA256:
`a2bc5f0ca656d2b3a5925bae60000dd64698514bc6812dcc823451fb60c653d5`.

Pocket4 audited output:
`~/.local/state/heptatrader/operator-evidence/fx-cache-real-ops-20261013-v1/independent-operational-audit.json`

Audited output SHA256:
`481b65c37a65b2f50b5c17452e3f6c08f865b2dd31d762c9befeedd9784ae398`.

## Physical qian-qi stdlib replay

A separate physical host qian-qi copied the four exact final
original research files (plan, journal, status, pocket4 independent
receipt) *after* the operational run and verified each hardcoded
SHA-256 without requiring the pocket4 interpreter, source parser
or IB Gateway. The standard library validator
`research/train_v2/fx_health_ops_stdlib_crosshost.py`
independently counted 24 real journals and exactly one terminal
source-cache counter, checked original as-of time vs published time,
recomputed maximum pre-next-publish age **21.907s**, 5,888 cached-hit
rehashes, all final original 24-probe statuses, original period,
original quote freshness, prefix digest, and rejection of
model/trading authority.

Physical qian-qi private original copy:
`~/.local/state/heptatrader-health-real24-20261013/`

Physical report:
`independent-physical-readback-v1.json`

SHA256:
`db3717a614f16a3be2c206038693a648bcb4f151a5f0ea9b40692b03faf116de`.

**Important trust boundary:** physical source bytes were transferred
AFTER the 24-cycle experiment, so this replay can verify exact bytes
and arithmetic but **not** independently attest the original preevent
clock, actual CPU quota, broker/exchange price signature, or native
product Root/Witness. There is no executable trade/positive alpha
proof here. This is additional operational integrity evidence.

## Robustness and negative controls

Prior research had already completed an isolated 32-segment
archive page rollover, two writer-checkpoint/power-loss recoveries,
cold cache restart, same-size corruption and uncheckpointed
archive rejection. This new actual-source run independently
checks 24 cycles under the original host workload.

New attack tests refuse: substituted original precommit after
the start, missing cycle, forged publishing age, changed PAPER
period, fewer than 24 source probes, source-prefix digest
tampering, forged READY text or quote ages, cached SHA verification
omission, new broker/order claims, counterfeit production release,
duplicate/nonfinite JSON, public input files, and overwritten
output receipts. Tests cannot be treated as hardware-attested proof.

The original four-window study still awaits the **predeclared
12:05 local final 120-minute outcome** of its 10:05 decision;
because the 10:05 preaction was censored, it cannot be relabeled
by a later cached status, even though later prices exist.

## Release decision / next gate

**Research cache candidate: PASS for this 24-cycle freshness and
local source-semantic equivalence. Product monitor cutover: HOLD.**

Before cutover, complete and independently replay all 16 original
frozen price-maturity attempts; perform multiple additional
sustained/cold/error-source/recovery cycles at real 25% quota with
explicit old-service rollback receipt, verify all source identity
and trading/PnL services after any controlled restart, and keep
signed native Owner/LedgerWriter/Witness, proper historical
effectiveness, true behavior propensity and IB Flex/account NAV
as separate non-waivable gates.

Absolutely no change to current FX/BTC PAPER entry/exits or
existing `heptatrader-learning-source-health.service` during this
research run.
