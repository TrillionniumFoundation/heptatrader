# HeptaTrader security hardening

Status: CURRENT  
Applies to: canonical runtime

## Secret boundary

The repository contains templates only. Broker login material, Agent session tokens, authorization credentials, operator keys, and GitHub governance tokens are delivered by protected deployment or CI credential facilities and are never committed, logged, placed in ordinary artifacts, or passed to untrusted candidate code.

Each untrusted Agent uses a distinct OS identity, token, socket, session, and trust domain. The Tool Gateway and Agent cannot read broker credentials or connect to protected broker API ports. Only the dedicated IB PAPER Execution identity may receive those capabilities.

## Filesystem boundary

Security-sensitive readers reject symlinks, unsafe hard links, unexpected owner/mode/type/size, metadata changes during read, and non-canonical paths. Durable state uses file synchronization, atomic replacement, and directory synchronization where applicable. Uncertainty fails closed.

## Runtime boundary

- Execution is the sole order authority.
- Risk-increasing mutations are journaled before send and idempotent by command ID.
- Authoritative quote/state, owner generation, kill switch, profile credential, and venue correlation are revalidated at the final authority.
- Cancel/reduce/flatten are explicit guarded exit paths.
- CTP and XT have no transport; LIVE is unavailable.

## Qualification boundary

Source correctness, live GitHub governance, runner/environment trust, and broker-observed PAPER evidence are separate claims. Candidate source is treated as hostile data inside privileged workflows. Missing live controls or receipts never becomes an implicit pass.

See [`docs/index.md`](docs/index.md), [`docs/modules/ib-paper.md`](docs/modules/ib-paper.md), and [`docs/BROKER-NETWORK-ISOLATION.md`](docs/BROKER-NETWORK-ISOLATION.md).
