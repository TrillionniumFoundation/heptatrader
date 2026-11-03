# Repeated successful observation audit records

The Gateway previously synchronized one audit record for every ordinary read.
Repeated identical reads now retain one durable sample per five seconds for an
authenticated owner and exact request argument scope. This applies only to successful
`system.get_health`, `market.get_quote`, `orders.list`, and
`portfolio.list_positions`, and `system.tools.list` outcomes. Other tool calls and all mutation intents,
mutation outcomes, denied outcomes, recovery and safety records retain their
existing synchronous durability behavior.

The scope includes the peer, daemon, execution domain, owner, account, venue,
environment, schema, tool and argument fingerprint. Only the
per-call identifier and queue deadline are omitted from the argument grouping.
Every retained record keeps its full original request fingerprint and records
`observation_sampling_window_ms=5000` and a hash of the complete response.
That hash includes status, tool name, reason, detail, order id and payload bytes.
A changed response immediately receives a new durable record, including A to B
to A transitions within a window. Each request scope retains only its latest
durable response; identical reads do not extend the window. A subsequent matching read returns
`SUPERVISOR_AUDIT_OBSERVATION_COALESCED`; it is not a per-request durable receipt.
Gateway metrics expose `audit_log.observations_coalesced`, and the metrics
exporter publishes `hepta_gateway_audit_observations_coalesced_total` when the
field is available. These counters are scoped to the existing service epoch.

Every call still checks the pinned journal inode, permissions, metadata and
change notifications. Changed or unavailable watches require full chain
verification before a read can coalesce. A failed write, a peer journal write,
or a critical/denied audit record clears existing windows. The map is limited to 128
scopes; a full map makes a new scope use the ordinary durable append. There is
no deferred critical write or background flush dependency.

The native lifecycle test compares 20 identical successful reads: the ordinary
path performs 20 `fdatasync` calls and grouping performs one. It also checks
window expiry, changed owner/arguments/responses, mutation boundaries, write failure and
same-metadata tampering. A second native test uses the real Gateway record
builder, registry status mapping and journal to check that instruments, cursors,
schemas, owners, disabled bindings, failed authorization, unknown tools and
mutations cannot share sampling privileges. These isolated fixtures do not
connect to Execution or a broker. Production cost reduction still needs to be
measured against the actual request mix after a normal Gateway handoff.
