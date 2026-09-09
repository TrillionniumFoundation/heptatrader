# HeptaTrader security hardening

Status: CURRENT

## Authority and secret boundaries

Execution is the sole order authority. Agent and Gateway identities do not receive Broker credentials or protected Broker-port access. Risk-increasing mutations require authoritative state and quote data, deterministic risk, journal-before-send, stable command identity, venue correlation, and reconciliation.

Security-sensitive file readers reject symlinks, unsafe hard links, wrong owner/mode/type/size, metadata changes during read, and non-canonical paths. Uncertainty fails closed. Durable state and release outputs use synchronized, atomic publication.

## Owner-operated repository

Repository administration is not an authorization domain for this self-use system. Teams, CODEOWNERS, mandatory review counts, branch rulesets, Merge Queue, protected governance environments, and governance receipts are not required.

IB PAPER qualification instead binds the exact current `main` SHA, immutable SDK/BID and builder inputs, a separately pinned external harness, PAPER-only Broker access, the required fault/recovery campaign, the kill switch, and authoritative terminal reconciliation. Missing runtime inputs or a missing verifier receipt never becomes an implicit pass.

CTP and XT/QMT have no real transport. LIVE remains unavailable.
