# Gap register

Status: CURRENT
Machine source: [`gap-register.json`](gap-register.json)
Verification: `python3 scripts/check_gap_register.py`

HeptaTrader is an owner-operated self-use system. Repository teams, CODEOWNERS, mandatory review counts, branch protection, Merge Queue, protected governance environments, and governance receipts are not authorization domains. The historical `G-TEAM-001` requirement is retired as **NOT_APPLICABLE**.

## Repository-controlled closure

Every repository-domain entry in `gap-register.json` must remain `CLOSED_SOURCE` and carry machine-verifiable source evidence. These checks are engineering evidence, not external authorization.

## Remaining external Broker qualification

The only external blocker is `G-IB-001`: exact-current-main, Broker-observed IB PAPER qualification. Closure requires immutable SDK/BID and builder inputs, distinct no-secret builder and PAPER execution identities, PAPER-only TWS/IB Gateway/account, a pinned external harness, the bounded Broker campaign, kill-switch and recovery evidence, authoritative terminal reconciliation, and a verifier-issued receipt.

While `G-IB-001` remains open:

- `paper_authorized` remains false;
- `live_authorized` remains false;
- IB stays `QUALIFICATION_REQUIRED`;
- LIVE stays `UNAVAILABLE`;
- simulator output, TCP reachability, manually authored JSON, or evidence from another source/artifact cannot close the Broker gap.

Direct owner commits and merges are permitted. They do not substitute for Broker qualification.
