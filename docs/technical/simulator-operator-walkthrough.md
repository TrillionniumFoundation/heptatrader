# Simulator operator walkthrough and recovery decisions

Status: CURRENT
Applies to: single-domain local simulator; not IB PAPER or LIVE
Implementation: `systemd/`, `HeptaTrade/cli/`, `HeptaTrade/tool_host/`, `HeptaTrade/execution/`
Tests: `tests/systemd_simulator_smoke.py`, `tests/python/test_installed_runtime_processes.py`

## Defaults and effective configuration

Use the artifact's installed examples under
`/usr/share/heptatrader/examples/systemd`, not an arbitrary historical XML.
These are the ordinary single-domain defaults, not universal multi-domain UIDs:

| Input | Default / role |
|---|---|
| `hepta-gateway`, `hepta-exec`, `hepta-agent` | logical UID/GID 2001, 2002, 2004; verify availability before provisioning |
| `HEPTA_EXECUTION_REMOTE_MODE` | `SIMULATOR`; never relabel an IB session |
| `HEPTA_TOOL_ALLOW_TRADE` / `HEPTA_TOOL_SESSION_TEMPLATES` | `0` / `watch`; read-only until deliberately configured for a bounded simulator test |
| Tool / supervisor socket | `/run/hepta-agent/tools.sock` / `/run/hepta-tool-gateway/session-supervisor.sock` |
| Execution / event socket | `/run/hepta-execution/execution.sock` / `/run/hepta-execution/events.sock` |
| Lease store / OMS journal | `/var/lib/hepta-tool-gateway/session-leases.hsl2` / `/var/lib/hepta-execution/oms-journal.jsonl` |
| Execution I/O / request bound | 2,500 ms / 16,384 bytes in the installed simulator example |
| Tool workers / pending / per-owner concurrency | 4 / 32 / 1 in the installed Gateway example |

Actual UIDs must agree across unit users, `HEPTA_EXECUTION_GATEWAY_UID`,
`HEPTA_EXECUTION_SERVICE_UID` and `HEPTA_TOOL_AGENT_UID`. Do not silently reuse an
occupied UID. The operator controls `/etc/heptatrader`, mode-0644 non-secret env
files, and private credential inputs. The Gateway lease key is delivered as
`hepta-supervisor-lease-key`; Execution receives an `HFC1` fence credential with
positive `fencing_token` and `generation`. Do not put either key in an env file.

The exact host lifecycle is executable in the [disposable manager test](systemd-simulator-acceptance.md).
That fixture generates throwaway identities/secrets; it is not an installer for
an existing trading host. For mutually untrusted Agents use the reviewed
trust-domain templates rather than sharing this single-domain token or UID.

## Start and inspect an already provisioned host

After artifact/static-host preflight and protected host configuration:

```sh
sudo systemd-tmpfiles --create /usr/lib/tmpfiles.d/heptatrader-agent-os.conf
sudo systemd-analyze verify \
  /usr/lib/systemd/system/hepta-execution-simulator.service \
  /usr/lib/systemd/system/hepta-tool-gateway.service
sudo systemctl start hepta-execution-simulator.service hepta-tool-gateway.service
sudo systemctl show hepta-execution-simulator.service hepta-tool-gateway.service \
  -p ActiveState -p SubState -p MainPID -p Result
sudo journalctl -u hepta-execution-simulator.service -u hepta-tool-gateway.service --since '-5 min'
```

`active` alone is not readiness. Require the execution readiness message, a
non-degraded Gateway, then authoritative read calls. Session Supervisor is part
of the Gateway process; there is no separate supervisor daemon to invent.

The operator provisions the read-only session with installed `hepta-sessionctl`:

```sh
sudo hepta-sessionctl --socket /run/hepta-tool-gateway/session-supervisor.sock \
  provision --template watch --token-file /protected/operator-session-token \
  --agent-id codex-agent-os-e2e --session-id simulator-watch-1 \
  --peer-uid "$(id -u hepta-agent)" --ttl-sec 3600
```

The token file must already be private and securely delivered to the Agent's
own mode-0600 file. The paths above are placeholders, not created credentials.
Run the following **as the Agent UID**, with its actual private token path:

```sh
heptactl --socket /run/hepta-agent/tools.sock --token-file "$TOKEN_FILE" \
  --call-id discovery-1 call system.tools.list
heptactl --socket /run/hepta-agent/tools.sock --token-file "$TOKEN_FILE" \
  --call-id quote-1 call market.get_quote instrument=EUR.USD
heptactl --socket /run/hepta-agent/tools.sock --token-file "$TOKEN_FILE" \
  --call-id positions-1 call portfolio.list_positions
```

Discovery supplies the current tool schemas; the [wire contract](agent-tool-protocol.md)
explains framing and hashing. A read-only `watch` session cannot submit orders.

## Bounded mutation contract

The executable manager/process fixtures are the complete runnable examples for
mutations. Their `paper` template name is historical; their venue and account
are strictly `SIMULATOR`/`SIM`, never a Broker. They explicitly set simulator
quantity/rate limits and do not change shipped read-only defaults.

`risk.preview_order` receives instrument, full contract identity, side, order
type, quantity, price, TIF and expiry. An approved response includes
`command_id`, `preview_permit` and `single_use=true`. `trade.place_order` must
reuse the exact fields and expiry, put the returned ID in the call identity and
include that permit. Persist this normalized request before sending; do not
recompute expiry or generate another ID after a missing response. Query
`execution.get_command_status` with `command_id=<original-id>` while recovery
is needed. An accepted RPC is not a fill: poll authoritative positions and
active orders without reissuing a mutation.

## Failure to action mapping

| Observation | Required action / evidence |
|---|---|
| unit `203/EXEC` or missing executable | stop startup; compare `ExecStart` with manifest; reinstall the approved artifact, not loose binaries or ad-hoc symlinks |
| `EXECUTION_SYSTEMD_SOCKET_ACTIVATION_INVALID` | inspect required socket units and descriptor names; start through the configured manager, not a naked daemon invocation |
| `EXECUTION_GATEWAY_UID_NOT_ISOLATED` / peer permission denial | compare actual OS UIDs and effective configuration; never disable peer checks |
| quote unavailable/stale or incomplete snapshot | keep reads/recovery only until fresh authoritative generation/barrier exists |
| typed `duplicate` | inspect the returned original order/command; do not send again under a fresh ID |
| typed `uncertain`, response loss, possible send | preserve request/journal; query the same command and reconcile; do not infer safe rejection |
| journal or lease corruption / incompatible schema | stop admission; preserve files; use tested migration or forward fix, never erase state to get a green start |
| nonzero service result on shutdown | preserve journal and manager logs; do not treat stop as terminal/flat proof |

See [rollback and restore](../operations/rollback-backup.md) and the
[lease format/migration contract](session-lease-format.md). After final verified
flatness and applicable session cleanup, stop Gateway before Execution and stop
their sockets when shutting down the installation. Broker shutdown has additional
kill-switch and authoritative reconciliation requirements, which this simulator
walkthrough does not replace.
