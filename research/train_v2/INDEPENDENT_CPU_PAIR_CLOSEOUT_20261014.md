# Train V2: two genuine future CPU price forecasts, physically independent closeout

2026-10-14, isolated research evidence only. This work starts at pinned
3f2e879 (the independent 24/24 original Source Health replay). It does
not change the PAPER broker, incumbent strategy, OMS, model candidate,
Laya Guard, past trial time slots, original market data or trade authority.

## Actual new prospective source decisions

Two distinct, originally preregistered CPU Native Numeric price predictions
were completed naturally after pocket4's original quote source recovered:

| Slot | Price prediction (bps) | Original 5-minute MID change (bps) | Direction | Over predeclared hypothetical cost |
| -- | --: | --: | -- | -- |
| 01 | +0.33517021 | -0.9830161616793490645707570000 | WRONG | NO |
| 02 | +0.35044288 | +0.491532240046472139058940000 | RIGHT | NO |

Both original input/result price horizons and prediction seal deadlines
were independently replayed from the original PAPER hash chain. The
frozen numeric candidate and source/decision/witness IDs were not
rewritten or retrospectively trained. These are model PRICE scores,
not original LLM BUY/SELL decisions or executable market orders.

On Pocket4, the ORIGINAL native outcome verifier produced immutable
create-only receipts:

- slot-01-outcome-new-20261014.json,
  SHA256 59af1892d7445f1f002e0769791718f24d3817295e5ecf680dd319a21f2c03da
- slot-02-outcome-new-20261014.json,
  SHA256 304a1c43acbe5eba857afde255804b6dddf553c6057bf0244c57154cd00eae2c
- two-new-ticks-price-cohort-closeout-20261014.json,
  SHA256 69a9e24fe27bf111ef014dcb7cdc265e38156271628212bbfa2692f6805680cc

Private evidence directory:
~/.local/state/heptatrader/operator-evidence/train-v2-cpu-original-20261014

The original cohort closeout says TWO_SEALED_PRICE_OUTCOMES_ONLY_NO_ALPHA:
two observed, zero waiting/censored, one correct sign, one incorrect sign,
no price move above the **prior frozen hypothetical** 10.9133557-bps
roundtrip cost + safety margin, and zero independent executed trade PnL.

## Separate physical qian-qi verification

The two market windows and their resulting endpoints are all inside ONE
naturally sealed original numeric source segment:
segment-1791909391614340951-4543ba44/quotes.jsonl.

Without accessing the original broker, qian-qi read a copy of exactly that
sealed segment plus two original plan/input/prediction/commit/outcome files.
The original segment copied from pocket4 has SHA256
95c556ed0b3357fe5323453bb92c22c02ded69a394d8d02ee6508e04457483ec.

The independent stdlib verifier:
- checks all 1,800 original quote entries and the HEADER->...->END
  cryptographic hash chain, quote time/expiry, source scope and prices;
- checks each precommitted model/plan/input/prediction digest, source
  receipt identity, original 5-minute due and completed sealed deadline;
- selects the first strictly original event-time endpoint within 5 seconds,
  confirms the intervening source price path has no >30-second hole,
  and recomputes both Decimal MID returns WITHOUT importing the Pocket4
  Laya/Train V2 outcome evaluator;
- cross-checks original result audit_ids and the complete two-window
  closeout, including one right/one wrong and 0/2 exceeding the pre-fixed
  hypothetical cost. Missing/rehashed/late sources fail closed.

First independently issued qian-qi receipt (kept immutable):
qian-qi-independent-price-review-v1.json,
SHA256 de97835fdb04aaf3f49a8eb47ba22077fec9c6cff726bfee2b273334fb7e6c92.

Final source-code-identified receipt (kept immutable):
qian-qi-independent-price-review-final-v2.json,
SHA256 249020d9113b7c16b6f5041db6646af4a905ba68de7faba776c482a453ae9ab5.

Original independent parser source SHA256:
7b907230d378239a09dbffd9ca10b7c99519f9789d1bd4cca01868dad8fb64a8.

qian-qi private workspace:
~/work/heptatrader-cpu-pair-independent-20261014/

Ten real-copied-evidence mutation tests passed on qian-qi, including:
deleted END, rehashed altered source, forged price/outcome, false broker
fills, wrong due time, changed fixed ticks, replayed quote receipt and
self-rehashed fake cohort accuracy. Fourteen additional portable
synthetic source-chain adversarial tests pass in normal HeptaTrader
Core Python test discovery.

## Limits and next real admission barriers

qian-qi independently verified the **one complete original segment
containing both references/endpoints**. It did NOT copy/check all 305
segments in the larger frozen Pocket4 source-archive fingerprint.
It also did NOT independently execute the original CPU Numeric model,
activate an Agentd signer, or attest a broker-signed quote. The two
forecasts were previously frozen; the historical result verification
is not a new prospectively registered population.

Full strategy net PnL, true trading behavior propensities, executable
spread/slippage, real fill/execution reward, independent Canonical
Observer/LedgerWriter acknowledgement, current Artifact Owner selection
and positive independent net alpha remain unqualified. The original
source HEALTH READY (24/24) and production Laya service 'active' do
not replace independent GPU-owner/lease evidence, full account PnL or
these product selection requirements. No GPU or model was launched
by this closeout.

No order, production model promotion, training task, service restart,
broker request, original raw segment mutation or retroactive source
repair occurred during this price outcome closeout.


## Final isolation and complete Pocket4 source regression

- qian-qi original copied private bytes: 10/10 physical-fixture mutation tests
  pass. Unaltered standalone stdlib script SHA:
  7b907230d378239a09dbffd9ca10b7c99519f9789d1bd4cca01868dad8fb64a8.
- Pocket4 synthetic portable independent source-chain tests: 14/14 pass.
- All historical training regressions: 539 pass; 7 pre-existing skips.
- Core Python complete discovery: 2,774 pass; 23 pre-existing skips.
- Source/build/ownership lane: 106 pass, none skipped.
- Original source file and two already frozen prediction/outcome receipts are
  unchanged; the independent verification does not start a trader/LLM/GPU,
  issue any new IB request, or alter model selection.

Passing code tests and a coherent price result do not establish a
broker-authenticated market feed, actual strategy PnL, or statistically
independent financial improvement.
