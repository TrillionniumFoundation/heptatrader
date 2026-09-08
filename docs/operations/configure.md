# Configuration

Status: CURRENT  
Applies to: simulator, Agent/Gateway, and IB PAPER candidate

## Principles

- One canonical configuration source per process.
- Templates contain no real secret.
- Secrets are delivered separately from non-secret environment/configuration.
- Process identity, socket path, execution domain, account, and capabilities must agree.
- Unknown or conflicting sources fail fast.

## Simulator

Configure a dedicated state directory, execution and event sockets, deterministic fixture inputs, and a separate simulator service identity. Simulator identities must not receive IB credentials or broker-port egress.

## Agent and Gateway

Each Agent trust domain needs a unique OS identity, session token file, Gateway socket, supervisor socket, allowed capabilities, instruments/contracts, execution domain, TTL, order quantity, and call-rate limits. Token contents must be private and must not appear in environment files or logs.

## IB PAPER

The non-secret profile binds the exact `DU` account, loopback host, allowed port, client ID, maximum quantity/notional/rate/active orders/gross position, quote contracts, primary instrument, quote age, Gateway identity, I/O bounds, and state/control directories.

The authorization credential is generated from and must match the reviewed profile. Broker login material is a separate protected credential. The root/operator-owned kill-switch directory is not writable by Execution, Gateway, or Agent.

## Validation

Before startup validate:

1. no conflicting configuration source;
2. absolute canonical paths;
3. expected owners, groups, modes, link counts, and non-symlink files;
4. distinct identities and sockets;
5. network deny/allow rules;
6. profile and credential digest agreement;
7. state/journal schema compatibility;
8. capability matrix still declares LIVE unavailable.
