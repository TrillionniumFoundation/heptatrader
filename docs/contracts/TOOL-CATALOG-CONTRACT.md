# Tool Catalog and Discovery Contract V1

Status: current core contract
Applies to: `hepta.tool-catalog.v1`, `system.tools.list`, `system.tools.describe`, Native and MCP discovery
Verification: `tool-catalog-drift`, `native-client-contracts`, `mcp-contracts`, `gateway-boundary`
Authority: versioned descriptor/catalog truth for the current server-side session; catalog membership does not grant Execution authority

## 1. Canonical source and projection

The machine source is [`tool-catalog-v1.json`](../../schemas/tool-catalog-v1.json). It owns protocol name/version, stable wire field IDs, registered tool names, capability labels, visibility classes, aliases, numeric policy and target-intent tool membership. Generated C++ and Python catalog blocks must be byte-equivalent projections of this source.

Runtime descriptors are built by [`TradingToolRegistry`](../../HeptaTrade/tools/trading_tool_registry.cpp). They add descriptions, effect, timeout, input schema and result schema, then filter the catalog through the current server-side session. The client cannot add a tool or widen its visibility by supplying a catalog file.

## 2. Descriptor model

Every visible descriptor contains exactly the discovery fields:

| Field | Contract |
|---|---|
| `name` | Canonical registered tool name. |
| `description` | Bounded human-readable purpose; non-authoritative. |
| `required_capability` | Capability the server checks against the Session binding. |
| `effect` | `read` or `trade`. A read descriptor cannot mutate Broker/Execution state. |
| `timeout_ms` | Server-advertised bounded operation timeout. It does not guarantee completion or Broker truth. |
| `schema_hash` | SHA-256 digest of the canonical descriptor semantics. |
| `input_schema` | Closed-world JSON Schema-like object used by client adapters and server semantics. |
| `result_schema` | Declared payload/result shape. The outer result envelope remains governed by the execution wire contract. |

The descriptor hash binds name, capability, effect, timeout and canonical schemas. A list or describe response with an invalid digest, duplicate field, duplicate name or digest/content mismatch is rejected.

## 3. Discovery operations

`system.tools.list` returns:

- `protocol = "hepta.agent-tools"`;
- selected protocol version and supported minimum/maximum;
- discovery `schema_version = 2`;
- `catalog_schema_hash` over the ordered visible descriptor set;
- non-empty `tools` array of complete descriptors.

`system.tools.describe` requires one `tool_name` argument and returns the complete descriptor for that visible tool plus the same protocol/schema/catalog identity. A describe response is valid only after a list response on the same client session and only when its descriptor digest equals the list snapshot.

The catalog hash is session-relative. Different capabilities, environment, bound instruments or optional runtime composition may produce a different visible set. A change during one client session fails closed and requires a fresh Session/discovery boundary rather than silently replacing the cached catalog.

## 4. Current registered tools

The machine registry contains the following classes; runtime publication remains session- and composition-dependent:

- discovery/control: `system.tools.list`, `system.tools.describe`, `system.cancel_request`, `system.get_health`;
- market/account/state reads: `market.get_quote`, `account.get_summary`, `portfolio.list_positions`, `orders.list`, `execution.get_command_status`, `risk.get_limits`, `decision.get_snapshot`, `events.wait`, `watch.get_snapshot`;
- target-position flow: `intent.preview_target_position`, `intent.apply_target_position`;
- operator order flow: `risk.preview_order`, `trade.place_order`, `trade.cancel_order`;
- conditional safe-exit flow: `risk.preview_flatten`, `trade.flatten_position`.

`risk.preview_flatten` and `trade.flatten_position` are published only when concrete authoritative read and reduce-only handlers are installed. Operator tools are not ordinary Agent capabilities. WATCH sessions cannot trade; LIVE-family environments are unsupported by this catalog and fail closed.

## 5. Capability and environment semantics

A descriptor's capability string is a server-side requirement, not a client assertion. The server obtains capabilities, environment, owner/account context, instrument visibility, quantity limits and lease state from the bound Session.

Visibility labels mean:

- `ordinary`: may be published to an eligible ordinary session;
- `operator`: requires an independently provisioned operator capability/profile;
- `watch`: only meaningful in the WATCH environment;
- `conditional`: published only when the required authoritative runtime composition exists.

Passing discovery does not prove downstream readiness, current market data, risk completeness, Execution availability, Broker connection or PAPER qualification. Every call is revalidated at invocation.

## 6. Input schemas and aliases

Input schemas are closed-world: unknown properties are rejected where `additionalProperties=false`, and required fields must be present. Direct native encoding accepts only the wire fields registered for that exact tool.

The registered aliases are compatibility projections, not independent fields:

| Public alias | Canonical wire field |
|---|---|
| `mutation_command_id` | `tool_call_id` |
| `target_position` | `quantity` |
| `max_slippage_bps` | `reference_price` |

For target-position tools, aliases are normalized before schema/wire encoding. Supplying both spellings so that they collapse onto one wire field is rejected. Alias values inherit the canonical field's fixed-point, range, expiry and identity semantics.

Trade descriptors exposed through MCP additionally require a stable public `command_id`. The adapter uses it as the native tool call/mutation identity and does not include it twice in the descriptor's domain arguments.

## 7. Catalog change process

Adding or changing a tool requires one atomic change across:

1. the machine catalog;
2. generated C++ and Python field/tool projections;
3. runtime descriptor and handler composition;
4. module manifests and contract provider/consumer declarations where affected;
5. reason codes, capability registry and operation documentation;
6. C++/Python discovery, schema-hash and negative tests.

A tool is not current merely because its name appears in the JSON registry. It must have a runtime descriptor, a bounded handler or explicit conditional publication, direct tests and an implementation-evidence scope that permits it. Removal, capability/effect change, field-ID reassignment, schema reinterpretation or replay-semantics change is a major compatibility event.

## 8. Verification

Required hostile cases include catalog/descriptor digest substitution, duplicate tool names, duplicate JSON fields, list/describe disagreement, version mismatch, unknown properties, alias collision, unauthorized capability, WATCH mutation, absent conditional handler and catalog drift during a client session.

The generated catalog checker must compare the machine source with both C++ and MCP projections. Native/MCP tests must validate the same descriptor and result against the same canonical digest. No manually authored catalog response or stale cached catalog may satisfy current discovery.
