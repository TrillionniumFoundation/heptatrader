# Tool protocol contract and generated field bindings

Status: CURRENT  
Applies to: repository HEAD  
Implementation: `protocol/hepta_tool_fields.def`, `scripts/generate_tool_protocol_fields.py`, `adapters/mcp/hepta_tool_protocol_fields.py`, `HeptaTrade/tool_host/typed_tool_protocol.cpp`, `HeptaTrade/tool_host/typed_tool_protocol.h`  
Tests: `tests/python/test_tool_protocol_field_source.py`, `tests/native_tool_client_tests.cpp`, `tests/unix_tool_server_tests.cpp`, `tests/python/test_protocol_fuzz_properties.py`

## Responsibilities

This module owns the stable field identity shared by the Python MCP bridge, native C++ client and Tool Gateway wire codec. It prevents the two language implementations from assigning different numeric meanings to the same field and provides a reproducible generator for the Python binding. It does not authorize a tool call or define broker behavior; session, capability and execution policy remain separate modules.

## Public protocol contract

Protocol version 1 uses a bounded `HTT1` field stream inside a length-prefixed Unix-socket request. `protocol/hepta_tool_fields.def` is the single source for the Python field name, C++ enumerator and numeric field ID. IDs 1 through 26 are unique and contiguous. Removing, renumbering or reusing an existing ID is incompatible; a new optional field requires a new unused ID and cross-language compatibility vectors.

The C++ field enum includes the definition directly with an X-macro. `scripts/generate_tool_protocol_fields.py` parses the same file and produces `adapters/mcp/hepta_tool_protocol_fields.py`. The generated file is committed so the standalone Python entry point has no build-time dependency, while `--check` proves that committed bytes exactly match the source definition.

Field identity is only one part of the protocol. Tool descriptor schemas, catalog hashes, command-ID rules, framing bounds, response envelopes and selected protocol version continue to be validated by the typed protocol and Agent entry modules. Human-facing description text is metadata and must not be treated as execution authority.

## State and compatibility

The field definition is immutable within protocol version 1. Generated Python state contains only the forward and reverse maps; it has no runtime mutation or persistence. A process may cache the discovered tool catalog for its lifetime, but the wire field mapping is fixed by the installed source artifact.

Compatibility is additive: an older peer may reject an unknown required field or unsupported protocol range, and a newer peer must not reinterpret an existing ID. Golden native-client and Unix-server tests exercise the committed C++ and Python mappings against the same server contract. Protocol changes update the definition, regenerate the binding, update descriptor schemas and add old/new vectors in one revision.

## Security and trust boundary

Generated constants do not validate values. The receiver still rejects duplicate fields, forbidden fields, oversized values, malformed numbers, unsupported versions and fields that are not permitted by the selected tool schema. A caller cannot bypass authorization by emitting a known numeric ID with a different semantic name. Unknown IDs and schema-hash mismatches fail closed before forwarding.

The generator accepts only canonical macro lines, unique names and the exact contiguous v1 ID set. It does not execute the definition as code. Installation places both the MCP entry point and its generated binding together, preventing an unreviewed ambient Python module from silently supplying field identities.

## Failure semantics

A malformed definition, duplicate name or ID, missing v1 ID, stale generated Python binding, missing C++ consumer, second competing C++ definition or import failure makes validation fail. Runtime framing or envelope errors remain typed protocol failures and never manufacture a successful tool result. A lost response to a mutation still follows command-status/idempotent retry rules; field generation does not change mutation identity.

## Observability

Source CI records generator success and protocol compatibility test results for the exact SHA. Runtime errors distinguish framing, field, schema, protocol-version, catalog-hash and response-envelope failures. Logs may include field names and reason codes but must not include session-token contents.

## Test expectations

`test_tool_protocol_field_source.py` checks deterministic generation, exact IDs and a single C++ consumer. Native-client and Unix-server suites cover valid requests and cross-language behavior. `test_protocol_fuzz_properties.py` exercises thousands of random response bodies, hostile envelope mutations, unknown or non-scalar request fields, unique encoded IDs and field-size boundaries. Any new field must add positive, duplicate, unknown-peer and malformed-value cases.

## Known limitations

The current protocol is local-host, Unix-stream and version 1 only. The single field source does not yet generate complete request/result structs or Markdown schema tables, and descriptor-schema construction still lives in the tool registry. Remote TLS, negotiated multi-version codecs and generated full IDL bindings require a separately reviewed protocol version and trust domain.
