# Configuration and profile lock

Canonical Agent OS services do not auto-discover arbitrary configs from developer directories. systemd supplies fixed environment files, StateDirectory/CredentialsDirectory and activated sockets; runtime parsers validate every value and fail closed on unknown modes, unsafe paths, wrong UID, wrong account/venue binding or invalid limits.

## Core Simulator

`hepta-execution-simulator.service` fixes `HEPTA_EXECUTION_SERVICE_MODE=SIMULATOR`. The env file may configure the allowed Gateway UID/Agent ID and bounded request/timeout values. Socket descriptors, state directory and credential directory come from systemd and must not be supplied by a user shell.

## IB PAPER

`hepta-execution-ib-paper.service` is the only canonical Broker-owning profile. It requires loopback PAPER endpoint, dedicated execution identity, explicit credentials, fixed control directory, risk limits and authoritative account binding. Config values that imply LIVE, public Broker endpoints, missing credentials or unsafe directories are invalid.

## Legacy resolver

`scripts/resolve_hepta_config.py` remains for legacy/research configuration parsing. It is not the source of truth for canonical systemd services and cannot turn a legacy XML profile into Broker authorization. New runtime features must use typed runtime config structs and explicit validation rather than candidate-path discovery.

## Change control

- Checked-in files use `.example` suffix and contain no secrets.
- Deployment creates the actual `/etc/heptatrader/*.env` files with root ownership and non-secret values only.
- Every release records config schema/version separately from runtime secrets.
- Profile mismatch, duplicate sources or unknown values are fatal; no fallback from PAPER to Simulator or from template to production config.
