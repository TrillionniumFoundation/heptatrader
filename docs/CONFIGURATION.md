# Runtime configuration

Status: current target contract; implementation state is tracked in `development/PLAN.md`
Applies to: `scripts/resolve_hepta_config.py`, `systemd/*.env.example`, runtime config parsers
Verification: same-revision CI

## Supported profiles

Only two profiles are accepted:

```text
sim
paper
```

`live` is unsupported and must be rejected by CLI, environment and XML/config parsing. A broker account string never selects a profile. PAPER requires an explicit reviewed configuration and separate authorization credential.

## Single source rule

Configuration source precedence is explicit:

```text
--config
HEPTA_CONFIG_PATH
HEPTA_TRADER_CONFIG_PATH   (deprecated compatibility alias)
```

If multiple sources are present they must resolve to the same file. Relative paths resolve from `--project-root`; no user workspace, build directory or legacy `Tools/` scan is allowed.

## Profile lock

Resolution order:

1. `--profile`;
2. `HEPTA_PROFILE`;
3. explicit profile in the selected config;
4. otherwise `sim`.

Any disagreement fails closed. Broker mode/account fields do not infer `paper` or `live`.

## PAPER restrictions

For `paper`:

- config path is explicit and non-template;
- account, host, port, client ID and hard limits are validated by the PAPER profile;
- broker credential and activation material are injected by deployment authority, not stored in repository config;
- missing or conflicting authorization, quote, state or kill-switch inputs prevent risk increase.

## Fingerprint

The resolver emits canonical path, profile, SHA-256 and source provenance. A fingerprint identifies input bytes; it does not grant mutation capability.

## Examples

Active examples are purpose-specific:

```text
systemd/hepta-execution-simulator.env.example
systemd/hepta-tool-gateway.env.example
systemd/hepta-agent-trust-domain.json.example
```

IB PAPER examples are installed only by an explicit PAPER component. Historical CTP/XT/Windows examples belong under `legacy/` and are never part of the minimal runtime install.
