# Proposed XT/QMT mapping

Status: PROPOSAL  
Applies to: future reviewed XT transport

A prospective XT transport is expected to map connection/account subscription, asset, positions, orders, trades, quotes, order errors, cancel errors, and asynchronous responses into the common Execution venue contract. The mapping must be pinned to a supported SDK version and verified against retained fixtures; a developer-local installation is not a portable contract.

Conceptual operations are:

- start/connect/subscribe account -> connection and authoritative refresh lifecycle;
- order/cancel -> Execution-owned durable command and venue correlation;
- asset/position/order/trade query -> generation-bound authoritative barriers;
- callbacks -> normalized ordered events with stable identity and source sequence.

Numeric constants and API names from an unpinned local SDK are deliberately omitted. They belong in a version-specific adapter contract and test corpus when the SDK redistribution and support boundary is known. Until then the current adapter remains fail-closed. See [`modules/xt-adapter.md`](modules/xt-adapter.md).
