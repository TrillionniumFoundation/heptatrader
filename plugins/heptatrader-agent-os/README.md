# HeptaTrader Agent OS plugin

Status: current
Applies to: `plugins/heptatrader-agent-os/`, installed MCP launcher metadata
Verification: same-revision CI

This plugin exposes the local HeptaTrader Tool Gateway as MCP tools. It contains no broker credential, account secret, PAPER/LIVE grant or local execution authority.

## Installed use

The runtime component installs `hepta-agent-mcp-launcher` into the configured bindir and the MCP bridge into the matching libexec tree. `.mcp.json` invokes the launcher by command name so `/usr` and `/usr/local` installations use the same plugin metadata.

```bash
cmake --preset core-release -DCMAKE_INSTALL_PREFIX=/usr
cmake --build --preset core-release --target hepta_runtime_binaries
sudo cmake --install build/core-release --component runtime
```

The launcher:

- requires a fixed Agent UID/GID and no supplementary groups;
- reads one root-reviewed trust-domain configuration;
- supplies only the session socket, token path and expected UID;
- verifies the installed MCP server is a root-owned, single-link `0755` file;
- never receives broker credentials.

Each mutually untrusted Agent must use an independent OS identity, socket, session token and capability set. The Agent may call only tools visible to its current session and must never infer PAPER or LIVE authority.

## Source development

For local protocol development without installing the plugin:

```bash
cmake --preset core-release
cmake --build --preset core-release --target hepta_tool_gatewayd hepta_sessionctl
python3 adapters/mcp/hepta_mcp_server.py
```

Source execution requires the reviewed `HEPTA_TOOL_SOCKET` and `HEPTA_TOOL_SESSION_TOKEN_FILE` environment. Final order authorization, risk, idempotency, reconciliation and kill switch remain deterministic Gateway/Execution responsibilities.
