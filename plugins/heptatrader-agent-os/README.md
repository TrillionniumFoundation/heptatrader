# HeptaTrader Agent OS plugin

This Codex-compatible bundle is also installable by OpenClaw. It launches the
long-lived `hepta-mcp-server` stdio bridge, which talks directly to the local
HeptaTrader Unix Tool Gateway without spawning `heptactl` for each call.

Install the `hepta-agent-os-runtime` component under `/usr` first. A root
operator then provisions the WATCH-only Agent session with
`/usr/libexec/hepta-agent-session-bootstrap --domain-config
/etc/heptatrader/trust-domains/<domain>.json provision-watch`; direct
`hepta-sessionctl` use is an internal supervisor interface, not the normal
Agent bootstrap. The plugin contains no token, broker credential, account
secret, PAPER authorization, or LIVE authorization.
The Codex/OpenClaw host process that launches this stdio MCP bridge must run as
the unprivileged identity assigned to exactly one trust domain. The installed
MCP configuration uses `/usr/libexec/hepta-agent-mcp-launcher`, a non-setuid
launcher which loads the root-owned
`/etc/heptatrader/trust-domains/uid-<effective-uid>.json` record, verifies that
its UID/GID and domain-specific socket/token paths match the caller, rejects
supplementary groups, sanitizes the child environment, and then executes the
bridge without changing identity. Missing, unsafe, or mismatched records fail
closed. The launcher must never be granted setuid, sudo, or broker credentials.

Provision a distinct OS user/group and reviewed root-owned trust-domain record
for every mutually untrusted Agent. The legacy
`hepta-agent-host-identity.conf.example` remains a single-domain compatibility
example for UID/GID 2004; it must not be shared by untrusted Agents. The host
service remains responsible for its own model/network configuration. This
separates Agent processes, Gateway identities, sockets, session tokens, and
Execution peer trust. The compatibility `hepta-gateway` group is only a
single-domain compatibility identity; templated Gateways have only their
domain-specific primary groups and no supplementary groups. Execution still
authorizes the exact configured Gateway UID and domain/account binding.

The declarative trust-domain staging command emits both
`<domain>.json` and a separately created `uid-<agent-uid>.json`; neither may
be a symlink or hard link. The root bootstrap reads `<domain>.json`, which
must be a `root:root`, mode-`0600`, single-link regular file. The Agent MCP
launcher reads `uid-<agent-uid>.json`, which must be a separate
`root:<domain-agent-group>`, mode-`0640`, single-link regular file whose
record binds the caller's exact UID/GID. Staging also emits a per-domain
`<domain>.agent-host.conf` identity fragment. Apply that fragment only to the
reviewed Codex/OpenClaw host service for the same domain. Staging does not
apply a drop-in, create credentials, start units, or authorize PAPER/LIVE.

For Codex, register the marketplace installed by the reviewed runtime component
and install the versioned plugin entry:

```sh
codex plugin marketplace add /usr/share/heptatrader
codex plugin add heptatrader-agent-os@heptatrader
```

For a source-checkout-only development review, the repository root containing
`.agents/plugins/marketplace.json` can be registered instead:

```sh
codex plugin marketplace add /path/to/HeptaTrader-master
```

`/path/to/HeptaTrader-master` must be replaced with the absolute path to the
reviewed checkout. Start a new Codex thread after installation so the MCP tool
catalog is loaded. The marketplace does not install the OS runtime, create
identities, provision a session, or grant trading authority.

Verify the installed Codex entry and plugin-contributed MCP server from the
same trust-domain host environment:

```sh
codex plugin list --json
codex mcp list --json
codex doctor --json
```

`codex mcp list --json` is the authoritative loader check: it must contain
exactly the `heptatrader` stdio server and fixed launcher. Codex 0.144.1 has a
known diagnostic gap where `doctor` reports zero base-config MCP servers even
after `codex mcp list` has loaded one plugin-contributed server. Do not mistake
that version-specific diagnostic omission for an absent server, and do not
promote `doctor` to the plugin-loader gate until a reviewed Codex release
includes plugin-contributed servers in that count.

OpenClaw can install the same bundle after the OS runtime is installed:

```sh
openclaw plugins install /usr/share/heptatrader/plugins/heptatrader-agent-os
openclaw plugins inspect heptatrader-agent-os --runtime --json
```

In the reviewed OpenClaw 2026.7.1-2 loader, `plugins install` is not a
disabled staging step: it writes the plugin entry with `enabled=true`.
Therefore run installation only after the local source and host plugin/tool
policy have been reviewed; use `openclaw plugins disable heptatrader-agent-os`
when the entry must remain inactive. Runtime inspection
must report `explicitlyEnabled=true` and `activated=true`, identify exactly one
`heptatrader` stdio MCP server, and contain no bundle diagnostics. This loader
activation still does not grant PAPER or LIVE authority. It proves loader
recognition, not UID/socket traversal, session validity, Execution liveness,
tool discovery, or a successful read.

A source checkout can rerun the intentionally opt-in real-loader gate. It uses
temporary HOME, state, and config directories and pins the reviewed OpenClaw
version, so it does not change the operator's normal OpenClaw installation:

```sh
python3 scripts/run_heptatrader_openclaw_loader_gate.py --run --require
```

The installed launcher derives the domain-specific paths from the root-owned
record; host-supplied socket/token overrides are discarded and secret values
must never appear in the plugin manifest. UID/GID 2004 and the historical
`/run/hepta-agent` paths are reachable only when an operator explicitly sets
`HEPTA_AGENT_SINGLE_DOMAIN_COMPAT=1` for one mutually trusted compatibility
domain. The packaged plugin does not set that switch.

Native OS agents that do not use MCP can link the installed
`libhepta_native_tool_client.a` and include
`heptatrader/client/native_tool_client.h`.
The shared client enforces the same framed protocol, bounded response, and
strict non-symlink `0600` session-token rules used by `heptactl`.
